"""Review regressions: no real provider, database or model downloads."""
import base64
import io
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from server import main, challenges, clip_bridge as bridge
from server.evaluation_jobs import EvaluationJobs
from server.images import evaluation_image
from server.persistence import digest
from server.tests.test_receipts import MemoryStore, png
from clip_worker import worker


class ReviewTests(unittest.TestCase):
    def test_exhausted_claim_bucket_does_not_block_result_submission(self):
        settings = replace(main.settings, max_requests_per_minute=20, clip_worker_token="x"*40)
        client = TestClient(main.app)
        headers = {"X-Clip-Worker-Token": "x"*40}
        main._windows.clear()
        try:
            with patch.object(main, "settings", settings), patch.object(bridge, "load_settings", return_value=settings):
                for _ in range(120):
                    self.assertEqual(client.post("/api/clip-worker/claim", headers=headers).status_code, 200)
                self.assertEqual(client.post("/api/clip-worker/claim", headers=headers).status_code, 429)
                response = client.post(f"/api/clip-worker/jobs/{uuid4()}/complete", headers=headers, json={
                    "lease_token": str(uuid4()), "model": bridge.MODEL, "pipeline": bridge.PIPELINE,
                    "cosine": .3, "elapsed_ms": 1})
                # A nonexistent lease reaches the handler (409), rather than
                # being rejected by the exhausted claim bucket (429).
                self.assertEqual(response.status_code, 409)
        finally:
            main._windows.clear()

    def test_clip_polling_does_not_exhaust_ui_or_print_bucket(self):
        settings = replace(main.settings, max_requests_per_minute=20, clip_worker_token="x"*40)
        client = TestClient(main.app)
        main._windows.clear()
        with patch.object(main, "settings", settings), patch.object(bridge, "load_settings", return_value=settings):
            for _ in range(120):
                self.assertEqual(client.post("/api/clip-worker/claim", headers={"X-Clip-Worker-Token": "x"*40}).status_code, 200)
            response = client.get(f"/api/challenges/{uuid4()}/evaluations/{uuid4()}")
            self.assertNotEqual(response.status_code, 429)
        main._windows.clear()

    def test_compare_uses_same_transport_as_server(self):
        out = io.BytesIO()
        Image.new("RGB", (2048, 1024), "red").save(out, format="PNG")
        raw = out.getvalue()
        expected, _ = evaluation_image(raw, "image/png")
        encoder = Mock(device="cpu")
        encoder.compare.return_value = 1.0
        with TemporaryDirectory() as folder:
            path = Path(folder)/"image.png"
            path.write_bytes(raw)
            with patch.object(worker, "Encoder", return_value=encoder), \
                    patch("sys.argv", ["worker", "--compare", str(path), str(path)]), redirect_stdout(io.StringIO()):
                worker.main()
        self.assertEqual(encoder.compare.call_args.args, (expected, expected))

    def test_real_async_gate_saves_fail_and_rejects_staff_without_paid_call(self):
        settings = replace(main.settings, clip_mode="gate", clip_worker_token="x"*40,
                           clip_reject_below=0.5, max_requests_per_minute=1000)
        store, jobs = MemoryStore(), EvaluationJobs(workers=1)
        raw = png()
        data = "data:image/png;base64," + base64.b64encode(raw).decode()
        cid = store.create_challenge(raw, "image/png")["id"]
        aid, jid = str(uuid4()), str(uuid4())
        store.insert("attempts", {"id": aid, "challenge_id": cid, "prompt": "test"})
        store.update("attempts", aid, {"generation_status": "ready", "generated_image_sha256": digest(raw)})
        headers = {"X-Challenge-Token": "session-token"}
        main._windows.clear()
        try:
            with patch.object(main, "settings", settings), patch.object(challenges, "load_settings", return_value=settings), \
                    patch.object(challenges, "Store", return_value=store), patch.object(challenges, "jobs", jobs), \
                    patch.object(bridge.broker, "compute", return_value={"cosine":0.1, "model":bridge.MODEL, "pipeline":bridge.PIPELINE, "elapsed_ms":1}), \
                    patch.object(challenges, "route_verdict") as route:
                client = TestClient(main.app)
                body = {"job_id":jid, "attempt_id":aid, "generated_image":data}
                self.assertEqual(client.post(f"/api/challenges/{cid}/evaluations", json=body, headers=headers).status_code, 202)
                jobs.executor.shutdown(wait=True)
                result = client.get(f"/api/challenges/{cid}/evaluations/{jid}", headers=headers).json()["result"]
                self.assertEqual(result["verdict"], "FAIL")
                self.assertIsNone(result["router_model"])
                self.assertFalse(result["router_called"])
                self.assertEqual(store.attempt(cid, aid)["evaluation_result"], result)
                saved = client.post(f"/api/challenges/{cid}/finalize", json={"attempt_id":aid,"generated_image":data,"source":"staff"}, headers=headers)
                self.assertEqual(saved.status_code, 422)
                self.assertEqual(store.tables["successes"], [])
                self.assertEqual(store.tables["api_calls"], [])
                route.assert_not_called()
        finally:
            jobs.executor.shutdown(wait=True)
            main._windows.clear()
