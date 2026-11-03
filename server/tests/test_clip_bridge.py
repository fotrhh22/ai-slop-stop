import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import time
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from server import clip_bridge as bridge, main
from server.config import load_settings


class ClipBridgeTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(load_settings(), clip_mode="observe", clip_worker_token="x" * 40,
                                clip_timeout=1, clip_failure_policy="fallback")
        out = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(out, format="PNG")
        self.raw = out.getvalue()
        self.route = Mock(return_value={"verdict": "PASS", "reason": "matched"})
        self.result = {"model": bridge.MODEL, "pipeline": bridge.PIPELINE, "cosine": 0.8, "elapsed_ms": 5}

    def evaluate(self, settings=None):
        return bridge.evaluate_with_clip(settings or self.settings, self.raw, "image/png",
                                         self.raw, "image/png", self.route)

    def test_outbound_worker_roundtrip_before_llm(self):
        broker = bridge.Broker()
        client = TestClient(main.app)
        headers = {"X-Clip-Worker-Token": self.settings.clip_worker_token}
        with patch.object(bridge, "broker", broker), patch.object(bridge, "load_settings", return_value=self.settings):
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(self.evaluate)
                deadline = time.monotonic() + 1
                job = None
                while not job and time.monotonic() < deadline:
                    job = client.post("/api/clip-worker/claim", headers=headers).json()["job"]
                    if not job:
                        time.sleep(0.005)
                self.assertIsNotNone(job)
                self.route.assert_not_called()
                self.assertEqual(base64.b64decode(job["target_base64"]), self.raw)
                endpoint = f"/api/clip-worker/jobs/{job['id']}/complete"
                body = {"lease_token": job["lease_token"], **self.result}
                self.assertEqual(client.post(endpoint, headers=headers, json=body).status_code, 200)
                result = future.result(timeout=1)
                self.assertEqual(result["similarity"]["cosine"], 0.8)
                self.assertTrue(result["router_called"])
                self.assertFalse(broker.tasks)
        self.route.assert_called_once()

    def test_gate_skips_paid_call_only_below_threshold(self):
        settings = replace(self.settings, clip_mode="gate", clip_reject_below=0.5)
        for score, calls in ((0.49, 0), (0.5, 1), (0.9, 1)):
            self.route.reset_mock()
            with patch.object(bridge.broker, "compute", return_value={**self.result, "cosine": score}):
                result = self.evaluate(settings)
            self.assertEqual(self.route.call_count, calls)
            self.assertEqual(result["router_called"], bool(calls))
            if not calls:
                self.assertEqual(result["verdict"], "FAIL")
                self.assertNotIn("criteria", result)

    def test_observe_never_skips_llm(self):
        with patch.object(bridge.broker, "compute", return_value={**self.result, "cosine": -1}):
            self.assertEqual(self.evaluate()["verdict"], "PASS")
        self.route.assert_called_once()

    def test_timeout_fallback_and_required(self):
        with patch.object(bridge.broker, "compute", side_effect=HTTPException(503, "CLIP_TIMEOUT")):
            self.assertEqual(self.evaluate()["similarity"]["status"], "unavailable")
            self.route.reset_mock()
            with self.assertRaises(HTTPException):
                self.evaluate(replace(self.settings, clip_failure_policy="required"))
            self.route.assert_not_called()

    def test_timeout_cleans_images_and_late_result_rejected(self):
        broker = bridge.Broker()
        with self.assertRaises(HTTPException):
            broker.compute({"target_base64": "payload"}, 0.001)
        self.assertFalse(broker.tasks)
        with self.assertRaises(HTTPException):
            broker.complete("missing", "lease", self.result)

    def test_capacity_lease_and_idempotency(self):
        broker = bridge.Broker(capacity=1)
        task = bridge.Task({}, time.monotonic() + 1)
        broker.tasks["job"] = task
        with self.assertRaises(HTTPException):
            broker.compute({}, 0.001)
        job = broker.claim()
        self.assertIsNone(broker.claim())
        with self.assertRaises(HTTPException):
            broker.complete("job", "wrong", self.result)
        broker.complete("job", job["lease_token"], self.result)
        broker.complete("job", job["lease_token"], self.result)
        with self.assertRaises(HTTPException):
            broker.complete("job", job["lease_token"], {**self.result, "cosine": 0})

    def test_worker_auth_and_invalid_result(self):
        client = TestClient(main.app)
        with patch.object(bridge, "load_settings", return_value=self.settings):
            self.assertEqual(client.post("/api/clip-worker/claim").status_code, 403)
            headers = {"X-Clip-Worker-Token": self.settings.clip_worker_token}
            endpoint = "/api/clip-worker/jobs/00000000-0000-0000-0000-000000000001/complete"
            for extra in ({"cosine": 2}, {"model": "wrong-model"}, {"pipeline": "wrong"}):
                body = {"lease_token": "00000000-0000-0000-0000-000000000002", **self.result, **extra}
                self.assertEqual(client.post(endpoint, headers=headers, json=body).status_code, 422)

    def test_missing_gate_threshold_is_configuration_error(self):
        with self.assertRaises(HTTPException) as error:
            self.evaluate(replace(self.settings, clip_mode="gate", clip_reject_below=None))
        self.assertEqual(error.exception.detail, "CLIP_BAD_CONFIG")
        self.route.assert_not_called()

    def test_off_does_not_dispatch(self):
        with patch.object(bridge.broker, "compute") as compute:
            self.evaluate(replace(self.settings, clip_mode="off"))
        compute.assert_not_called()

    def test_qwen_observe_reports_clip_score_and_keeps_qwen_final_verdict(self):
        with patch.object(bridge.broker,'compute',return_value={**self.result,'cosine':0.01}):
            result=self.evaluate(replace(self.settings,router_provider='zeus'))
        self.route.assert_called_once()
        self.assertEqual(result['verdict'],'PASS')
        self.assertEqual(result['evaluation_source'],'qwen_vision')
        self.assertEqual(result['similarity']['cosine'],0.01)


if __name__ == "__main__":
    unittest.main()
