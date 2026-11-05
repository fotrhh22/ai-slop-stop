"""One process, one or two GPUs, one generation window. Run with uvicorn --workers 1.

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


def model_device(torch, role):
    count = torch.cuda.device_count()
    if count not in (1, 2):
        raise RuntimeError("One or two dedicated CUDA GPUs are required")
    default = 1 if role == "ROUTER" and count == 2 else 0
    index = int(os.environ.get("GPU_" + role + "_DEVICE", str(default)))
    if not 0 <= index < count:
        raise RuntimeError("Configured model GPU is unavailable")
    if count == 2:
        other_role = "IMAGE" if role == "ROUTER" else "ROUTER"
        other_default = 0 if other_role == "IMAGE" else 1
        other = int(os.environ.get("GPU_" + other_role + "_DEVICE", str(other_default)))
        if index == other:
            raise RuntimeError("Two-GPU mode requires separate image and router devices")
    torch.cuda.set_device(index)
    return index, "cuda:" + str(index)


class FluxRunner:
    def __init__(self):
        # Imports stay out of the HTTP module so CPU-only contract tests can run.
        import torch
        import pynvml
        from diffusers import Flux2KleinPipeline
        from zeus.monitor import ZeusMonitor
        self.torch = torch
        self.model_id = os.environ.get("GPU_IMAGE_MODEL", "black-forest-labs/FLUX.2-klein-4B")
        self.revision = os.environ.get("GPU_MODEL_REVISION", "")
        if not re.fullmatch(r"[a-f0-9]{40}", self.revision):
            raise RuntimeError("GPU_MODEL_REVISION must be an immutable Hugging Face commit SHA")
        self.gpu_index, self.device = model_device(torch, "IMAGE")
        self.width = self.height = 1024
        self.steps = 4
        self.pipe = Flux2KleinPipeline.from_pretrained(self.model_id,
            revision=self.revision, torch_dtype=torch.bfloat16, use_safetensors=True).to(self.device)
        self.pipe.set_progress_bar_config(disable=True)
        # Warm-up is outside every participant's measurement window.
        with torch.inference_mode():
            self.pipe(prompt="a red bicycle", width=self.width, height=self.height, num_inference_steps=4, guidance_scale=1.0)
        torch.cuda.synchronize(self.gpu_index)
        self.monitor = ZeusMonitor(gpu_indices=[self.gpu_index])
        pynvml.nvmlInit()
        device = torch.cuda.get_device_properties(self.gpu_index)
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
            self.pipe(prompt="a red bicycle", width=1024, height=1024, num_inference_steps=4, guidance_scale=1.0)
        probe = self.monitor.end_window("probe")
        if not math.isfinite(probe.total_energy) or probe.total_energy <= 0:
            raise RuntimeError("GPU energy probe failed; do not serve unverified telemetry")

    def run(self, request):
        self.torch.cuda.set_device(self.gpu_index)
        rid, seed = str(request.request_id), secrets.randbelow(2**31)
        start, wall = utcnow(), perf_counter()
        energy, image, error = None, None, None
        window_open = False
        try:
            self.monitor.begin_window(rid)
            window_open = True
            with self.torch.inference_mode():
                image = self.pipe(prompt=request.prompt, width=self.width, height=self.height,
                    num_inference_steps=self.steps, guidance_scale=1.0,
                    generator=self.torch.Generator(device=self.device).manual_seed(seed)).images[0]
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


class RouteRequest(BaseModel):
    request_id: UUID
    target_image: str = Field(max_length=4_000_000)
    generated_image: str = Field(max_length=4_000_000)


class QwenRunner:
    def __init__(self):
        import torch
        from transformers import AutoProcessor, BitsAndBytesConfig, Qwen3_5ForConditionalGeneration
        from zeus.monitor import ZeusMonitor
        self.torch = torch
        self.model_id = os.environ.get("GPU_ROUTER_MODEL", "Qwen/Qwen3.5-4B")
        self.revision = os.environ.get("GPU_ROUTER_REVISION", "")
        if not re.fullmatch(r"[a-f0-9]{40}", self.revision):
            raise RuntimeError("GPU_ROUTER_REVISION must be an immutable model commit SHA")
        from gpu_worker.kernels import check
        self.fast_path_versions = check()
        self.gpu_index, self.device = model_device(torch, "ROUTER")
        self.processor = AutoProcessor.from_pretrained(self.model_id, revision=self.revision)
        from gpu_worker.vision_limits import configure
        self.max_pixels = configure(self.processor)
        self.quantization = os.environ.get("GPU_ROUTER_QUANTIZATION", "bf16")
        kwargs = dict(revision=self.revision, torch_dtype=torch.bfloat16, attn_implementation="sdpa")
        if self.quantization == "int8":
            kwargs.update(quantization_config=BitsAndBytesConfig(load_in_8bit=True), device_map={"": self.gpu_index})
        elif self.quantization != "bf16":
            raise RuntimeError("GPU_ROUTER_QUANTIZATION must be bf16 or int8")
        self.model = Qwen3_5ForConditionalGeneration.from_pretrained(self.model_id, **kwargs)
        if self.quantization == "bf16":
            self.model = self.model.to(self.device)
        self.model.eval()
        self.monitor = ZeusMonitor(gpu_indices=[self.gpu_index])
        self.system = (Path(__file__).resolve().parents[1] / "server/prompts/evaluate.md").read_text()
        self.system += '\nJSON 형식: {"verdict":"PASS 또는 FAIL","reason":"한국어 한 문장","criteria":{"subject":"MATCH 또는 MISMATCH","composition":"MATCH 또는 MISMATCH","color":"MATCH 또는 MISMATCH","background":"MATCH 또는 MISMATCH"}}'
        # Record the judge GPU separately when generation uses another device.
        self.gpu_uuid = str(torch.cuda.get_device_properties(self.gpu_index).uuid)
        if not self.gpu_uuid.startswith("GPU-"):
            self.gpu_uuid = "GPU-" + self.gpu_uuid
        self.gpu_name = torch.cuda.get_device_name(self.gpu_index)
        self.zeus_version = version("zeus")
        from PIL import Image
        out = io.BytesIO()
        Image.new("RGB", (128, 128), "red").save(out, format="PNG")
        data = "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
        # Warm up forward/decode and probe real telemetry outside participant calls.
        sample = self.run(RouteRequest(request_id=UUID(int=0), target_image=data, generated_image=data))
        if not sample.get("energy") or sample["energy"]["status"] != "measured" or sample["status"] != "succeeded":
            raise RuntimeError("Qwen warmup/verdict/energy probe failed")

    @staticmethod
    def decode_image(data):
        from server.images import validate_image
        header, content = data.split(",", 1)
        if header not in ("data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"):
            raise ValueError("unsupported image source")
        raw = base64.b64decode(content, validate=True)
        if len(raw) > 3_000_000:
            raise ValueError("image too large")
        image = validate_image(raw)
        image.thumbnail((512, 512))
        return image

    def run(self, request):
        self.torch.cuda.set_device(self.gpu_index)
        from server.energy import RouterEnergyMeasurement
        from server.verdict import Verdict
        target, generated = self.decode_image(request.target_image), self.decode_image(request.generated_image)
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": [
            {"type": "text", "text": "TARGET:"}, {"type": "image", "image": target},
            {"type": "text", "text": "GENERATED:"}, {"type": "image", "image": generated},
            {"type": "text", "text": "지정 JSON만 출력하라."}]}]
        rid, start, wall = str(request.request_id), utcnow(), perf_counter()
        measured, output, inputs, error, opened = None, None, None, None, False
        try:
            self.monitor.begin_window(rid)
            opened = True
            inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                return_dict=True, return_tensors="pt", enable_thinking=False)
            from gpu_worker.vision_limits import verify
            self.vision_tokens = verify(inputs, self.processor, self.max_pixels)
            inputs = inputs.to(self.device)
            with self.torch.inference_mode():
                output = self.model.generate(**inputs, max_new_tokens=256, do_sample=False, use_cache=True)
        except Exception:
            error = "GPU_ROUTER_FAILED"
            log.exception("Qwen inference failed request_id=%s", rid)
        finally:
            if opened:
                try:
                    measured = self.monitor.end_window(rid)
                except Exception:
                    log.exception("Qwen telemetry failed request_id=%s", rid)
        end, duration = utcnow(), perf_counter() - wall
        input_count = int(inputs["input_ids"].shape[-1]) if inputs is not None else None
        output_count = int(output.shape[-1]) - input_count if output is not None else None
        value = measured.total_energy if measured else None
        valid = value is not None and math.isfinite(value) and value > 0
        energy = RouterEnergyMeasurement(measurement_id=request.request_id,
            status="measured" if valid else "unavailable", energy_j=value if valid else None,
            duration_s=measured.time if measured else duration,
            reason=None if valid else "ENERGY_COUNTER_UNAVAILABLE_OR_WINDOW_TOO_SHORT",
            gpu_name=self.gpu_name, gpu_uuid=self.gpu_uuid, model_id=self.model_id,
            model_revision=self.revision, zeus_version=self.zeus_version,
            started_at=start, finished_at=end, input_tokens=input_count, output_tokens=output_count)
        verdict = None
        if not error:
            try:
                text = self.processor.decode(output[0][input_count:], skip_special_tokens=True)
                verdict = Verdict.model_validate_json(text).model_dump()
            except Exception:
                error = "GPU_ROUTER_BAD_JSON"
        return {"request_id": rid, "status": "failed" if error else "succeeded",
                "energy": energy.model_dump(mode="json"), "verdict": verdict,
                "router_quantization": self.quantization,
                "error": error, "input_tokens": input_count, "output_tokens": output_count}


def create_app(runner=None, ledger_path=None, api_key=None, judge=None):
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
            app.state.runner = runner or FluxRunner()
            app.state.judge = judge if judge is not None else (QwenRunner() if runner is None else None)
            yield

    app = FastAPI(title="AI SLOP STOP GPU worker", lifespan=lifespan)

    def authorize(authorization: str = Header(default="")):
        if len(key) < 32 or not secrets.compare_digest(authorization, "Bearer " + key):
            raise HTTPException(403, "GPU_AUTH_DENIED")

    @app.get("/health", dependencies=[Depends(authorize)])
    def health():
        return {"ok": True, "provider": "zeus", "router_ready": app.state.judge is not None,
                "image_model": getattr(app.state.runner, "model_id", "test"),
                "router_model": getattr(app.state.judge, "model_id", None),
                "image_revision": getattr(app.state.runner, 'revision', None),
                "router_revision": getattr(app.state.judge, 'revision', None),
                "gpu_name": getattr(app.state.runner, 'gpu_name', None),
                "gpu_uuid": getattr(app.state.runner, 'gpu_uuid', None),
                "router_quantization": getattr(app.state.judge, 'quantization', None),
                "router_gpu_name": getattr(app.state.judge, 'gpu_name', None),
                "router_gpu_uuid": getattr(app.state.judge, 'gpu_uuid', None),
                "image_device": getattr(app.state.runner, 'device', None),
                "router_device": getattr(app.state.judge, 'device', None),
                "router_fast_path_versions": getattr(app.state.judge, 'fast_path_versions', None),
                "router_max_pixels": getattr(app.state.judge, 'max_pixels', None)}

    @app.post("/v1/generate", dependencies=[Depends(authorize)])
    def generate(request: GenerateRequest):
        rid = str(request.request_id)
        if not request.prompt.strip():
            raise HTTPException(400, "EMPTY_PROMPT")
        fingerprint = hashlib.sha256(("generate:" + request.prompt).encode()).hexdigest()
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

    @app.post("/v1/route", dependencies=[Depends(authorize)])
    def route(request: RouteRequest):
        if app.state.judge is None:
            raise HTTPException(503, "GPU_ROUTER_NOT_READY")
        rid = str(request.request_id)
        fingerprint = hashlib.sha256(("route:" + request.target_image + ":" + request.generated_image).encode()).hexdigest()
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
                result = app.state.judge.run(request)
            except Exception:
                log.exception("Router failed request_id=%s", rid)
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
