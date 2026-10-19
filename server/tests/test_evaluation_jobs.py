"""Slow-provider regression: HTTP requests must finish before the provider does."""
import base64
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from server import challenges, main
from server.config import Settings
from server.evaluation_jobs import EvaluationJobs
from server.gemini_client import GeminiError
from server.persistence import digest
from server.tests.test_receipts import MemoryStore, png


class EvaluationJobTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(gemini_api_key="fake", image_model="image-test", router_model="router-test",
            router_provider="gemini", public_app_origin=main.settings.public_app_origin,
            max_requests_per_minute=1000, port=8000)
        self.store, self.jobs = MemoryStore(), EvaluationJobs(workers=1)
        self.raw = png()
        self.data = "data:image/png;base64," + base64.b64encode(self.raw).decode()
        self.cid = self.store.create_challenge(self.raw, "image/png")["id"]
        self.aid, self.jid = str(uuid4()), str(uuid4())
        self.store.insert("attempts", {"id": self.aid, "challenge_id": self.cid, "prompt": "test"})
        self.store.update("attempts", self.aid, {"generation_status": "ready", "generated_image_sha256": digest(self.raw)})
        self.headers = {"X-Challenge-Token": "session-token"}
        self.body = {"attempt_id": self.aid, "job_id": self.jid, "generated_image": self.data}
        self.url = f"/api/challenges/{self.cid}/evaluations"
        self.patches = [patch.object(challenges, "load_settings", return_value=self.settings),
                        patch.object(challenges, "Store", return_value=self.store),
                        patch.object(challenges, "jobs", self.jobs), patch.object(main, "settings", self.settings)]
        for p in self.patches: p.start()
        main._windows.clear()
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        self.jobs.executor.shutdown(wait=True)
        for p in reversed(self.patches): p.stop()

    def test_slow_fail_returns_start_and_status_without_waiting_or_repeating_call(self):
        entered, release = threading.Event(), threading.Event()
        def slow_provider(*args, **kwargs):
            entered.set()
            if not release.wait(5): raise AssertionError("provider never released")
            return {"verdict": "FAIL", "reason": "구도가 다릅니다."}
        with patch.object(challenges, "route_verdict", side_effect=slow_provider) as provider:
            try:
                started = self.client.post(self.url, json=self.body, headers=self.headers)
                self.assertEqual(started.status_code, 202, started.text)
                self.assertTrue(entered.wait(2))
                for _ in range(25):
                    status = self.client.get(self.url + "/" + self.jid, headers=self.headers)
                    self.assertEqual(status.status_code, 200, status.text)
                    self.assertEqual(status.json()["status"], "running")
                # Lost start-response retransmission and another Try both join.
                self.assertEqual(self.client.post(self.url, json=self.body, headers=self.headers).json()["job_id"], self.jid)
                another = {**self.body, "job_id": str(uuid4())}
                self.assertEqual(self.client.post(self.url, json=another, headers=self.headers).json()["job_id"], self.jid)
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(self.client.get(self.url + "/" + self.jid).status_code, 403)
            finally:
                release.set()
            self.jobs.executor.shutdown(wait=True)
            finished = self.client.get(self.url + "/" + self.jid, headers=self.headers).json()
            self.assertEqual(finished["status"], "done")
            self.assertEqual(finished["result"]["verdict"], "FAIL")
            self.assertEqual(self.store.attempt(self.cid, self.aid)["router_verdict"], "FAIL")
            self.assertEqual(provider.call_count, 1)
            again = self.client.post(self.url, json=self.body, headers=self.headers).json()
            self.assertEqual(again["status"], "done")
            self.assertEqual(provider.call_count, 1)

    def test_pass_job_defers_receipt(self):
        with patch.object(challenges, "route_verdict", return_value={"verdict": "PASS", "reason": "test"}), \
             patch.object(self.store, "finalize") as finalize:
            response = self.client.post(self.url, json=self.body, headers=self.headers)
            self.assertEqual(response.status_code, 202)
            self.jobs.executor.shutdown(wait=True)
            result = self.client.get(self.url + "/" + self.jid, headers=self.headers).json()
            self.assertTrue(result["result"]["receipt_pending"])
            self.assertEqual(result["result"]["verdict"], "PASS")
            finalize.assert_not_called()

    def test_provider_timeout_is_error_not_a_fail_verdict(self):
        with patch.object(challenges, "route_verdict", side_effect=GeminiError("ROUTER_TIMEOUT")) as provider:
            self.client.post(self.url, json=self.body, headers=self.headers)
            self.jobs.executor.shutdown(wait=True)
            result = self.client.get(self.url + "/" + self.jid, headers=self.headers).json()
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error"], "ROUTER_TIMEOUT")
            self.assertNotIn("result", result)
            self.assertEqual(provider.call_count, 1)

    def test_lost_job_is_explicitly_interrupted(self):
        response = self.client.get(self.url + "/" + str(uuid4()), headers=self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "EVALUATION_INTERRUPTED")

    def test_capacity_is_bounded(self):
        release = threading.Event()
        try:
            self.jobs.start(self.jid, self.cid, self.aid, "token", lambda: release.wait(5))
            with self.assertRaises(HTTPException) as error:
                self.jobs.start(str(uuid4()), str(uuid4()), str(uuid4()), "token", lambda: {})
            self.assertEqual(error.exception.detail, "EVALUATION_BUSY")
        finally:
            release.set()

    def test_unhandled_server_error_keeps_cors_header(self):
        origin = self.settings.public_app_origin
        with patch.object(main, "generate_image", side_effect=RuntimeError("synthetic failure")):
            response = self.client.post("/api/generate", json={"prompt": "test"}, headers={"Origin": origin})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers.get("access-control-allow-origin"), origin)
