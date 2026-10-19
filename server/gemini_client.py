"""Gemini 호출 래퍼.
- generate_image: 프롬프트 -> 이미지(data URL)
- route_verdict: 두 이미지 -> PASS/FAIL + 이유 (Router)
"""
from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

from google import genai
from google.genai import types

from .images import evaluation_image
from .config import Settings
from .persistence import PersistenceError

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "evaluate.md"
_logger = logging.getLogger("uvicorn.error")


class GeminiError(Exception):
    pass


def _with_retry(fn, attempts: int = 3, delay: float = 1.5, recorder=None, label="model"):
    """일시적 네트워크 오류(connection reset 등)에 한해 재시도."""
    last = None
    for i in range(attempts):
        call_id = recorder.start() if recorder else None
        try:
            started = time.perf_counter()
            try:
                response = fn()
            finally:
                _logger.info("gemini_api operation=%s attempt=%d elapsed_ms=%.0f",
                             label, i + 1, (time.perf_counter() - started) * 1000)
        except Exception as e:  # noqa: BLE001
            if recorder:
                recorder.finish(call_id, error=e)
            msg = str(e).lower()
            transient = any(
                s in msg
                for s in ("reset by peer", "connection", "timeout", "temporarily", "503", "500")
            )
            last = e
            if not transient or i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))
        else:
            if recorder:
                recorder.finish(call_id, response=response)
            return response
    raise last  # pragma: no cover


def _client(settings: Settings) -> genai.Client:
    if not settings.gemini_configured:
        raise GeminiError("GEMINI_API_KEY_MISSING")
    # SDK retries would bypass the per-request ledger; retries live in _with_retry.
    return genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(attempts=1)))


def decode_data_url(data_url: str) -> tuple[bytes, str]:
    """이미지 소스를 (raw bytes, mime) 로 변환.
    - data:<mime>;base64,<...>  (업로드본 / 생성 이미지)
    임의 원격 URL은 받지 않는다.
    """
    if data_url.startswith("data:"):
        try:
            header, b64 = data_url.split(",", 1)
            mime = header.split(":", 1)[1].split(";", 1)[0] or "image/png"
            if mime not in ("image/png", "image/jpeg", "image/webp"):
                raise ValueError("unsupported image MIME")
            return base64.b64decode(b64, validate=True), mime
        except Exception as e:  # noqa: BLE001
            raise GeminiError(f"BAD_DATA_URL: {e}") from e
    raise GeminiError("UNSUPPORTED_IMAGE_SOURCE")


_VERDICT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING", "enum": ["PASS", "FAIL"]},
        "reason": {"type": "STRING"},
        "criteria": {
            "type": "OBJECT",
            "properties": {key: {"type": "STRING", "enum": ["MATCH", "MISMATCH"]}
                           for key in ("subject", "composition", "color", "background")},
            "required": ["subject", "composition", "color", "background"],
        },
    },
    "required": ["verdict", "reason", "criteria"],
}


def route_verdict(
    settings: Settings,
    target_bytes: bytes,
    target_mime: str,
    generated_bytes: bytes,
    generated_mime: str,
    recorder=None,
) -> dict:
    """두 이미지를 Gemini Router 에 넘겨 PASS/FAIL 판정을 받는다."""
    client = _client(settings)
    system = _PROMPT_PATH.read_text(encoding="utf-8")
    target_bytes, target_mime = evaluation_image(target_bytes, target_mime)
    generated_bytes, generated_mime = evaluation_image(generated_bytes, generated_mime)
    try:
        resp = _with_retry(
            lambda: client.models.generate_content(
                model=settings.router_model,
                contents=[
                    "TARGET 이미지:",
                    types.Part.from_bytes(data=target_bytes, mime_type=target_mime),
                    "GENERATED 이미지:",
                    types.Part.from_bytes(data=generated_bytes, mime_type=generated_mime),
                    "위 기준으로 PASS 또는 FAIL 을 판정하라.",
                ],
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=_VERDICT_SCHEMA,
                    temperature=0.2,
                    # Gemini 3 defaults to extended reasoning. Classification does
                    # not need that default; LOW is supported across Gemini 3.
                    thinking_config=(types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
                                     if settings.router_model.startswith("gemini-3") else None),
                ),
            ), recorder=recorder, label="router"
        )
    except PersistenceError:
        raise
    except Exception as e:  # noqa: BLE001
        raise GeminiError(f"ROUTER_FAILED: {type(e).__name__}: {e}") from e

    try:
        data = json.loads(resp.text)
    except Exception as e:  # noqa: BLE001
        raise GeminiError(f"ROUTER_BAD_JSON: {e}") from e

    if not isinstance(data, dict):
        raise GeminiError("ROUTER_BAD_RESULT")
    verdict = data.get("verdict")
    reason = data.get("reason")
    criteria = data.get("criteria")
    keys = ("subject", "composition", "color", "background")
    if (verdict not in ("PASS", "FAIL") or not isinstance(reason, str) or not reason.strip()
            or not isinstance(criteria, dict)
            or any(criteria.get(key) not in ("MATCH", "MISMATCH") for key in keys)):
        raise GeminiError("ROUTER_BAD_RESULT")
    return {"verdict": verdict, "reason": reason.strip(),
            "criteria": {key: criteria[key] for key in keys}}


def generate_image(settings: Settings, prompt: str, recorder=None) -> str:
    """프롬프트로 이미지를 생성해 data URL(base64) 로 반환."""
    client = _client(settings)
    try:
        resp = _with_retry(
            lambda: client.models.generate_content(
                model=settings.image_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                ),
            ), recorder=recorder, label="generate"
        )
    except PersistenceError:
        raise
    except Exception as e:  # noqa: BLE001
        raise GeminiError(f"GENERATE_FAILED: {type(e).__name__}: {e}") from e

    texts: list[str] = []
    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else []) or []:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                mime = inline.mime_type or "image/png"
                raw = inline.data
                if isinstance(raw, bytes):
                    b64 = base64.b64encode(raw).decode("ascii")
                else:  # SDK 가 이미 base64 문자열을 줄 수도 있음
                    b64 = raw
                return f"data:{mime};base64,{b64}"
            txt = getattr(part, "text", None)
            if txt:
                texts.append(txt)

    # 이미지가 없을 때: 왜 없는지(차단 사유 / 모델 텍스트)를 에러에 담는다.
    detail_parts: list[str] = []
    try:
        pf = getattr(resp, "prompt_feedback", None)
        block = getattr(pf, "block_reason", None) if pf else None
        if block:
            detail_parts.append(f"prompt_blocked={block}")
    except Exception:  # noqa: BLE001
        pass
    for cand in resp.candidates or []:
        fr = getattr(cand, "finish_reason", None)
        if fr:
            detail_parts.append(f"finish_reason={fr}")
    if texts:
        joined = " ".join(texts).strip().replace("\n", " ")
        detail_parts.append(f"text={joined[:200]}")

    if detail_parts:
        raise GeminiError("NO_IMAGE_IN_RESPONSE: " + " | ".join(detail_parts))
    raise GeminiError("NO_IMAGE_IN_RESPONSE")
