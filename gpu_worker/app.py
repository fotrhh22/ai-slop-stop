"""One process, one GPU, one generation window. Run with uvicorn --workers 1.

Persistent request IDs prevent a network retry from repeating generation.
Do not expose this service without GPU_API_KEY and an HTTPS reverse proxy.
"""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import fcntl
from importlib.metadata import version
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from time import perf_counter
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from server.energy import EnergyMeasurement

log = logging.getLogger("uvicorn.error")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class RequestLedger:
    """Single worker owns the file. Running at restart becomes unknown, never retried."""
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("create table if not exists requests (id text primary key, fingerprint text not null, result text not null)")
            for rid, value in db.execute("select id,result from requests").fetchall():
                if json.loads(value)["status"] == "running":
                    self._save(db, rid, {"request_id": rid, "status": "unknown", "error": "GPU_WORKER_RESTARTED"})

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def _save(db, rid, result):
        db.execute("update requests set result=? where id=?", (json.dumps(result), rid))

    def existing(self, rid, fingerprint):
        with self.connect() as db:
            row = db.execute("select fingerprint,result from requests where id=?", (rid,)).fetchone()
        if not row:
            return None
        if not secrets.compare_digest(row[0], fingerprint):
            raise HTTPException(409, "GPU_REQUEST_ID_CONFLICT")
        result = json.loads(row[1])
        if result["status"] == "running":
            raise HTTPException(409, "GPU_REQUEST_RUNNING")
        return result

    def start(self, rid, fingerprint):
        with self.connect() as db:
            db.execute("insert into requests values(?,?,?)", (rid, fingerprint,
                json.dumps({"request_id": rid, "status": "running"})))

    def finish(self, rid, result):
        with self.connect() as db:
            self._save(db, rid, result)


class GenerateRequest(BaseModel):
    request_id: UUID
    prompt: str = Field(min_length=1, max_length=2000)


class SDXLRunner:
    def __init__(self):
        # Imports stay out of the HTTP module so CPU-only contract tests can run.
        import torch
        import pynvml
        from diffusers import StableDiffusionXLPipeline
        from zeus.monitor import ZeusMonitor
        self.torch = torch
        self.model_id = os.environ.get("GPU_IMAGE_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")
        self.revision = os.environ.get("GPU_MODEL_REVISION", "")
        if not re.fullmatch(r"[a-f0-9]{40}", self.revision):
            raise RuntimeError("GPU_MODEL_REVISION must be an immutable Hugging Face commit SHA")
        if torch.cuda.device_count() != 1:
            raise RuntimeError("Expose exactly one dedicated GPU with CUDA_VISIBLE_DEVICES")
        self.width = self.height = 1024
        self.steps = 30
        self.pipe = StableDiffusionXLPipeline.from_pretrained(self.model_id,
            revision=self.revision, torch_dtype=torch.float16, use_safetensors=True).to("cuda")
        self.pipe.set_progress_bar_config(disable=True)
        # Warm-up is outside every participant's measurement window.
        with torch.inference_mode():
            self.pipe("a red bicycle", width=self.width, height=self.height, num_inference_steps=2)
        torch.cuda.synchronize()
        self.monitor = ZeusMonitor(gpu_indices=[0])
        pynvml.nvmlInit()
        device = torch.cuda.get_device_properties(0)
        # Match UUID, not physical index 0: container device numbering may differ.
        device_uuid = str(device.uuid)
        if not device_uuid.startswith("GPU-"):
            device_uuid = "GPU-" + device_uuid
        handle = pynvml.nvmlDeviceGetHandleByUUID(device_uuid)
        self.gpu_name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(self.gpu_name, bytes):
            self.gpu_name = self.gpu_name.decode()
        self.gpu_uuid = device_uuid
        self.zeus_version = version("zeus")
        # Establish energy-counter support before marking the worker ready.
        self.monitor.begin_window("probe")
        with torch.inference_mode():
            self.pipe("a red bicycle", width=1024, height=1024, num_inference_steps=5)
        probe = self.monitor.end_window("probe")
        if not math.isfinite(probe.total_energy) or probe.total_energy <= 0:
            raise RuntimeError("GPU energy probe failed; do not serve unverified telemetry")

    def run(self, request):
        rid, seed = str(request.request_id), secrets.randbelow(2**31)
        start, wall = utcnow(), perf_counter()
        energy, image, error = None, None, None
        window_open = False
        try:
            self.monitor.begin_window(rid)
            window_open = True
            with self.torch.inference_mode():
                image = self.pipe(request.prompt, width=self.width, height=self.height,
                    num_inference_steps=self.steps, guidance_scale=5.0,
                    generator=self.torch.Generator(device="cuda").manual_seed(seed)).images[0]
        except Exception:
            error = "GPU_GENERATION_FAILED"
            log.exception("GPU generation failed request_id=%s", rid)
        finally:
            if window_open:
                try:
                    energy = self.monitor.end_window(rid)
                except Exception:
                    log.exception("GPU measurement failed request_id=%s", rid)
        end, duration = utcnow(), perf_counter() - wall
        joules = energy.total_energy if energy else None
        valid = joules is not None and math.isfinite(joules) and joules > 0
        measurement = EnergyMeasurement(measurement_id=request.request_id,
            status="measured" if valid else "unavailable", energy_j=joules if valid else None,
            duration_s=energy.time if energy else duration,
            reason=None if valid else "ENERGY_COUNTER_UNAVAILABLE_OR_WINDOW_TOO_SHORT",
            gpu_name=self.gpu_name, gpu_uuid=self.gpu_uuid, model_id=self.model_id,
            model_revision=self.revision, zeus_version=self.zeus_version,
            width=self.width, height=self.height, steps=self.steps, seed=seed,
            started_at=start, finished_at=end)
        result = {"request_id": rid, "status": "failed" if error else "succeeded",
                  "energy": measurement.model_dump(mode="json")}
        if error:
            return {**result, "error": error}
        # PNG encoding is CPU work and intentionally outside the GPU window.
        out = io.BytesIO()
        image.save(out, format="PNG")
        raw = out.getvalue()
        return {**result, "image_url": "data:image/png;base64," + base64.b64encode(raw).decode(),
                "image_sha256": hashlib.sha256(raw).hexdigest()}


def create_app(runner=None, ledger_path=None, api_key=None):
    key = api_key if api_key is not None else os.environ.get("GPU_API_KEY", "")
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if len(key) < 32:
            raise RuntimeError("GPU_API_KEY must have at least 32 characters")
        path = str(ledger_path or os.environ.get("GPU_LEDGER_PATH", "/workspace/ai-slop/requests.sqlite3"))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Stop a second Uvicorn process before it touches the journal or GPU.
        with open(path + ".lock", "a") as owner:
            try:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("GPU worker already running; use --workers 1") from exc
            app.state.ledger = RequestLedger(path)
            app.state.runner = runner or SDXLRunner()
            yield

    app = FastAPI(title="AI SLOP STOP GPU worker", lifespan=lifespan)

    def authorize(authorization: str = Header(default="")):
        if len(key) < 32 or not secrets.compare_digest(authorization, "Bearer " + key):
            raise HTTPException(403, "GPU_AUTH_DENIED")

    @app.get("/health", dependencies=[Depends(authorize)])
    def health():
        return {"ok": True, "provider": "zeus", "scope": "gpu_board_generation_window"}

    @app.post("/v1/generate", dependencies=[Depends(authorize)])
    def generate(request: GenerateRequest):
        rid = str(request.request_id)
        if not request.prompt.strip():
            raise HTTPException(400, "EMPTY_PROMPT")
        fingerprint = hashlib.sha256(request.prompt.encode()).hexdigest()
        cached = app.state.ledger.existing(rid, fingerprint)
        if cached:
            return cached
        if not lock.acquire(blocking=False):
            raise HTTPException(429, "GPU_BUSY")
        try:
            cached = app.state.ledger.existing(rid, fingerprint)
            if cached:
                return cached
            app.state.ledger.start(rid, fingerprint)
            try:
                result = app.state.runner.run(request)
            except Exception:
                log.exception("Worker failed request_id=%s", rid)
                result = {"request_id": rid, "status": "unknown", "error": "GPU_RESULT_UNCONFIRMED"}
            app.state.ledger.finish(rid, result)
            return result
        finally:
            lock.release()

    @app.get("/v1/requests/{request_id}", dependencies=[Depends(authorize)])
    def request_status(request_id: UUID):
        with app.state.ledger.connect() as db:
            row = db.execute("select result from requests where id=?", (str(request_id),)).fetchone()
        if not row:
            raise HTTPException(404, "GPU_REQUEST_NOT_FOUND")
        return json.loads(row[0])

    return app


app = create_app()
