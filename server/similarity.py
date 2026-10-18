"""이미지 유사도 — 로컬 CLIP (open_clip, ViT-B/32).

목표 이미지와 생성 이미지를 각각 CLIP 임베딩 -> 코사인 유사도(0~1).
교체 가능하도록 compute_similarity(a_bytes, b_bytes) 단일 함수로 격리한다.
모델은 최초 호출 시 1회만 로드(지연 로딩).
"""
from __future__ import annotations

import io
import threading

from PIL import Image

_lock = threading.Lock()
_model = None
_preprocess = None


class SimilarityError(Exception):
    pass


def _ensure_model():
    global _model, _preprocess
    if _model is not None:
        return
    with _lock:
        if _model is not None:
            return
        import open_clip  # 지연 import (서버 기동 빠르게)
        import torch  # noqa: F401

        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="openai"
        )
        model.eval()
        _model = model
        _preprocess = preprocess


def _embed(img_bytes: bytes):
    import torch
    import torch.nn.functional as F

    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise SimilarityError(f"BAD_IMAGE: {e}") from e
    with torch.no_grad():
        tensor = _preprocess(img).unsqueeze(0)
        vec = _model.encode_image(tensor)
        return F.normalize(vec, dim=-1)


# CLIP 코사인 유사도가 실제로 분포하는 구간(min-max 재스케일 기준).
# 무관한 쌍도 코사인이 0 이 아니라 ~0.45 바닥에서 시작하고, 거의 동일해도 ~0.90 근처라
# raw 값은 0.45~0.90 좁은 띠에 몰린다. 이 구간을 0~1 로 선형 리맵해 체감 점수를 벌린다.
# (값은 경험적 기본값. 운영 데이터로 조정 가능.)
_RESCALE_MIN = 0.45
_RESCALE_MAX = 0.90


def _rescale(cos: float) -> float:
    span = _RESCALE_MAX - _RESCALE_MIN
    if span <= 0:
        return cos
    scaled = (cos - _RESCALE_MIN) / span
    return max(0.0, min(1.0, scaled))


def compute_similarity(a_bytes: bytes, b_bytes: bytes) -> dict:
    """두 이미지의 CLIP 유사도를 반환.
    - raw: 원시 코사인 유사도 [0,1] (Router 참고 신호)
    - score: min-max 재스케일된 표시용 점수 [0,1] (UI 노출)
    """
    _ensure_model()
    va = _embed(a_bytes)
    vb = _embed(b_bytes)
    cos = float((va * vb).sum())  # 정규화된 벡터이므로 내적 = 코사인
    cos = max(0.0, min(1.0, cos))  # [0,1] 클램프
    return {
        "raw": round(cos, 4),
        "score": round(_rescale(cos), 2),
    }
