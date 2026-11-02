"""Gemini or measured Qwen verdict provider, without silent fallback."""
from types import SimpleNamespace
from typing import Literal, Optional
from uuid import UUID, uuid4
import base64
import httpx
from pydantic import BaseModel, ConfigDict
from .energy import RouterEnergyMeasurement
from .gemini_client import GeminiError, route_verdict as gemini_route
from .images import evaluation_image
from .verdict import Verdict

class RouterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    status: Literal["succeeded", "failed", "unknown"]
    verdict: Optional[Verdict] = None
    energy: Optional[RouterEnergyMeasurement] = None
    error: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

def route_verdict(settings, target_bytes, target_mime, generated_bytes, generated_mime, recorder=None):
    if settings.router_provider == "gemini":
        return gemini_route(settings, target_bytes, target_mime, generated_bytes, generated_mime, recorder=recorder)
    if settings.router_provider != "zeus":
        raise GeminiError("ROUTER_PROVIDER_INVALID")
    if not settings.gpu_api_url or len(settings.gpu_api_key) < 32:
        raise GeminiError("GPU_API_NOT_CONFIGURED")
    if recorder:
        recorder.store.rows("api_calls", select="energy_measurement", limit="1")
    call_id = recorder.start() if recorder else None
    rid = call_id or str(uuid4())
    def data_url(raw, mime):
        raw, mime = evaluation_image(raw, mime)
        return f"data:{mime};base64," + base64.b64encode(raw).decode()
    try:
        response = httpx.post(settings.gpu_api_url + "/v1/route",
            headers={"Authorization": "Bearer " + settings.gpu_api_key},
            json={"request_id": rid, "target_image": data_url(target_bytes, target_mime),
                  "generated_image": data_url(generated_bytes, generated_mime)},
            timeout=httpx.Timeout(settings.gpu_timeout_seconds, connect=10))
        response.raise_for_status()
        result = RouterResult.model_validate(response.json())
        if str(result.request_id) != rid:
            raise ValueError("router request mismatch")
        if result.energy and (str(result.energy.measurement_id) != rid or result.energy.model_id != settings.router_model):
            raise ValueError("router measurement mismatch")
        if result.status == "succeeded" and (result.verdict is None or result.energy is None):
            raise ValueError("missing verdict or telemetry")
    except Exception as exc:
        if recorder:
            recorder.finish(call_id, error=exc, unknown=True)
        raise GeminiError("GPU_ROUTER_RESULT_UNCONFIRMED") from exc
    usage = {"prompt_token_count": result.input_tokens, "candidates_token_count": result.output_tokens}
    if result.input_tokens is not None and result.output_tokens is not None:
        usage["total_token_count"] = result.input_tokens + result.output_tokens
    recorded = SimpleNamespace(usage_metadata=usage, model_version=settings.router_model,
        energy_measurement=result.energy.model_dump(mode="json") if result.energy else None)
    if recorder:
        recorder.finish(call_id, response=recorded,
            error=RuntimeError(result.error or "GPU_ROUTER_FAILED") if result.status != "succeeded" else None,
            unknown=result.status == "unknown")
    if result.status != "succeeded":
        raise GeminiError("GPU_ROUTER_FAILED" if result.status == "failed" else "GPU_ROUTER_RESULT_UNCONFIRMED")
    return result.verdict.model_dump()
