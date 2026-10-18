"""Local OpenCLIP ViT-B/32 on CPU. Loaded lazily; image encoder retained only."""
from __future__ import annotations

import io
import math
import threading
import warnings

from PIL import Image, ImageOps

from .config import Settings

MODEL = "ViT-B-32"
PRETRAINED = "openai"
MODEL_LABEL = "CLIP ViT-B/32 (openai)"
_encoder = None
_preprocess = None
_inference_lock = threading.Lock()
_MAX_PIXELS = 20_000_000


class SimilarityError(Exception):
    pass


def model_loaded() -> bool:
    return _encoder is not None


def _read_image(raw: bytes):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > _MAX_PIXELS:
                    raise SimilarityError("IMAGE_TOO_LARGE")
                return ImageOps.exif_transpose(source).convert("RGB")
    except SimilarityError:
        raise
    except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise SimilarityError("BAD_IMAGE") from exc


def _ensure_encoder(settings: Settings):
    global _encoder, _preprocess
    # Caller holds _inference_lock: one load and one inference at a time.
    if _encoder is not None:
        return
    try:
        import open_clip
        import torch

        torch.set_num_threads(1)
        model, _, preprocess = open_clip.create_model_and_transforms(
            MODEL, pretrained=PRETRAINED, device="cpu", precision="fp32",
            cache_dir=settings.clip_cache_dir,
        )
        # encode_image() uses visual directly. Discard unused text encoder to
        # reduce steady-state RAM without changing image embeddings.
        encoder = model.visual.eval()
        del model
        _preprocess = preprocess
        _encoder = encoder
    except Exception as exc:
        raise SimilarityError("CLIP_LOAD_FAILED") from exc


def compute_similarity(a_bytes: bytes, b_bytes: bytes, settings: Settings) -> dict:
    # Reject invalid images before loading the model.
    a_image = _read_image(a_bytes)
    b_image = _read_image(b_bytes)
    with _inference_lock:
        _ensure_encoder(settings)
        try:
            import torch
            import torch.nn.functional as F

            with torch.inference_mode():
                # Sequential encodes avoid the larger peak of a two-image batch.
                a = F.normalize(_encoder(_preprocess(a_image).unsqueeze(0)), dim=-1)
                b = F.normalize(_encoder(_preprocess(b_image).unsqueeze(0)), dim=-1)
                cosine = float((a * b).sum())
            if not math.isfinite(cosine):
                raise SimilarityError("CLIP_INVALID_RESULT")
        except SimilarityError:
            raise
        except Exception as exc:
            raise SimilarityError("CLIP_INFERENCE_FAILED") from exc
    cosine = max(-1.0, min(1.0, cosine))
    return {"raw": round(cosine, 4), "score": round(max(0.0, cosine), 4),
            "model": MODEL_LABEL, "provider": "local"}
