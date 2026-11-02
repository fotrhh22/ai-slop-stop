"""Provider switch. GPU credentials are never sent to the browser."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict
from typing import Literal, Optional

from .energy import EnergyMeasurement
from .gemini_client import GeminiError, generate_image as gemini_generate, decode_data_url
from .images import validate_image


class GPUResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    status: Literal["succeeded", "failed", "unknown"]
    image_url: Optional[str] = None
    image_sha256: Optional[str] = None
    energy: Optional[EnergyMeasurement] = None
    error: Optional[str] = None


def generate_image(settings, prompt, recorder=None):
    if settings.image_provider == "gemini":
        return gemini_generate(settings, prompt, recorder=recorder)
    if settings.image_provider != "zeus":
        raise GeminiError("IMAGE_PROVIDER_INVALID")
    if not settings.gpu_api_url or len(settings.gpu_api_key) < 32:
        raise GeminiError("GPU_API_NOT_CONFIGURED")
    # Fail before contacting the GPU if the ledger migration has not been applied.
    if recorder:
        recorder.store.rows("api_calls", select="energy_measurement", limit="1")
    call_id = recorder.start() if recorder else None
    request_id = recorder.aid if recorder else str(uuid4())
    try:
        response = httpx.post(settings.gpu_api_url + "/v1/generate",
            headers={"Authorization": "Bearer " + settings.gpu_api_key},
            json={"request_id": request_id, "prompt": prompt},
            timeout=httpx.Timeout(settings.gpu_timeout_seconds, connect=10))
        response.raise_for_status()
        result = GPUResult.model_validate(response.json())
        if str(result.request_id) != request_id:
            raise ValueError("request ID mismatch")
        if result.energy and (str(result.energy.measurement_id) != request_id
                              or result.energy.model_id != settings.image_model):
            raise ValueError("measurement identity mismatch")
        if result.status == "succeeded":
            from .persistence import digest
            raw, _ = decode_data_url(result.image_url or "")
            validate_image(raw)
            if digest(raw) != result.image_sha256 or result.energy is None:
                raise ValueError("image/measurement missing or mismatched")
    except Exception as exc:
        # A timeout may happen after the remote GPU has already consumed energy.
        # Never retry this POST automatically or report unknown usage as zero.
        if recorder:
            recorder.finish(call_id, error=exc, unknown=True)
        raise GeminiError("GPU_RESULT_UNCONFIRMED") from exc
    recorded = SimpleNamespace(usage_metadata={}, model_version=settings.image_model,
        energy_measurement=result.energy.model_dump(mode="json") if result.energy else None)
    if recorder:
        recorder.finish(call_id, response=recorded,
            error=RuntimeError(result.error or "GPU_GENERATION_FAILED") if result.status != "succeeded" else None,
            unknown=result.status == "unknown")
    if result.status != "succeeded":
        raise GeminiError("GPU_GENERATION_FAILED" if result.status == "failed" else "GPU_RESULT_UNCONFIRMED")
    return result.image_url
