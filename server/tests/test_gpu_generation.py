"""Telemetry boundaries and repeat-safe generation without a real GPU."""
import base64
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from contextlib import nullcontext
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gpu_worker.app import create_app
from server import challenges, main
from server.config import Settings
from server.energy import EnergyMeasurement, energy_summary
from server.evaluation_jobs import EvaluationJobs
from server.generation import generate_image
from server.gemini_client import GeminiError
from server.persistence import CallRecorder, digest
from server.tests.test_receipts import MemoryStore, png


def telemetry(rid, **overrides):
    return dict(version=1, measurement_id=rid, status="measured", energy_j=3600,
        duration_s=20, method="zeus_nvml", scope="gpu_board_generation_window", includes_idle=True,
        gpu_name="test GPU", gpu_uuid="GPU-test", model_id="test-model", model_revision="a" * 40,
        zeus_version="test", width=1024, height=1024, steps=30, seed=1,
        started_at="2026-11-02T00:00:00+00:00", finished_at="2026-11-02T00:00:20+00:00",
        **overrides)


def settings():
    return Settings(gemini_api_key="fake", image_model="test-model", router_model="router",
        router_provider="gemini", public_app_origin=main.settings.public_app_origin,
        max_requests_per_minute=1000, port=8000, image_provider="zeus",
        gpu_api_url="https://gpu.test", gpu_api_key="k" * 32)


def result_for(rid):
    raw = png()
    return dict(request_id=rid, status="succeeded", energy=telemetry(rid),
                image_url="data:image/png;base64," + base64.b64encode(raw).decode(), image_sha256=digest(raw))


class EnergyTests(unittest.TestCase):
    def test_joules_wh_and_partial_coverage_including_failed_calls(self):
        rid = str(uuid4())
        summary = energy_summary([
            dict(operation="generate", status="failed", energy_measurement=telemetry(rid)),
            dict(operation="generate", status="unknown"), dict(operation="router", status="succeeded")])
        self.assertEqual(summary["energy_wh"], 1)
        self.assertEqual(summary["measured_generation_calls"], 1)
        self.assertEqual(summary["unmeasured_generation_calls"], 1)
        self.assertEqual(summary["unmeasured_router_calls"], 1)
        self.assertFalse(summary["generation_complete"])
        self.assertIsNone(energy_summary([dict(operation="generate")])["energy_wh"])

    def test_invalid_counter_is_not_measured_zero(self):
        for value in (0, -1, float("nan"), float("inf")):
            payload = telemetry(str(uuid4()))
            payload["energy_j"] = value
            with self.assertRaises(ValidationError):
                EnergyMeasurement.model_validate(payload)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.rid = str(uuid4())
        self.store = MemoryStore()
        self.recorder = CallRecorder(self.store, str(uuid4()), self.rid, "generate", "test-model", provider="zeus")

    def response(self, payload):
        return httpx.Response(200, json=payload, request=httpx.Request("POST", "https://gpu.test/v1/generate"))

    def test_image_and_telemetry_saved_together(self):
        # MemoryStore's legacy default overwrites the provider: inspect the insert instead.
        with patch.object(self.store, "insert", wraps=self.store.insert) as insert, \
             patch("server.generation.httpx.post", return_value=self.response(result_for(self.rid))) as post:
            image = generate_image(settings(), "a bicycle", self.recorder)
            self.assertTrue(image.startswith("data:image/png"))
            self.assertEqual(insert.call_args.args[1]["provider"], "zeus")
            self.assertEqual(post.call_count, 1)
        row = self.store.tables["api_calls"][0]
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["energy_measurement"]["energy_j"], 3600)
        self.assertIsNone(row["total_tokens"])

    def test_timeout_does_not_retry_and_records_unknown(self):
        with patch("server.generation.httpx.post", side_effect=httpx.ReadTimeout("timeout")) as post:
            with self.assertRaisesRegex(GeminiError, "UNCONFIRMED"):
                generate_image(settings(), "a bicycle", self.recorder)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.store.tables["api_calls"][0]["status"], "unknown")

    def test_mismatched_image_rejects_telemetry(self):
        payload = result_for(self.rid)
        payload["image_sha256"] = "bad"
        with patch("server.generation.httpx.post", return_value=self.response(payload)):
            with self.assertRaisesRegex(GeminiError, "UNCONFIRMED"):
                generate_image(settings(), "a bicycle", self.recorder)
        self.assertNotIn("energy_measurement", self.store.tables["api_calls"][0])

    def test_failed_generation_keeps_measured_energy(self):
        payload = dict(request_id=self.rid, status="failed", error="GPU_GENERATION_FAILED", energy=telemetry(self.rid))
        with patch("server.generation.httpx.post", return_value=self.response(payload)):
            with self.assertRaisesRegex(GeminiError, "GPU_GENERATION_FAILED"):
                generate_image(settings(), "a bicycle", self.recorder)
        row = self.store.tables["api_calls"][0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["energy_measurement"]["energy_j"], 3600)


class WorkerTests(unittest.TestCase):
    def test_auth_replay_conflict_and_restart_dedup(self):
        rid = str(uuid4())
        runner = Mock()
        runner.run.return_value = result_for(rid)
        headers = {"Authorization": "Bearer " + "k" * 32}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "requests.sqlite3"
            with TestClient(create_app(runner, path, "k" * 32)) as client:
                body = dict(request_id=rid, prompt="a bicycle")
                self.assertEqual(client.post("/v1/generate", json=body).status_code, 403)
                first = client.post("/v1/generate", json=body, headers=headers)
                second = client.post("/v1/generate", json=body, headers=headers)
                self.assertEqual(first.json(), second.json())
                self.assertEqual(runner.run.call_count, 1)
                self.assertEqual(client.post("/v1/generate", json={**body, "prompt": "different"}, headers=headers).status_code, 409)
                self.assertEqual(client.get("/v1/requests/" + rid, headers=headers).json(), first.json())
            with TestClient(create_app(runner, path, "k" * 32)) as restarted:
                self.assertEqual(restarted.post("/v1/generate", json=body, headers=headers).status_code, 200)
                self.assertEqual(runner.run.call_count, 1)

    def test_concurrent_request_is_busy_without_another_gpu_execution(self):
        runner = Mock()
        entered, release = threading.Event(), threading.Event()
        rid = str(uuid4())
        def slow(request):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test timeout")
            return result_for(str(request.request_id))
        runner.run.side_effect = slow
        headers = {"Authorization": "Bearer " + "k" * 32}
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_app(runner, Path(folder) / "requests.sqlite3", "k" * 32)) as client:
                thread = threading.Thread(target=lambda: client.post("/v1/generate", json={"request_id": rid, "prompt": "bicycle"}, headers=headers))
                thread.start()
                try:
                    self.assertTrue(entered.wait(2))
                    other = client.post("/v1/generate", json={"request_id": str(uuid4()), "prompt": "bicycle"}, headers=headers)
                    self.assertEqual(other.status_code, 429)
                    self.assertEqual(runner.run.call_count, 1)
                finally:
                    release.set()
                    thread.join(3)

    def test_interrupted_request_is_never_regenerated(self):
        from gpu_worker.app import RequestLedger
        rid = str(uuid4())
        with tempfile.TemporaryDirectory() as folder:
            ledger = RequestLedger(Path(folder) / "requests.sqlite3")
            ledger.start(rid, "fingerprint")
            restarted = RequestLedger(ledger.path)
            self.assertEqual(restarted.existing(rid, "fingerprint")["status"], "unknown")


class MeasurementWindowTests(unittest.TestCase):
    def runner(self, energy_j=3600, fail=False):
        from gpu_worker.app import SDXLRunner
        runner = object.__new__(SDXLRunner)
        runner.width = runner.height = 1024
        runner.steps, runner.revision, runner.model_id = 30, "a" * 40, "test-model"
        runner.gpu_name, runner.gpu_uuid, runner.zeus_version = "test GPU", "GPU-test", "test"
        runner.torch = NS(inference_mode=nullcontext, Generator=lambda **kwargs: NS(manual_seed=lambda seed: seed))
        runner.monitor = Mock()
        runner.monitor.end_window.return_value = NS(total_energy=energy_j, time=20)
        def encode(out, format):
            self.assertTrue(runner.monitor.end_window.called, "PNG encoding must follow energy window")
            out.write(png())
        image = Mock(); image.save.side_effect = encode
        runner.pipe = Mock(side_effect=RuntimeError("GPU failure")) if fail else Mock(return_value=NS(images=[image]))
        return runner

    def test_window_wraps_generation_and_excludes_encoding(self):
        from gpu_worker.app import GenerateRequest
        runner = self.runner()
        result = runner.run(GenerateRequest(request_id=uuid4(), prompt="bicycle"))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["energy"]["energy_j"], 3600)
        runner.monitor.begin_window.assert_called_once()
        runner.monitor.end_window.assert_called_once()

    def test_zero_counter_is_unavailable_and_failed_generation_still_measured(self):
        from gpu_worker.app import GenerateRequest
        result = self.runner(energy_j=0).run(GenerateRequest(request_id=uuid4(), prompt="bicycle"))
        self.assertEqual(result["energy"]["status"], "unavailable")
        self.assertIsNone(result["energy"]["energy_j"])
        with self.assertLogs("uvicorn.error", level="ERROR"):
            result = self.runner(fail=True).run(GenerateRequest(request_id=uuid4(), prompt="bicycle"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["energy"]["energy_j"], 3600)


class GenerationJobTests(unittest.TestCase):
    def test_long_gpu_request_uses_short_http_calls_and_one_generation(self):
        config, store = settings(), MemoryStore()
        cid = store.create_challenge(png(), "image/png")["id"]
        aid = str(uuid4())
        entered, release = threading.Event(), threading.Event()
        manager = EvaluationJobs(workers=1, max_jobs=8, prefix="GENERATION")
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timeout")
            return result_for(aid)["image_url"]
        headers = {"X-Challenge-Token": "session-token"}
        try:
            with patch.object(challenges, "Store", return_value=store), \
                 patch.object(challenges, "load_settings", return_value=config), \
                 patch.object(challenges, "generation_jobs", manager), \
                 patch.object(challenges, "generate_image", side_effect=slow) as generate:
                client = TestClient(main.app)
                body = dict(attempt_id=aid, prompt="a bicycle")
                url = f"/api/challenges/{cid}/generations"
                start = client.post(url, json=body, headers=headers)
                self.assertEqual(start.status_code, 202)
                self.assertTrue(entered.wait(2))
                self.assertEqual(client.post(url, json=body, headers=headers).json()["job_id"], aid)
                self.assertEqual(client.get(url + "/" + aid, headers=headers).json()["status"], "running")
                self.assertEqual(client.get(url + "/" + aid, headers={"X-Challenge-Token": "wrong"}).status_code, 403)
                release.set()
                manager.executor.shutdown(wait=True)
                done = client.get(url + "/" + aid, headers=headers).json()
                self.assertEqual(done["status"], "done", done)
                self.assertEqual(generate.call_count, 1)
        finally:
            release.set()
            manager.executor.shutdown(wait=True)
