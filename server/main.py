"""백엔드 진입점 (FastAPI).

라우트:
  GET  /health           설정/키 상태
  POST /api/generate     프롬프트 -> Gemini 이미지 생성 -> { imageUrl }
  POST /api/evaluate     (3단계) 목표+생성 이미지 -> Gemini Router PASS/FAIL

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
from .images import ImageError, validate_image
from .challenges import router as challenge_router
from .persistence import PersistenceError

settings = load_settings()

app = FastAPI(title="Image Challenge Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_app_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Challenge-Token"],
)

app.include_router(challenge_router)


@app.exception_handler(PersistenceError)
async def persistence_error(request: Request, exc: PersistenceError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

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
        "evaluation_method": "gemini_vision",
        "persistence_configured": bool(settings.supabase_url and settings.supabase_secret_key),
    }


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


@app.post("/api/generate")
def api_generate(req: GenerateRequest) -> dict:
    if settings.supabase_url and settings.supabase_secret_key:
        return JSONResponse(status_code=400, content={"detail": "USE_CHALLENGE_SESSION_API"})
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
    if settings.supabase_url and settings.supabase_secret_key:
        return JSONResponse(status_code=400, content={"detail": "USE_CHALLENGE_SESSION_API"})
    # 1) 두 이미지 디코드
    try:
        target_bytes, target_mime = decode_data_url(req.target_image)
        gen_bytes, gen_mime = decode_data_url(req.generated_image)
    except GeminiError as e:
        return JSONResponse(status_code=400, content={"detail": str(e).split(":")[0]})

    try:
        validate_image(target_bytes)
        validate_image(gen_bytes)
    except ImageError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    # Gemini compares both images directly; no local model is loaded.
    try:
        verdict = route_verdict(
            settings, target_bytes, target_mime, gen_bytes, gen_mime
        )
    except GeminiError as e:
        return JSONResponse(
            status_code=502,
            content={"detail": str(e).split(":")[0], "message": str(e)},
        )

    return {**verdict, "router_model": settings.router_model}
