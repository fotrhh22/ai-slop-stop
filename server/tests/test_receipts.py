from __future__ import annotations

import base64
import io
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from server import challenges, main
from server.config import Settings
from server.gemini_client import _with_retry
from server.persistence import CallRecorder, PersistenceError, Store, digest


def png():
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(out, format="PNG")
    return out.getvalue()


class MemoryStore:
    """Workflow fixture. Snapshot SQL itself is checked against PostgreSQL separately."""
    def __init__(self):
        self.tables = {t: [] for t in ("challenges", "attempts", "api_calls", "successes")}
        self.images = {}
        self.finalizations = 0

    def rows(self, table, **filters):
        result = self.tables[table]
        for k, v in filters.items():
            if v.startswith("eq."):
                result = [r for r in result if str(r.get(k)) == v[3:]]
        return result

    def insert(self, table, data):
        row = {"id": str(uuid4()), **data}
        if table == "attempts": row.update(generation_status="pending")
        if table == "challenges": row.update(status="active")
        if table == "api_calls": row.update(status="pending", provider="gemini")
        self.tables[table].append(row)
        return row

    def update(self, table, rid, data):
        self.rows(table, id="eq." + rid)[0].update(data)

    def create_challenge(self, raw, mime):
        cid = str(uuid4())
        self.insert("challenges", {"id": cid, "target_image_path": cid + "/target"})
        self.images[cid + "/target"] = raw
        return {"id": cid, "accessToken": "session-token"}

    def authorize(self, cid, token):
        if token != "session-token": raise PersistenceError("CHALLENGE_ACCESS_DENIED", 403)
        return self.rows("challenges", id="eq." + cid)[0]

    def attempt(self, cid, aid):
        return self.rows("attempts", challenge_id="eq." + cid, id="eq." + aid)[0]

    def download(self, bucket, path): return self.images[path]
    def receipt(self, cid): return next(iter(self.rows("successes", challenge_id="eq." + cid)), None)

    def finalize(self, cid, aid, raw, mime, source, actor=None, reason=None):
        existing = self.receipt(cid)
        if existing: return existing
        self.finalizations += 1
        attempt = self.attempt(cid, aid)
        calls = self.rows("api_calls", challenge_id="eq." + cid)
        saved = self.insert("successes", {"challenge_id": cid, "attempt_id": aid,
            "prompt": attempt["prompt"], "pass_source": source, "router_verdict": attempt["router_verdict"],
            "generation_calls": sum(c["operation"] == "generate" for c in calls),
            "router_calls": sum(c["operation"] == "router" for c in calls),
            "model_calls_total": len(calls), "known_tokens_total": sum(c.get("total_tokens") or 0 for c in calls),
            "calls_without_total_tokens": sum(c.get("total_tokens") is None for c in calls),
            "source": "supabase"})
        self.update("challenges", cid, {"status": "succeeded"})
        return saved


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(gemini_api_key="fake", image_model="image-test", router_model="router-test",
            router_provider="gemini", public_app_origin="http://127.0.0.1:5173", max_requests_per_minute=1000,
            port=8000, staff_auth_token="staff-secret")
        self.store = MemoryStore()
        self.raw = png()
        self.data = "data:image/png;base64," + base64.b64encode(self.raw).decode()
        main._windows.clear()
        settings_patch = patch.object(main, "settings", self.settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def run_workflow(self, staff=False):
        router_results = iter(["FAIL", "FAIL", "FAIL" if staff else "PASS"])
        count = {"generate": 0}
        def provider(**kwargs):
            generation = isinstance(kwargs["contents"], str)
            if generation:
                count["generate"] += 1
                if count["generate"] == 1: raise RuntimeError("503 temporarily unavailable")
            return NS(usage_metadata={"prompt_token_count": 20, "candidates_token_count": 70,
                "thoughts_token_count": 10, "total_token_count": 100},
                model_version="image-test" if generation else "router-test",
                candidates=[NS(content=NS(parts=[NS(inline_data=NS(data=self.raw, mime_type="image/png"))]))],
                text=json.dumps({"verdict": next(router_results) if not generation else "FAIL", "reason": "test",
                    "criteria": {key: "MATCH" for key in ("subject", "composition", "color", "background")}}))
        fake_client = NS(models=NS(generate_content=Mock(side_effect=provider)))
        with patch.object(challenges, "load_settings", return_value=self.settings), \
             patch.object(challenges, "Store", return_value=self.store), \
             patch("server.gemini_client._client", return_value=fake_client), \
             patch("server.gemini_client.time.sleep"):
            client = TestClient(main.app)
            created = client.post("/api/challenges", json={"target_image": self.data}).json()
            cid = created["id"]
            headers = {"X-Challenge-Token": "session-token"}
            for _ in range(3):
                aid = str(uuid4())
                prompt = "  exact prompt\nsecond line  "
                response = client.post(f"/api/challenges/{cid}/generate", json={"attempt_id": aid, "prompt": prompt}, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                response = client.post(f"/api/challenges/{cid}/evaluate", json={"attempt_id": aid, "generated_image": self.data}, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
            if staff:
                body = {"attempt_id": aid, "generated_image": self.data, "source": "staff", "actor": "operator", "reason": "reviewed"}
                denied = client.post(f"/api/challenges/{cid}/finalize", json=body, headers=headers)
                self.assertEqual(denied.status_code, 403)
                response = client.post(f"/api/challenges/{cid}/finalize", json=body, headers={**headers, "X-Staff-Token": "staff-secret"})
                saved = response.json()
                self.assertEqual(saved["router_verdict"], "FAIL")
            else:
                saved = response.json()["receipt"]
            self.assertEqual(saved["prompt"], prompt)
            self.assertEqual(saved["generation_calls"], 4)  # 3 generations + retry
            self.assertEqual(saved["router_calls"], 3)  # both FAILs counted
            self.assertEqual(saved["model_calls_total"], 7)
            self.assertEqual(saved["known_tokens_total"], 600)
            self.assertEqual(saved["calls_without_total_tokens"], 1)
            before = fake_client.models.generate_content.call_count
            again = client.get(f"/api/challenges/{cid}/receipt", headers=headers)
            self.assertEqual(again.json()["id"], saved["id"])
            self.assertEqual(fake_client.models.generate_content.call_count, before)
            self.assertEqual(client.get(f"/api/challenges/{cid}/receipt").status_code, 403)
            self.assertEqual(self.store.finalizations, 1)

    def test_router_pass_accumulates_failures_retries_and_missing_usage(self): self.run_workflow()
    def test_staff_pass_requires_auth_and_preserves_original_fail(self): self.run_workflow(staff=True)

    def test_deferred_receipt_returns_saved_pass_before_finalize(self):
        created = self.store.create_challenge(self.raw, "image/png")
        cid, aid = created["id"], str(uuid4())
        self.store.insert("attempts", {"id": aid, "challenge_id": cid, "prompt": "test"})
        self.store.update("attempts", aid, {"generation_status": "ready", "generated_image_sha256": digest(self.raw)})
        result = {"verdict": "PASS", "reason": "test", "criteria": {
            key: "MATCH" for key in ("subject", "composition", "color", "background")}}
        with patch.object(challenges, "load_settings", return_value=self.settings), \
             patch.object(challenges, "Store", return_value=self.store), \
             patch.object(challenges, "route_verdict", return_value=result) as provider, \
             patch.object(self.store, "finalize", return_value={"id": "saved"}) as save:
            client = TestClient(main.app)
            headers = {"X-Challenge-Token": "session-token"}
            response = client.post(f"/api/challenges/{cid}/evaluate", headers=headers,
                json={"attempt_id": aid, "generated_image": self.data, "defer_receipt": True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["receipt_pending"])
            self.assertEqual(self.store.attempt(cid, aid)["router_verdict"], "PASS")
            save.assert_not_called()
            saved = client.post(f"/api/challenges/{cid}/finalize", headers=headers,
                json={"attempt_id": aid, "generated_image": self.data, "source": "router"})
            self.assertEqual(saved.json()["id"], "saved")
            self.assertEqual(provider.call_count, 1)
            save.assert_called_once()

    def test_usage_write_failure_does_not_retry_paid_request(self):
        recorder = Mock()
        recorder.finish.side_effect = PersistenceError("SUPABASE_UNAVAILABLE")
        provider = Mock(return_value=object())
        with self.assertRaises(PersistenceError): _with_retry(provider, recorder=recorder, delay=0)
        self.assertEqual(provider.call_count, 1)

    def test_pending_receipt_retry_reuses_cached_router_pass(self):
        cid, aid = str(uuid4()), str(uuid4())
        self.store.create_challenge(self.raw, "image/png")
        self.store.tables["challenges"][0].update(id=cid, target_image_path=cid+"/target")
        self.store.insert("attempts", {"id": aid, "challenge_id": cid, "prompt": "cached"})
        self.store.update("attempts", aid, {"generation_status": "ready", "generated_image_sha256": digest(self.raw),
            "router_verdict": "PASS", "evaluation_result": {"verdict": "PASS", "reason": "cached"}})
        with patch.object(challenges, "load_settings", return_value=self.settings), \
             patch.object(challenges, "Store", return_value=self.store), \
             patch.object(challenges, "route_verdict") as provider, \
             patch.object(self.store, "finalize", side_effect=[PersistenceError("SUPABASE_UNAVAILABLE"), {"id": "saved"}]):
            client = TestClient(main.app)
            url = f"/api/challenges/{cid}/evaluate"
            args = {"json": {"attempt_id": aid, "generated_image": self.data}, "headers": {"X-Challenge-Token": "session-token"}}
            first = client.post(url, **args).json()
            self.assertEqual(first["receipt_error"], "SUPABASE_UNAVAILABLE")
            second = client.post(url, **args).json()
            self.assertEqual(second["receipt"]["id"], "saved")
            provider.assert_not_called()

    def test_no_provider_call_if_ledger_start_fails(self):
        recorder = Mock()
        recorder.start.side_effect = PersistenceError("SUPABASE_UNAVAILABLE")
        provider = Mock()
        with self.assertRaises(PersistenceError): _with_retry(provider, recorder=recorder)
        provider.assert_not_called()

    def test_receipt_checks_actual_image_and_per_model_usage(self):
        settings = Settings(**{**self.settings.__dict__, "supabase_url": "https://example.supabase.co", "supabase_secret_key": "sb_secret_fake"})
        store = Store(settings)
        cid, aid = str(uuid4()), str(uuid4())
        calls = [{"id": "call", "provider": "gemini", "model": "image-test", "operation": "generate",
                  "status": "succeeded", "input_tokens": 20, "output_tokens": 70, "thought_tokens": 10, "total_tokens": 100}]
        usage = {"calls": 1, "generation_calls": 1, "router_calls": 0, "failed_calls": 0, "unknown_calls": 0,
                 "known_input_tokens": 20, "known_output_tokens": 70, "known_thought_tokens": 10, "known_total_tokens": 100,
                 **{"calls_without_" + f: 0 for f in ("input_tokens", "output_tokens", "thought_tokens", "total_tokens")}}
        saved = {"id": str(uuid4()), "challenge_id": cid, "attempt_id": aid, "prompt": "original", "image_path": cid+"/"+aid,
                 "model_calls_total": 1, "generation_calls": 1, "router_calls": 0, "failed_calls": 0,
                 "known_tokens_total": 100, "calls_without_total_tokens": 0, "tokens_by_model": {"gemini/image-test": usage}}
        def rows(table, **filters):
            return [saved] if table == "successes" else [{"prompt": "original", "generated_image_sha256": digest(self.raw)}]
        with patch.object(store, "rows", side_effect=rows), patch.object(store, "calls", return_value=calls), \
             patch.object(store, "download", return_value=self.raw), patch.object(store, "request", return_value=NS(json=lambda: {"signedURL": "/object/sign/test"})):
            self.assertTrue(store.receipt(cid)["verified"])
            usage["known_output_tokens"] = 999
            result = store.receipt(cid)
            self.assertFalse(result["verified"])
            self.assertFalse(result["verification"]["models_match"])
