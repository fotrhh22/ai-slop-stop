"""Sequential remote acceptance probe. Never retries a generation POST.

GPU_API_URL and GPU_API_KEY come from the environment, not CLI arguments.
python -m gpu_worker.smoke_test --count 10 --output evidence.json
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import math
from time import perf_counter
import base64
import urllib.request
from uuid import uuid4

from server.energy import EnergyMeasurement, RouterEnergyMeasurement


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--prompt", default="A red bicycle standing beside a white wall, illustration")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-router", action="store_true")
    parser.add_argument("--target", type=Path, help="Target image for quality trial; omitted = compare generated image to itself")
    args = parser.parse_args()
    if not 1 <= args.count <= 30:
        parser.error("count must be 1..30")
    base = os.environ["GPU_API_URL"].rstrip("/")
    key = os.environ["GPU_API_KEY"]
    if not base.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
        parser.error("use HTTPS or a local SSH tunnel")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    results = []
    for _ in range(args.count):
        rid = str(uuid4())
        request = urllib.request.Request(base + "/v1/generate", headers=headers,
            data=json.dumps({"request_id": rid, "prompt": args.prompt}).encode())
        # Write each ID before sending: a lost response can be investigated by ID.
        results.append({"request_id": rid, "status": "unconfirmed",
            "prompt_sha256": hashlib.sha256(args.prompt.encode()).hexdigest()})
        args.output.write_text(json.dumps(results, indent=2))
        roundtrip_start = perf_counter()
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.load(response)
        if payload.get("request_id") != rid:
            raise ValueError("request ID mismatch")
        energy = EnergyMeasurement.model_validate(payload["energy"])
        results[-1].update(status=payload["status"], energy=energy.public(), image_sha256=payload.get("image_sha256"))
        args.output.write_text(json.dumps(results, indent=2))
        print(json.dumps({"request_id": rid, "status": payload["status"],
                          "energy_wh": energy.public()["energy_wh"], "duration_s": energy.duration_s}))
        if payload["status"] != "succeeded" or energy.status != "measured":
            raise RuntimeError("GPU acceptance failed; inspect evidence without retrying this request")
        if args.with_router:
            if args.target:
                from server.images import evaluation_image
                raw, mime = evaluation_image(args.target.read_bytes(), "image/png")
                target = f"data:{mime};base64," + base64.b64encode(raw).decode()
            else:
                target = payload["image_url"]
            router_id = str(uuid4())
            results[-1].update(router_request_id=router_id, router_status="unconfirmed",
                               quality_trial=args.target is not None)
            args.output.write_text(json.dumps(results, indent=2))
            request = urllib.request.Request(base + "/v1/route", headers=headers, data=json.dumps({
                "request_id": router_id, "target_image": target, "generated_image": payload["image_url"]}).encode())
            with urllib.request.urlopen(request, timeout=180) as response:
                routed = json.load(response)
            if routed.get("request_id") != router_id:
                raise ValueError("router request ID mismatch")
            router_energy = RouterEnergyMeasurement.model_validate(routed["energy"])
            results[-1].update(router_status=routed["status"], router_energy=router_energy.public(),
                               verdict=routed.get("verdict"), gpu_roundtrip_s=perf_counter() - roundtrip_start)
            args.output.write_text(json.dumps(results, indent=2))
            if routed["status"] != "succeeded" or router_energy.status != "measured":
                raise RuntimeError("Router acceptance failed; inspect evidence")
    if args.with_router:
        times = sorted(r["gpu_roundtrip_s"] for r in results)
        print(json.dumps({"gpu_roundtrip_p95_s": times[math.ceil(0.95*len(times))-1],
                          "includes_browser_render_supabase": False}))
    wh = [r["energy"]["energy_wh"] for r in results]
    print(json.dumps({"count": len(wh), "mean_wh": statistics.mean(wh), "median_wh": statistics.median(wh),
                      "min_wh": min(wh), "max_wh": max(wh)}))


if __name__ == "__main__":
    main()
