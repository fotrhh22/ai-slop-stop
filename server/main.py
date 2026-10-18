"""백엔드 진입점 (FastAPI).

라우트:
  GET  /health           설정/키 상태
  POST /api/generate     프롬프트 -> Gemini 이미지 생성 -> { imageUrl }
  POST /api/evaluate     (3단계) 목표+생성 이미지 -> 유사도 + Router PASS/FAIL

API 키는 서버에만 존재한다. 브라우저는 /api 만 호출한다.
"""
from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import load_settings
from .gemini_client import GeminiError, decode_data_url, generate_image, route_verdict
from .similarity import SimilarityError, compute_similarity

settings = load_settings()

app = FastAPI(title="Image Challenge Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_app_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# 아주 단순한 분당 호출 제한 (공개 운영 대비)
_windows: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        if int(request.headers.get("content-length") or 0) > 8_000_000:
            return JSONResponse(status_code=413, content={"detail": "REQUEST_TOO_LARGE"})
        key = request.client.host if request.client else "unknown"
        now = monotonic()
        w = _windows[key]
        while w and w[0] <= now - 60:
            w.popleft()
        if len(w) >= settings.max_requests_per_minute:
            return JSONResponse(status_code=429, content={"detail": "RATE_LIMITED"})
        w.append(now)
        if len(_windows) > 10_000:
            _windows.clear()
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "gemini_configured": settings.gemini_configured,
        "image_model": settings.image_model,
        "router_provider": settings.router_provider,
        "router_model": settings.router_model,
    }


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


@app.post("/api/generate")
def api_generate(req: GenerateRequest) -> dict:
    try:
        image_url = generate_image(settings, req.prompt)
        return {"imageUrl": image_url}
    except GeminiError as e:
        code = str(e).split(":")[0]
        return JSONResponse(status_code=502, content={"detail": code, "message": str(e)})


class EvaluateRequest(BaseModel):
    target_image: str
    generated_image: str


@app.post("/api/evaluate")
def api_evaluate(req: EvaluateRequest):
    # 1) 두 이미지 디코드
    try:
        target_bytes, target_mime = decode_data_url(req.target_image)
        gen_bytes, gen_mime = decode_data_url(req.generated_image)
    except GeminiError as e:
        return JSONResponse(status_code=400, content={"detail": str(e).split(":")[0]})

    # 2) CLIP 유사도 (raw: Router 참고 신호, score: UI 표시용 재스케일)
    try:
        sim = compute_similarity(target_bytes, gen_bytes)
    except SimilarityError as e:
        return JSONResponse(status_code=502, content={"detail": "SIMILARITY_FAILED", "message": str(e)})

    # 3) Gemini Router 최종 판정 (PASS/FAIL + 이유). raw 코사인을 참고 신호로 전달.
    try:
        verdict = route_verdict(
            settings, target_bytes, target_mime, gen_bytes, gen_mime, sim["raw"]
        )
    except GeminiError as e:
        return JSONResponse(
            status_code=502,
            content={"detail": str(e).split(":")[0], "message": str(e)},
        )

    return {
        "similarity": sim["score"],   # 재스케일된 표시용 점수 (0~1)
        "similarity_raw": sim["raw"], # 원시 코사인 (투명성/디버그)
        "verdict": verdict["verdict"],
        "reason": verdict["reason"],
    }
