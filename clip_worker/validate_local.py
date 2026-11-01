"""Manual macOS validation with real CLIP and loopback-only, stubbed APIs.

No production configuration is changed. Tokens are random, in-memory, and never
written to results. Photos are supplied by path; artifacts default to .cache/.
Run benchmark and integration separately to avoid overlapping model processes.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import replace
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import resource
import secrets
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
from uuid import uuid4


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    return {"exit_code": result.returncode, "output": result.stdout.strip(), "error": result.stderr.strip()}


def system_memory():
    return {"memory_pressure": command("/usr/bin/memory_pressure", "-Q"),
            "pressure_level": command("/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"),
            "swap": command("/usr/sbin/sysctl", "vm.swapusage"),
            "vm_stat": command("/usr/bin/vm_stat")}


def rss_mib(pid):
    result = command("/bin/ps", "-o", "rss=", "-p", str(pid))
    return round(int(result["output"]) / 1024, 2) if result["output"] else None


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * fraction + 0.5))]


def summary(values):
    return {"n": len(values), "median_ms": round(statistics.median(values), 3),
            "p95_ms": round(percentile(values, .95), 3), "min_ms": round(min(values), 3),
            "max_ms": round(max(values), 3)}


def image_inputs(paths):
    from PIL import Image
    from server.images import evaluation_image
    raws, compact, records = [], [], []
    for path in paths:
        raw = path.read_bytes()
        with Image.open(io.BytesIO(raw)) as image:
            size = image.size
        transported, _ = evaluation_image(raw, "image/jpeg")
        with Image.open(io.BytesIO(transported)) as image:
            transport_size = image.size
        raws.append(raw)
        compact.append(transported)
        records.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                        "original_size": size, "transport_size": transport_size,
                        "original_bytes": len(raw), "transport_bytes": len(transported)})
    return raws, compact, records


def benchmark(paths):
    from .worker import Encoder, MODEL, PIPELINE
    _, compact, records = image_inputs(paths)
    result = {"images": records, "model": MODEL, "pipeline": PIPELINE,
              "clip_source_commit": "d05afc436d78f1c48dc0dbf8e5980a9d471f35f6",
              "versions": {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "clip", "pillow", "numpy")},
              "hardware": command("/usr/sbin/sysctl", "hw.memsize", "machdep.cpu.brand_string")["output"],
              "memory": {"before_load": system_memory()}, "rss_mib": {"before_load": rss_mib(os.getpid())}}
    started = time.perf_counter()
    encoder = Encoder("auto", ".cache/clip")
    result["model_prepare_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["device"] = encoder.device
    checkpoint = Path(".cache/clip/ViT-B-32.pt")
    with checkpoint.open("rb") as stream:
        result["checkpoint_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    result["preprocess"] = str(encoder.preprocess)
    result["rss_mib"]["after_load"] = rss_mib(os.getpid())
    result["memory"]["after_load"] = system_memory()

    def run(clear=False):
        if clear:
            encoder.features.clear()
        if encoder.device == "mps":
            encoder.torch.mps.synchronize()
        start = time.perf_counter()
        score = encoder.compare(*compact)
        if encoder.device == "mps":
            encoder.torch.mps.synchronize()
        return (time.perf_counter() - start) * 1000, score

    elapsed, score = run()
    result["first_pair"] = {"elapsed_ms": round(elapsed, 3), "cosine": score}
    result["rss_mib"]["after_first"] = rss_mib(os.getpid())
    result["memory"]["after_first"] = system_memory()
    result["repeat_no_embedding_cache"] = summary([run(clear=True)[0] for _ in range(20)])
    result["rss_mib"]["after_repeat"] = rss_mib(os.getpid())
    result["both_embeddings_cached"] = summary([run()[0] for _ in range(20)])
    result["rss_mib"]["after_cache"] = rss_mib(os.getpid())
    # Check the reviewed CLI transport path numerically using the actual model.
    direct = [encoder.encode(raw) for raw in (p.read_bytes() for p in paths)]
    raw_score = float((direct[0] @ direct[1].T).item())
    result["raw_vs_transport"] = {"original_path_cosine": raw_score, "service_path_cosine": score,
                                  "absolute_delta": abs(raw_score - score)}
    result["peak_process_rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, 2)
    if encoder.device == "mps":
        result["mps_mib"] = {"allocated": round(encoder.torch.mps.current_allocated_memory() / 1024**2, 2),
                             "driver": round(encoder.torch.mps.driver_allocated_memory() / 1024**2, 2)}
    result["memory"]["end"] = system_memory()
    return result


def integration(paths, output):
    from contextlib import ExitStack
    from unittest.mock import patch
    import httpx
    import uvicorn
    from server import main, challenges, clip_bridge as bridge
    from server.config import load_settings
    from server.evaluation_jobs import EvaluationJobs
    from server.persistence import digest
    from server.tests.test_receipts import MemoryStore

    raws, _, images = image_inputs(paths)
    token = secrets.token_urlsafe(32)
    settings = replace(load_settings(), gemini_api_key="", supabase_url="", supabase_secret_key="",
                       clip_mode="observe", clip_worker_token=token, clip_timeout=20,
                       clip_failure_policy="required", max_requests_per_minute=1000)
    store, broker, jobs = MemoryStore(), bridge.Broker(), EvaluationJobs(workers=2)
    counts = {"stub_calls": 0}
    def stub(*args, **kwargs):
        counts["stub_calls"] += 1
        return {"verdict": "FAIL", "reason": "Local validation Gemini stub",
                "criteria": {key: "MISMATCH" for key in ("subject", "composition", "color", "background")}}

    results = {"images": images, "cases": [], "memory_before": system_memory()}
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    worker = None
    worker_log = None
    workers = []

    def start_worker():
        nonlocal worker, worker_log
        log_path = output / f"worker-{len(workers) + 1}.log"
        worker_log = log_path.open("w")
        env = os.environ.copy()
        # In-memory local overrides; no real service credentials go to the worker.
        for key in list(env):
            if any(word in key for word in ("GEMINI", "SUPABASE", "PRINT_", "CLIP_")):
                env.pop(key)
        env.update(CLIP_API_BASE=base, CLIP_WORKER_TOKEN=token, CLIP_CACHE_DIR=".cache/clip")
        worker = subprocess.Popen(["/usr/bin/caffeinate", "-i", sys.executable, "-m", "clip_worker.worker", "--env", "/dev/null"],
                                  env=env, stdout=worker_log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise RuntimeError("Local worker exited before ready; inspect private cache log")
            if "CLIP ready:" in log_path.read_text():
                children = command("/usr/bin/pgrep", "-P", str(worker.pid))["output"].split()
                # caffeinate may exec the utility in its original PID and leave
                # a small assertion-monitor child. Identify Python by comm,
                # rather than mistakenly reporting that monitor's ~2 MiB RSS.
                pid = None
                for candidate in [worker.pid, *(int(child) for child in children)]:
                    comm = command("/bin/ps", "-o", "comm=", "-p", str(candidate))["output"]
                    if Path(comm).name.lower().startswith("python"):
                        pid = candidate
                        break
                if pid is None:
                    raise RuntimeError("Unable to identify caffeinate model process")
                info = {"caffeinate_pid": worker.pid, "model_pid": pid, "ready_rss_mib": rss_mib(pid)}
                assert info["ready_rss_mib"] > 100, "RSS sample did not identify model process"
                workers.append(info)
                return info
            time.sleep(.1)
        raise TimeoutError("Local model readiness exceeded 60 seconds")

    def stop_worker():
        nonlocal worker, worker_log
        if worker:
            if worker.poll() is None:
                os.killpg(worker.pid, signal.SIGTERM)
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(worker.pid, signal.SIGKILL)
                    worker.wait(timeout=5)
            worker_log.close()
            worker = None

    def start_request(client):
        cid = store.create_challenge(raws[0], "image/jpeg")["id"]
        aid, jid = str(uuid4()), str(uuid4())
        store.insert("attempts", {"id": aid, "challenge_id": cid, "prompt": "validation fixture"})
        store.update("attempts", aid, {"generation_status": "ready", "generated_image_sha256": digest(raws[1])})
        started = time.perf_counter()
        response = client.post(f"/api/challenges/{cid}/evaluations", headers={"X-Challenge-Token": "session-token"},
                               json={"job_id": jid, "attempt_id": aid, "generated_image": "data:image/jpeg;base64," + base64.b64encode(raws[1]).decode()})
        if response.status_code != 202:
            raise AssertionError((response.status_code, response.text))
        return cid, response.json()["job_id"], started

    def wait_result(client, request, timeout=25):
        cid, jid, started = request
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = client.get(f"/api/challenges/{cid}/evaluations/{jid}", headers={"X-Challenge-Token": "session-token"})
            response.raise_for_status()
            data = response.json()
            if data["status"] != "running":
                return data, round((time.perf_counter() - started) * 1000, 2)
            time.sleep(.05)
        raise TimeoutError("Evaluation job did not finish")

    def case(name, assertion, details):
        results["cases"].append({"name": name, "passed": bool(assertion), **details})
        (output / "integration.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"{name}: {'PASS' if assertion else 'FAIL'}", flush=True)
        if not assertion:
            raise AssertionError(name)

    def manual_claim(client):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = client.post("/api/clip-worker/claim", headers={"X-Clip-Worker-Token": token})
            response.raise_for_status()
            job = response.json()["job"]
            if job:
                return job
            time.sleep(.01)
        raise TimeoutError("Manual claim not available")

    def submit(client, job, cosine=.3):
        return client.post(f"/api/clip-worker/jobs/{job['id']}/complete", headers={"X-Clip-Worker-Token": token}, json={
            "lease_token": job["lease_token"], "model": bridge.MODEL, "pipeline": bridge.PIPELINE,
            "cosine": cosine, "elapsed_ms": 1})

    server = uvicorn.Server(uvicorn.Config(main.app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(main, "settings", settings))
            stack.enter_context(patch.object(challenges, "load_settings", side_effect=lambda: settings))
            stack.enter_context(patch.object(bridge, "load_settings", side_effect=lambda: settings))
            stack.enter_context(patch.object(challenges, "Store", return_value=store))
            stack.enter_context(patch.object(challenges, "jobs", jobs))
            stack.enter_context(patch.object(bridge, "broker", broker))
            stack.enter_context(patch.object(challenges, "route_verdict", side_effect=stub))
            stack.enter_context(patch.object(main, "route_verdict", side_effect=stub))
            # Defense in depth: any accidental real provider initialization fails locally.
            stack.enter_context(patch("server.gemini_client._client", side_effect=AssertionError("Paid API forbidden")))
            thread.start()
            with httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
                deadline = time.monotonic() + 10
                while not server.started and time.monotonic() < deadline:
                    time.sleep(.01)
                assert server.started
                first = start_worker()
                delay = []
                for _ in range(3):
                    data, elapsed = wait_result(client, start_request(client))
                    assert data["status"] == "done" and "cosine" in data["result"]["similarity"]
                    delay.append(elapsed)
                case("sequential_real_worker", counts["stub_calls"] == 3, {"latency": summary(delay)})
                requests = [start_request(client), start_request(client)]
                done = [wait_result(client, request) for request in requests]
                case("concurrent_two_real_worker", all(item[0]["status"] == "done" and "cosine" in item[0]["result"]["similarity"] for item in done),
                     {"latencies_ms": [item[1] for item in done], "stub_calls": counts["stub_calls"]})
                results["memory_with_worker"] = system_memory()
                first["active_rss_mib"] = rss_mib(first["model_pid"])

                # Kill after a request has actually been leased by the real worker.
                stop_worker()
                start_worker()
                # Stop the worker's whole process group immediately after claim;
                # delay encoding in this test server's claim response, after lease assignment.
                original_claim = broker.claim
                claimed = threading.Event()
                release_claim = threading.Event()
                def paused_claim():
                    job = original_claim()
                    if job:
                        claimed.set()
                        release_claim.wait(5)
                    return job
                settings = replace(settings, clip_timeout=2)
                main.settings = settings
                before = counts["stub_calls"]
                with patch.object(broker, "claim", side_effect=paused_claim):
                    request = start_request(client)
                    assert claimed.wait(3)
                    stop_worker()
                    release_claim.set()
                    data, elapsed = wait_result(client, request, timeout=8)
                case("kill_after_real_claim_required", data["status"] == "error" and data["error"] == "CLIP_TIMEOUT" and counts["stub_calls"] == before,
                     {"error": data.get("error"), "latency_ms": elapsed, "pending_after": len(broker.tasks)})

                settings = replace(settings, clip_timeout=20)
                main.settings = settings
                restarted = start_worker()
                data, elapsed = wait_result(client, start_request(client))
                case("restart_recovers_next_request", data["status"] == "done" and "cosine" in data["result"]["similarity"], {"latency_ms": elapsed})
                restarted["active_rss_mib"] = rss_mib(restarted["model_pid"])
                stop_worker()

                settings = replace(settings, clip_timeout=.5, clip_failure_policy="fallback")
                main.settings = settings
                data, elapsed = wait_result(client, start_request(client))
                case("worker_offline_fallback", data["status"] == "done" and data["result"]["similarity"].get("error") == "CLIP_TIMEOUT",
                     {"latency_ms": elapsed, "similarity": data.get("result", {}).get("similarity")})

                settings = replace(settings, clip_timeout=.5, clip_failure_policy="required")
                main.settings = settings
                before = counts["stub_calls"]
                request = start_request(client)
                late = manual_claim(client)
                data, elapsed = wait_result(client, request)
                response = submit(client, late)
                case("late_result_rejected", data["status"] == "error" and response.status_code == 409 and counts["stub_calls"] == before,
                     {"status_code": response.status_code, "error": data.get("error"), "latency_ms": elapsed})

                settings = replace(settings, clip_timeout=5)
                main.settings = settings
                request = start_request(client)
                duplicate = manual_claim(client)
                # Hold the waiter so both duplicate HTTP requests exercise the
                # pending-task idempotency branch deterministically.
                with broker.lock:
                    task = broker.tasks[duplicate["id"]]
                # Wait is already running; hold event.set until both POSTs finish.
                original_set = task.done.set
                with patch.object(task.done, "set", return_value=None):
                    first_response = submit(client, duplicate)
                    second_response = submit(client, duplicate)
                    conflicting = submit(client, duplicate, cosine=.4)
                original_set()
                data, _ = wait_result(client, request)
                after_cleanup = submit(client, duplicate)
                case("duplicate_and_conflict", first_response.status_code == 200 and second_response.status_code == 200
                     and conflicting.status_code == 409 and after_cleanup.status_code == 409 and data["status"] == "done",
                     {"statuses": [first_response.status_code, second_response.status_code, conflicting.status_code, after_cleanup.status_code]})

                case("no_paid_or_external_db_calls", not store.tables["api_calls"] and len(broker.tasks) == 0,
                     {"paid_calls": 0, "stub_calls": counts["stub_calls"], "pending_tasks": len(broker.tasks)})
            results["workers"] = workers
            results["memory_after_worker_exit"] = system_memory()
    finally:
        stop_worker()
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        jobs.executor.shutdown(wait=True)
        main._windows.clear()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("benchmark", "integration"))
    parser.add_argument("images", nargs=2, type=Path)
    parser.add_argument("--output", type=Path, default=Path(".cache/local-validation"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = benchmark(args.images) if args.mode == "benchmark" else integration(args.images, args.output)
    path = args.output / (args.mode + ".json")
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Results written: {path}")
    if args.mode == "benchmark":
        print(json.dumps({key: result[key] for key in ("device", "model_prepare_ms", "first_pair", "repeat_no_embedding_cache", "both_embeddings_cached", "rss_mib", "peak_process_rss_mib")}, indent=2))


if __name__ == "__main__":
    main()
