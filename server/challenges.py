"""Persisted challenge workflow. Single Render worker serializes each session."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from time import perf_counter
import threading
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .config import load_settings
from .evaluation_jobs import jobs
from .gemini_client import GeminiError, decode_data_url, generate_image, route_verdict
from .persistence import CallRecorder, PersistenceError, Store, digest
from .images import ImageError, validate_image

router = APIRouter(prefix="/api/challenges")
_locks = [threading.Lock() for _ in range(64)]
_logger = logging.getLogger("uvicorn.error")


@contextmanager
def evaluation_stage(name):
    start = perf_counter()
    try:
        yield
    finally:
        _logger.info("evaluation stage=%s elapsed_ms=%.0f", name, (perf_counter() - start) * 1000)



def session(cid, token):
    settings = load_settings()
    store = Store(settings)
    challenge = store.authorize(str(cid), token)
    return settings, store, challenge


def image(data_url):
    try:
        raw, mime = decode_data_url(data_url)
        validate_image(raw)
        return raw, mime
    except (GeminiError, ImageError) as exc:
        raise HTTPException(400, str(exc).split(":")[0]) from exc


class CreateChallenge(BaseModel):
    target_image: str


@router.post("")
def create(req: CreateChallenge):
    raw, mime = image(req.target_image)
    return Store(load_settings()).create_challenge(raw, mime)


class Generate(BaseModel):
    attempt_id: UUID
    prompt: str = Field(min_length=1, max_length=2000)


@router.post("/{cid}/generate")
def generate(cid: UUID, req: Generate, x_challenge_token: str = Header(default="")):
    with _locks[cid.int % len(_locks)]:
        settings, store, challenge = session(cid, x_challenge_token)
        cid, aid = str(cid), str(req.attempt_id)
        if challenge["status"] != "active":
            raise HTTPException(409, "CHALLENGE_COMPLETED")
        if not req.prompt.strip():
            raise HTTPException(400, "EMPTY_PROMPT")
        if store.rows("attempts", id="eq." + aid):
            raise HTTPException(409, "ATTEMPT_ALREADY_EXISTS")
        if store.rows("attempts", challenge_id="eq." + cid, router_verdict="eq.PASS"):
            raise HTTPException(409, "SUCCESS_SAVE_PENDING")
        store.insert("attempts", {"id": aid, "challenge_id": cid, "prompt": req.prompt})
        try:
            result = generate_image(settings, req.prompt,
                recorder=CallRecorder(store, cid, aid, "generate", settings.image_model))
        except GeminiError as exc:
            store.update("attempts", aid, {"generation_status": "failed"})
            raise HTTPException(502, str(exc).split(":")[0]) from exc
        raw, _ = image(result)
        store.update("attempts", aid, {"generation_status": "ready", "generated_image_sha256": digest(raw)})
        return {"imageUrl": result}


class Evaluate(BaseModel):
    attempt_id: UUID
    generated_image: str
    defer_receipt: bool = False


def verify_image(store, cid, aid, data_url):
    attempt = store.attempt(cid, aid)
    raw, mime = image(data_url)
    if attempt["generation_status"] != "ready" or digest(raw) != attempt.get("generated_image_sha256"):
        raise HTTPException(400, "GENERATED_IMAGE_MISMATCH")
    return attempt, raw, mime


@router.post("/{cid}/evaluate")
def evaluate(cid: UUID, req: Evaluate, x_challenge_token: str = Header(default="")):
    with _locks[cid.int % len(_locks)]:
        with evaluation_stage("authorize"):
            settings, store, challenge = session(cid, x_challenge_token)
        cid, aid = str(cid), str(req.attempt_id)
        with evaluation_stage("verify_image"):
            attempt, raw, mime = verify_image(store, cid, aid, req.generated_image)
        existing = store.receipt(cid) if challenge["status"] == "succeeded" else None
        if existing:
            if existing["attempt_id"] != aid:
                raise HTTPException(409, "CHALLENGE_COMPLETED")
            return {**(attempt.get("evaluation_result") or {}), "verdict": "PASS", "receipt": existing}
        if challenge["status"] != "active":
            raise HTTPException(409, "CHALLENGE_COMPLETED")
        result = attempt.get("evaluation_result")
        # A failed success-save must not trigger another paid Router call.
        if attempt.get("router_verdict") != "PASS" or not result:
            with evaluation_stage("download_target"):
                target = store.download("challenge-targets", challenge["target_image_path"])
            target_mime = "image/png" if target.startswith(b"\x89PNG") else "image/webp" if target[8:12] == b"WEBP" else "image/jpeg"
            try:
                with evaluation_stage("router"):
                    verdict = route_verdict(settings, target, target_mime, raw, mime,
                        recorder=CallRecorder(store, cid, aid, "router", settings.router_model))
            except GeminiError as exc:
                raise HTTPException(502, str(exc).split(":")[0]) from exc
            result = {"router_model": settings.router_model, **verdict}
            with evaluation_stage("save_verdict"):
                store.update("attempts", aid, {"router_verdict": verdict["verdict"],
                    "router_reason": verdict["reason"], "evaluation_result": result})
        if result["verdict"] == "PASS":
            if req.defer_receipt:
                return {**result, "receipt_pending": True}
            try:
                return {**result, "receipt": store.finalize(cid, aid, raw, mime, "router")}
            except PersistenceError as exc:
                return {**result, "receipt_error": str(exc)}
        return result


class StartEvaluation(Evaluate):
    job_id: UUID


@router.post("/{cid}/evaluations", status_code=202)
def start_evaluation(cid: UUID, req: StartEvaluation, x_challenge_token: str = Header(default="")):
    # Do not take the session lock here: a retransmitted request must return
    # immediately even while the previous request is waiting on Gemini.
    _, store, _ = session(cid, x_challenge_token)
    store.attempt(str(cid), str(req.attempt_id))
    evaluate_req = Evaluate(attempt_id=req.attempt_id, generated_image=req.generated_image, defer_receipt=True)
    return jobs.start(str(req.job_id), str(cid), str(req.attempt_id), x_challenge_token,
                      lambda: evaluate(cid, evaluate_req, x_challenge_token))


@router.get("/{cid}/evaluations/{job_id}")
def evaluation_status(cid: UUID, job_id: UUID, x_challenge_token: str = Header(default="")):
    # The job remembers the hash of the already-authorized session token.
    # Polling needs no DB query and cannot invoke a model.
    return jobs.status(str(job_id), str(cid), x_challenge_token)


class Finalize(Evaluate):
    source: str = Field(pattern="^(router|staff)$")


@router.post("/{cid}/finalize")
def finalize(cid: UUID, req: Finalize, x_challenge_token: str = Header(default="")):
    with _locks[cid.int % len(_locks)]:
        _, store, _ = session(cid, x_challenge_token)
        cid, aid = str(cid), str(req.attempt_id)
        _, raw, mime = verify_image(store, cid, aid, req.generated_image)
        # Public STAFF action: no operator authentication or approval form.
        # Supply truthful defaults for the existing receipt schema, not a user identity.
        actor = "이름 미입력" if req.source == "staff" else None
        reason = "STAFF 버튼으로 수동 승인" if req.source == "staff" else None
        return store.finalize(cid, aid, raw, mime, req.source, actor, reason)


@router.get("/{cid}/receipt")
def receipt(cid: UUID, x_challenge_token: str = Header(default="")):
    _, store, _ = session(cid, x_challenge_token)
    saved = store.receipt(str(cid))
    if not saved:
        raise HTTPException(404, "RECEIPT_NOT_FOUND")
    return saved


@router.get("/{cid}/receipt/image")
def receipt_image(cid: UUID, x_challenge_token: str = Header(default="")):
    # Download only the stored success image, never a browser-supplied remote URL.
    _, store, _ = session(cid, x_challenge_token)
    saved = store.rows("successes", challenge_id="eq." + str(cid))
    if not saved:
        raise HTTPException(404, "RECEIPT_NOT_FOUND")
    attempt = store.attempt(str(cid), saved[0]["attempt_id"])
    raw = store.download("challenge-successes", saved[0]["image_path"])
    if digest(raw) != attempt.get("generated_image_sha256"):
        raise HTTPException(409, "SUCCESS_IMAGE_MISMATCH")
    mime = "image/png" if raw.startswith(b"\x89PNG") else "image/webp" if raw[8:12] == b"WEBP" else "image/jpeg"
    return Response(raw, media_type=mime, headers={"Cache-Control": "no-store"})
