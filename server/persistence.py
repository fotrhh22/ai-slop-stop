"""Server-only Supabase REST/Storage and immutable success receipt readback."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from functools import lru_cache
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from .config import Settings


class PersistenceError(Exception):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.status = status


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@lru_cache(maxsize=1)
def _http_client():
    # httpx.Client is thread-safe; reuse TLS connections across workflow requests.
    # Credentials remain per request so different Store instances cannot mix keys.
    return httpx.Client(timeout=30)


class Store:
    def __init__(self, settings: Settings):
        if not settings.supabase_url or not settings.supabase_secret_key:
            raise PersistenceError("SUPABASE_NOT_CONFIGURED")
        self.base = settings.supabase_url.rstrip("/")
        self.headers = {"apikey": settings.supabase_secret_key}
        if settings.supabase_secret_key.startswith("eyJ"):
            self.headers["Authorization"] = "Bearer " + settings.supabase_secret_key

    def request(self, method, path, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}
        try:
            response = _http_client().request(method, self.base + path, headers=headers, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json()
            except ValueError:
                detail = {}
            if detail.get("code") in ("42703", "42P01", "PGRST204", "PGRST202"):
                raise PersistenceError("SUPABASE_SCHEMA_UPDATE_REQUIRED") from exc
            if detail.get("message") in ("MODEL_CALLS_STILL_PENDING", "ROUTER_HAS_NOT_PASSED", "CHALLENGE_NOT_ACTIVE"):
                raise PersistenceError(detail["message"], 409) from exc
            if exc.response.status_code in (401, 403):
                raise PersistenceError("SUPABASE_SERVER_KEY_INVALID") from exc
            if exc.response.status_code == 404 and path.startswith("/storage/"):
                raise PersistenceError("STORAGE_BUCKET_OR_OBJECT_NOT_FOUND") from exc
            if exc.response.status_code == 409:
                raise PersistenceError("RECORD_ALREADY_EXISTS", 409) from exc
            raise PersistenceError("SUPABASE_REQUEST_FAILED") from exc
        except httpx.RequestError as exc:
            raise PersistenceError("SUPABASE_UNAVAILABLE") from exc
        return response

    def rows(self, table, **filters):
        return self.request("GET", "/rest/v1/" + table,
                            params={"select": "*", **filters}).json()

    def insert(self, table, data):
        rows = self.request("POST", "/rest/v1/" + table, json=data,
                            headers={"Prefer": "return=representation"}).json()
        return rows[0]

    def update(self, table, row_id, data):
        self.request("PATCH", "/rest/v1/" + table, params={"id": "eq." + row_id}, json=data)

    def upload(self, bucket, path, raw, mime):
        self.request("POST", f"/storage/v1/object/{bucket}/{path}", content=raw,
                     headers={"Content-Type": mime, "x-upsert": "true"})

    def download(self, bucket, path):
        return self.request("GET", f"/storage/v1/object/authenticated/{bucket}/{path}").content

    def create_challenge(self, raw, mime):
        cid, token = str(uuid4()), secrets.token_urlsafe(32)
        path = cid + "/target"
        self.upload("challenge-targets", path, raw, mime)
        self.insert("challenges", {"id": cid, "target_image_path": path,
                                   "access_token_hash": digest(token.encode())})
        return {"id": cid, "accessToken": token}

    def authorize(self, cid, token):
        rows = self.rows("challenges", id="eq." + cid)
        if not rows or not token or not hmac.compare_digest(
            rows[0].get("access_token_hash") or "", digest(token.encode())
        ):
            raise PersistenceError("CHALLENGE_ACCESS_DENIED", 403)
        return rows[0]

    def attempt(self, cid, aid):
        rows = self.rows("attempts", id="eq." + aid, challenge_id="eq." + cid)
        if not rows:
            raise PersistenceError("ATTEMPT_NOT_FOUND", 404)
        return rows[0]

    def calls(self, cid):
        # Paginate: PostgREST normally caps a response at 1000 records.
        result, offset = [], 0
        while True:
            page = self.rows("api_calls", challenge_id="eq." + cid, order="started_at.asc,id.asc",
                             limit="500", offset=str(offset))
            result.extend(page)
            if len(page) < 500:
                return result
            offset += len(page)

    def receipt(self, cid):
        rows = self.rows("successes", challenge_id="eq." + cid)
        if not rows:
            return None
        saved = rows[0]
        attempt = self.attempt(cid, saved["attempt_id"])
        calls = self.calls(cid)
        image = self.download("challenge-successes", saved["image_path"])
        verification = {
            "prompt_matches": saved["prompt"] == attempt["prompt"],
            "image_matches": digest(image) == attempt.get("generated_image_sha256"),
            "calls_match": saved["model_calls_total"] == len(calls),
            "generation_calls_match": saved["generation_calls"] == sum(c["operation"] == "generate" for c in calls),
            "router_calls_match": saved["router_calls"] == sum(c["operation"] == "router" for c in calls),
            "known_tokens_match": saved["known_tokens_total"] == sum(c.get("total_tokens") or 0 for c in calls),
            "missing_usage_matches": saved["calls_without_total_tokens"] == sum(c.get("total_tokens") is None for c in calls),
            "failed_calls_match": saved["failed_calls"] == sum(c["status"] == "failed" for c in calls),
            "no_pending_calls": all(c["status"] != "pending" for c in calls),
        }
        # Verify per-model values too; never label browser state as DB readback.
        by_model = {}
        for call in calls:
            key = call["provider"] + "/" + call["model"]
            values = by_model.setdefault(key, {"calls": 0, "generation_calls": 0, "router_calls": 0,
                "failed_calls": 0, "unknown_calls": 0, **{
                    prefix + field: 0 for field in ("input_tokens", "output_tokens", "thought_tokens", "total_tokens")
                    for prefix in ("known_", "calls_without_")}})
            values["calls"] += 1
            values["generation_calls"] += call["operation"] == "generate"
            values["router_calls"] += call["operation"] == "router"
            values["failed_calls"] += call["status"] == "failed"
            values["unknown_calls"] += call["status"] == "unknown"
            for field in ("input_tokens", "output_tokens", "thought_tokens", "total_tokens"):
                values["known_" + field] += call.get(field) or 0
                values["calls_without_" + field] += call.get(field) is None
        verification["models_match"] = by_model == saved["tokens_by_model"]
        signed = self.request("POST", "/storage/v1/object/sign/challenge-successes/" + saved["image_path"],
                              json={"expiresIn": 3600}).json()
        image_url = signed.get("signedURL") or signed.get("signedUrl")
        if not image_url:
            raise PersistenceError("RECEIPT_IMAGE_URL_FAILED")
        if not image_url.startswith("https://"):
            image_url = self.base + ("" if image_url.startswith("/storage/v1/") else "/storage/v1") + image_url
        return {**saved, "image_url": image_url, "verification": verification,
                "verified": all(verification.values()), "source": "supabase",
                "usage_complete": saved["calls_without_total_tokens"] == 0,
                "calls": [{k: c.get(k) for k in (
                    "id", "operation", "model", "status", "input_tokens", "output_tokens",
                    "thought_tokens", "total_tokens", "started_at"
                )} for c in calls]}

    def finalize(self, cid, aid, raw, mime, source, actor=None, reason=None):
        existing = self.receipt(cid)
        if existing:
            return existing
        attempt = self.attempt(cid, aid)
        if digest(raw) != attempt.get("generated_image_sha256"):
            raise PersistenceError("GENERATED_IMAGE_MISMATCH", 400)
        if source == "router" and attempt.get("router_verdict") != "PASS":
            raise PersistenceError("ROUTER_HAS_NOT_PASSED", 409)
        path = cid + "/" + aid
        self.upload("challenge-successes", path, raw, mime)
        self.request("POST", "/rest/v1/rpc/finalize_challenge", json={
            "p_challenge_id": cid, "p_attempt_id": aid, "p_image_path": path,
            "p_pass_source": source, "p_staff_actor": actor, "p_staff_reason": reason,
        })
        receipt = self.receipt(cid)
        if not receipt:
            raise PersistenceError("SUCCESS_READBACK_FAILED")
        return receipt


class CallRecorder:
    def __init__(self, store, cid, aid, operation, model):
        self.store, self.cid, self.aid = store, cid, aid
        self.operation, self.model = operation, model

    def start(self):
        call_id = str(uuid4())
        self.store.insert("api_calls", {"id": call_id, "challenge_id": self.cid,
            "attempt_id": self.aid, "operation": self.operation, "model": self.model})
        return call_id

    def finish(self, call_id, response=None, error=None):
        usage = getattr(response, "usage_metadata", None) if response is not None else None
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump(mode="json", exclude_none=True)
        usage = usage if isinstance(usage, dict) else {}
        data = {"status": "failed" if error else "succeeded",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "usage_metadata": usage,
                "error_code": type(error).__name__ if error else None}
        for field, source in [("input_tokens", "prompt_token_count"), ("output_tokens", "candidates_token_count"),
                              ("thought_tokens", "thoughts_token_count"), ("total_tokens", "total_token_count")]:
            value = usage.get(source)
            data[field] = value if type(value) is int and value >= 0 else None
        actual_model = getattr(response, "model_version", None) if response is not None else None
        if isinstance(actual_model, str) and actual_model:
            data["model"] = actual_model
        self.store.update("api_calls", call_id, data)
