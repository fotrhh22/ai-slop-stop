"""Small, bounded rendezvous for an outbound-only local CLIP worker.

Matches the existing single-process evaluation jobs: no torch, disk images,
public Mac endpoint, or database credentials on the Mac are required.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hmac
import math
import threading
from time import monotonic, perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .config import load_settings
from .images import evaluation_image
from .persistence import digest

MODEL = "openai/ViT-B/32"
PIPELINE = "clip-rgb-exif-white-1024-v1"
router = APIRouter(prefix="/api/clip-worker")


@dataclass
class Task:
    payload: dict
    deadline: float
    lease: str | None = None
    result: dict | None = None
    done: threading.Event = field(default_factory=threading.Event)


class Broker:
    def __init__(self, capacity=2):
        self.lock = threading.Lock()
        self.tasks: dict[str, Task] = {}
        self.capacity = capacity

    def compute(self, payload, timeout):
        jid = str(uuid4())
        task = Task(payload, monotonic() + timeout)
        with self.lock:
            if len(self.tasks) >= self.capacity:
                raise HTTPException(503, "CLIP_BUSY")
            self.tasks[jid] = task
        try:
            if not task.done.wait(timeout):
                raise HTTPException(503, "CLIP_TIMEOUT")
            if task.result.get("error"):
                raise HTTPException(503, "CLIP_WORKER_FAILED")
            return task.result
        finally:
            with self.lock:
                self.tasks.pop(jid, None)

    def claim(self):
        with self.lock:
            for jid, task in self.tasks.items():
                if task.lease is None and monotonic() < task.deadline:
                    task.lease = str(uuid4())
                    return {"id": jid, "lease_token": task.lease, **task.payload}
        return None

    def complete(self, jid, lease, result):
        with self.lock:
            task = self.tasks.get(jid)
            if (task is None or task.lease is None or monotonic() >= task.deadline
                    or not hmac.compare_digest(task.lease, lease)):
                raise HTTPException(409, "CLIP_LEASE_LOST")
            if task.result is not None:
                if task.result != result:
                    raise HTTPException(409, "CLIP_RESULT_CONFLICT")
                return
            task.result = result
            task.done.set()


broker = Broker()


def authorize(token):
    expected = load_settings().clip_worker_token
    if len(expected) < 32:
        raise HTTPException(503, "CLIP_NOT_CONFIGURED")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(403, "CLIP_AUTH_DENIED")


@router.post("/claim")
def claim(x_clip_worker_token: str = Header(default="")):
    authorize(x_clip_worker_token)
    return {"job": broker.claim()}


class Complete(BaseModel):
    lease_token: UUID
    model: str = Field(pattern=r"^openai/ViT-B/32$")
    pipeline: str = Field(pattern=r"^clip-rgb-exif-white-1024-v1$")
    cosine: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    elapsed_ms: float = Field(ge=0, le=3_600_000, allow_inf_nan=False)
    error: str | None = Field(default=None, pattern="^INFERENCE_FAILED$")


@router.post("/jobs/{jid}/complete")
def complete(jid: UUID, req: Complete, x_clip_worker_token: str = Header(default="")):
    authorize(x_clip_worker_token)
    if (req.cosine is None) == (req.error is None):
        raise HTTPException(400, "CLIP_BAD_RESULT")
    result = req.model_dump(exclude={"lease_token"}, exclude_none=True)
    broker.complete(str(jid), str(req.lease_token), result)
    return {"ok": True}


def evaluate_with_clip(settings, target, target_mime, generated, generated_mime, route, recorder=None):
    """Observe first; only an explicitly configured gate can skip the paid call."""
    if settings.clip_mode == "off":
        return route(settings, target, target_mime, generated, generated_mime, recorder=recorder)
    if (settings.clip_mode not in ("observe", "gate")
            or settings.clip_failure_policy not in ("fallback", "required")
            or not math.isfinite(settings.clip_timeout) or not 0 < settings.clip_timeout <= 60
            or len(settings.clip_worker_token) < 32
            or (settings.clip_mode == "gate" and (settings.clip_reject_below is None
                or not -1 <= settings.clip_reject_below <= 1))):
        raise HTTPException(503, "CLIP_BAD_CONFIG")
    started = perf_counter()
    payload = {"model": MODEL, "pipeline": PIPELINE}
    # A fixed transport pipeline is shared by both images. Raw hashes retain
    # correspondence with originals even when transport images are compressed.
    for name, raw, mime in (("target", target, target_mime), ("generated", generated, generated_mime)):
        compact, _ = evaluation_image(raw, mime)
        payload[name + "_base64"] = base64.b64encode(compact).decode("ascii")
        payload[name + "_sha256"] = digest(raw)
    try:
        similarity = {**broker.compute(payload, settings.clip_timeout),
                      "target_sha256": digest(target), "generated_sha256": digest(generated)}
    except HTTPException as exc:
        if settings.clip_failure_policy == "required":
            raise
        similarity = {"status": "unavailable", "error": str(exc.detail)}
    similarity["roundtrip_ms"] = round((perf_counter() - started) * 1000, 1)
    if (settings.clip_mode == "gate" and "cosine" in similarity
            and similarity["cosine"] < settings.clip_reject_below):
        return {"verdict": "FAIL", "reason": "목표 이미지와 전체적인 유사도가 낮습니다. 피사체와 구도를 조정해 보세요.",
                "evaluation_source": "clip_gate", "router_called": False,
                "similarity": similarity, "clip_reject_below": settings.clip_reject_below}
    result = route(settings, target, target_mime, generated, generated_mime, recorder=recorder)
    return {**result, "similarity": similarity, "router_called": True, "evaluation_source": "gemini_vision"}
