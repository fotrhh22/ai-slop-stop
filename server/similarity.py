"""Remote Jina CLIP embeddings, without torch or local model weights."""
from __future__ import annotations

import base64
import io
import math
import warnings

import httpx
from PIL import Image, ImageOps

from .config import Settings

_ENDPOINT = "https://api.jina.ai/v1/embeddings"
_MAX_PIXELS = 20_000_000


class SimilarityError(Exception):
    pass


def _encode_image(raw: bytes) -> str:
    """Validate and resize to <=512px, preserving aspect ratio and orientation."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > _MAX_PIXELS:
                    raise SimilarityError("IMAGE_TOO_LARGE")
                image = ImageOps.exif_transpose(source)
                image.thumbnail((512, 512), Image.Resampling.LANCZOS)
                if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    image = Image.new("RGB", rgba.size, "white")
                    image.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    image = image.convert("RGB")
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=90)
    except SimilarityError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise SimilarityError("BAD_IMAGE") from exc
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _unit_vector(value) -> list[float]:
    if not isinstance(value, list) or not value:
        raise SimilarityError("EMBEDDING_BAD_RESPONSE")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
        raise SimilarityError("EMBEDDING_BAD_RESPONSE")
    try:
        vector = [float(x) for x in value]
    except (ValueError, OverflowError) as exc:
        raise SimilarityError("EMBEDDING_BAD_RESPONSE") from exc
    if not all(math.isfinite(x) for x in vector):
        raise SimilarityError("EMBEDDING_BAD_RESPONSE")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm == 0:
        raise SimilarityError("EMBEDDING_BAD_RESPONSE")
    return [x / norm for x in vector]


def compute_similarity(a_bytes: bytes, b_bytes: bytes, settings: Settings) -> dict:
    if not settings.jina_api_key:
        raise SimilarityError("JINA_API_KEY_MISSING")
    payload = {
        "model": settings.similarity_model,
        "embedding_type": "float",
        "normalized": True,
        "input": [{"image": _encode_image(a_bytes)}, {"image": _encode_image(b_bytes)}],
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(settings.similarity_timeout, connect=10)) as client:
            response = client.post(
                _ENDPOINT,
                headers={"Authorization": f"Bearer {settings.jina_api_key}"},
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        # Never expose upstream bodies, which may echo inputs or credentials.
        if code in (401, 403):
            raise SimilarityError("EMBEDDING_AUTH_FAILED") from exc
        if code == 429:
            raise SimilarityError("EMBEDDING_RATE_LIMITED") from exc
        raise SimilarityError(f"EMBEDDING_HTTP_{code}") from exc
    except httpx.TimeoutException as exc:
        raise SimilarityError("EMBEDDING_TIMEOUT") from exc
    except httpx.RequestError as exc:
        raise SimilarityError("EMBEDDING_UNAVAILABLE") from exc

    try:
        body = response.json()
        records = body["data"]
        if not isinstance(records, list) or len(records) != 2:
            raise SimilarityError("EMBEDDING_BAD_RESPONSE")
        if any(not isinstance(r, dict) or type(r.get("index")) is not int for r in records):
            raise SimilarityError("EMBEDDING_BAD_RESPONSE")
        by_index = {r["index"]: r for r in records}
        if set(by_index) != {0, 1}:
            raise SimilarityError("EMBEDDING_BAD_RESPONSE")
        a = _unit_vector(by_index[0]["embedding"])
        b = _unit_vector(by_index[1]["embedding"])
        if len(a) != len(b):
            raise SimilarityError("EMBEDDING_BAD_RESPONSE")
        cosine = max(-1.0, min(1.0, math.fsum(x * y for x, y in zip(a, b))))
        # Keep signed raw cosine for Router; UI uses a zero floor, no empirical rescaling.
        return {"raw": round(cosine, 4), "score": round(max(0.0, cosine), 4),
                "model": settings.similarity_model, "provider": "jina"}
    except (KeyError, TypeError, ValueError) as exc:
        raise SimilarityError("EMBEDDING_BAD_RESPONSE") from exc
