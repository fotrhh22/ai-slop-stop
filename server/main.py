"""백엔드 진입점 (FastAPI).

라우트:
  GET  /health           설정/키 상태
  POST /api/generate     프롬프트 -> Gemini 이미지 생성 -> { imageUrl }
  POST /api/evaluate     (3단계) 목표+생성 이미지 -> Gemini Router PASS/FAIL

API 키는 서버에만 존재한다. 브라우저는 /api 만 호출한다.
"""
from __future__ import annotations

from collections import defaultdict, deque
import asyncio
from contextlib import asynccontextmanager
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import load_settings
from .campaign import router as campaign_router
from .profiles import router as profiles_router
from .gemini_client import GeminiError, decode_data_url
from .remote_router import route_verdict
from .generation import generate_image
from .images import ImageError, validate_image
from .challenges import router as challenge_router
from .persistence import PersistenceError
from .printing import router as printing_router
from .clip_bridge import router as clip_router, evaluate_with_clip

settings = load_settings()

@asynccontextmanager
async def lifespan(app):
    from . import cleanup
    task = None
    if settings.session_cleanup_enabled and settings.supabase_url and settings.supabase_secret_key:
        task = asyncio.create_task(cleanup.run(settings))
    try:
        yield
    finally:
        if task is not None:
            await cleanup.stop(task)


app = FastAPI(title="Image Challenge Backend", version="0.1.0", lifespan=lifespan)



app.include_router(challenge_router)
app.include_router(printing_router)
app.include_router(clip_router)
app.include_router(campaign_router)
app.include_router(profiles_router)


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
        polling = (request.method == "GET" and ("/evaluations/" in request.url.path or "/generations/" in request.url.path or request.url.path.endswith(("/print-job", "/model-profiles")))) or request.url.path.startswith(("/api/printing/", "/api/clip-worker/", "/api/campaign/"))
        # Outbound CLIP polling must not consume the booth browser/print quota.
        # Keep result submissions separate so busy claim polling cannot block completion.
        if request.url.path.startswith("/api/clip-worker/"):
            bucket = "clip-claim:" if request.url.path.endswith("/claim") else "clip-result:"
        else:
            bucket = "poll:" if polling else "write:"
        key = bucket + (request.client.host if request.client else "unknown")
        now = monotonic()
        w = _windows[key]
        while w and w[0] <= now - 60:
            w.popleft()
        limit = max(120, settings.max_requests_per_minute * 6) if polling else settings.max_requests_per_minute
        if len(w) >= limit:
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
        "image_provider": settings.image_provider,
        "gpu_configured": bool(settings.gpu_api_url and len(settings.gpu_api_key) >= 32),
        "router_provider": settings.router_provider,
        "router_model": settings.router_model,
        "evaluation_method": "qwen_vision" if settings.router_provider == "zeus" else "gemini_vision",
        "clip_mode": settings.clip_mode,
        "persistence_configured": bool(settings.supabase_url and settings.supabase_secret_key),
        "bike_configured": len(settings.bike_worker_token) >= 32,
        "receipt_print_trigger": "bike_session_end",
        "manual_pass_enabled": False,
        "session_cleanup_enabled": settings.session_cleanup_enabled,
        "unpassed_retention_hours": settings.unpassed_retention_hours,
        "default_profile": "gemini",
        "model_selection_enabled": True,
        "gpu_registration_configured": len(settings.gpu_control_token) >= 32,
        "gpu_profiles_configured": [key for key,value in settings.gpu_profiles.items() if len(value.get('key','')) >= 32],
    }


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


@app.post("/api/generate")
def api_generate(req: GenerateRequest) -> dict:
    if settings.image_provider == "zeus" or (settings.supabase_url and settings.supabase_secret_key):
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

    try:
        verdict = evaluate_with_clip(
            settings, target_bytes, target_mime, gen_bytes, gen_mime, route_verdict
        )
    except GeminiError as e:
        return JSONResponse(
            status_code=502,
            content={"detail": str(e).split(":")[0], "message": str(e)},
        )

    return {**verdict, "router_model": settings.router_model if verdict.get("router_called", True) else None}


# Include ServerErrorMiddleware responses in CORS handling too.
app = CORSMiddleware(
    app,
    allow_origins=[settings.public_app_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Challenge-Token", "X-Print-Station-Token", "X-Print-Worker-Token"],
)
