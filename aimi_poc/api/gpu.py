"""PoC inference contract. No Zeus, energy windows, or token accounting."""
import asyncio
import base64
import hashlib
import io
import os
from uuid import UUID
from pathlib import Path
from time import perf_counter

import httpx
from PIL import Image

from .study import CONTENT


class GPUError(Exception):
    pass


def unpack_image(data):
    if not isinstance(data, str) or not data.startswith("data:image/png;base64,"):
        raise GPUError("INVALID_GPU_IMAGE")
    try:
        raw = base64.b64decode(data.split(",", 1)[1], validate=True)
        if len(raw) > 12_000_000:
            raise ValueError("large image")
        with Image.open(io.BytesIO(raw)) as im:
            if im.size != (1024, 1024) or im.format != "PNG":
                raise ValueError("wrong dimensions")
            im.verify()
        return raw
    except Exception as e:
        raise GPUError("INVALID_GPU_IMAGE") from e


class GPUClient:
    def __init__(self, mode, config):
        self.mode, self.config = mode, config
        self.url = os.getenv("POC_GPU_API_URL", os.getenv("GPU_API_URL", "")).rstrip("/")
        self.key = os.getenv("POC_GPU_API_KEY", os.getenv("GPU_API_KEY", ""))
        self.delay = float(os.getenv("POC_MOCK_DELAY", "0.04"))

    async def health(self):
        if self.mode == "mock":
            return {"ok": True, "mode": "mock", "runtime_id": "mock", "notice": "모의 실행: 실제 모델 결과가 아닙니다"}
        if not self.url.startswith("https://") or len(self.key) < 32:
            raise GPUError("GPU_CONFIGURATION_REQUIRED")
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(self.url + "/health", headers={"Authorization": "Bearer " + self.key})
            res.raise_for_status()
            data = res.json()
        expected = {k: self.config[k] for k in ["image_model", "image_revision", "image_quantization", "router_model", "router_revision", "router_quantization", "router_max_pixels", "router_output_format"]}
        expected["router_prompt_sha256"] = self.config["router_prompt_sha256"]
        if data.get("mode") != "poc-no-energy" or not data.get("router_ready"):
            raise GPUError("POC_WORKER_REQUIRED")
        if data.get("gpu_count") != 1 or "5090" not in data.get("gpu_name", ""):
            raise GPUError("SINGLE_RTX5090_REQUIRED")
        if any(data.get(k) != v for k, v in expected.items()):
            raise GPUError("GPU_STUDY_CONFIG_MISMATCH")
        try:
            UUID(str(data.get('runtime_id', '')))
        except ValueError:
            raise GPUError('POC_WORKER_UPDATE_REQUIRED')
        return data

    async def remote(self, operation, rid, payload):
        headers = {"Authorization": "Bearer " + self.key}
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10)) as client:
            # Reconcile the fixed request ID before submitting; never invent an ID after a timeout.
            existing = await client.get(self.url + "/v1/requests/" + rid, headers=headers)
            if existing.status_code == 404:
                try:
                    for busy_attempt in range(60):
                        res = await client.post(self.url + "/v1/" + operation, json={"request_id": rid, **payload}, headers=headers)
                        if res.status_code != 429:
                            break
                        if busy_attempt == 59:
                            raise GPUError("GPU_BUSY")
                        # A 429 explicitly rejected this ID before execution.
                        # Keep the same ID; timeouts still reconcile below.
                        await asyncio.sleep(1)
                    res.raise_for_status()
                    result = res.json()
                except (httpx.TimeoutException, httpx.NetworkError):
                    result = {"status": "running"}
            else:
                existing.raise_for_status()
                result = existing.json()
            for _ in range(90):
                if result.get("status") != "running":
                    break
                await asyncio.sleep(2)
                res = await client.get(self.url + "/v1/requests/" + rid, headers=headers)
                if res.status_code == 404:
                    raise GPUError("GPU_RESULT_UNCONFIRMED")
                res.raise_for_status()
                result = res.json()
        if result.get("request_id") != rid or result.get("status") != "succeeded":
            raise GPUError(result.get("error") or "GPU_RESULT_UNCONFIRMED")
        return result

    async def generate(self, rid, prompt, seed):
        if self.mode == "mock":
            await asyncio.sleep(self.delay)
            raw = (CONTENT / "public/target-a.png").read_bytes()
            return raw, {"mode": "mock", "model": "mock-not-klein", "seed": seed, "generation_s": self.delay}
        result = await self.remote("generate", rid, {"prompt": prompt, "seed": seed})
        raw = unpack_image(result.get("image_url"))
        meta = result.get("metadata", {})
        if hashlib.sha256(raw).hexdigest() != result.get("image_sha256"):
            raise GPUError("GPU_IMAGE_HASH_MISMATCH")
        for key, expected in [("model", self.config["image_model"]), ("revision", self.config["image_revision"]), ("seed", seed), ("quantization", "nf4"), ("steps", 4)]:
            if meta.get(key) != expected:
                raise GPUError("GPU_GENERATION_CONFIG_MISMATCH")
        return raw, meta

    async def route(self, rid, raw, prompt):
        if self.mode == "mock":
            await asyncio.sleep(self.delay)
            passed = "연습통과" in prompt
            return {"verdict": "PASS" if passed else "FAIL", "reason": "모의 판정입니다. 실제 학습 효과 분석에 사용하지 않습니다.",
                    "criteria": {k: "MATCH" if passed else "MISMATCH" for k in ["subject", "composition", "color", "background"]}}, {"mode": "mock", "model": "mock-not-qwen", "router_s": self.delay}
        target = (CONTENT / "public/target-a.png").read_bytes()
        url = lambda b: "data:image/png;base64," + base64.b64encode(b).decode()
        result = await self.remote("route", rid, {"target_image": url(target), "generated_image": url(raw)})
        verdict = result.get("verdict", {})
        if not isinstance(verdict, dict) or set(verdict) != {'verdict'} or verdict['verdict'] not in ('PASS', 'FAIL'):
            raise GPUError('INVALID_GPU_VERDICT')
        meta = result.get("metadata", {})
        if meta.get("revision") != self.config["router_revision"] or meta.get("model") != self.config["router_model"]:
            raise GPUError("GPU_ROUTER_CONFIG_MISMATCH")
        return verdict, meta
