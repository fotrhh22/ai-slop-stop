"""서버 설정. .env 에서 환경 변수를 로드한다 (의존성 없음).
API 키는 백엔드에만 두고 브라우저로 내보내지 않는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file() -> None:
    root = Path(__file__).resolve().parents[1]  # server/ -> project root
    # Exported variables win, then root .env.local, then root .env.
    for name in (".env.local", ".env"):
        p = root / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file()


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    image_model: str
    router_model: str
    router_provider: str
    public_app_origin: str
    max_requests_per_minute: int
    port: int
    supabase_url: str = ""
    supabase_secret_key: str = ""

    print_station_id: str = "booth-1"
    print_pairing_token: str = ""
    print_worker_token: str = ""
    clip_mode: str = "off"
    clip_worker_token: str = ""
    clip_timeout: float = 20.0
    clip_failure_policy: str = "fallback"
    clip_reject_below: float | None = None
    image_provider: str = "gemini"
    gpu_api_url: str = ""
    gpu_api_key: str = ""
    gpu_timeout_seconds: int = 150

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)


def load_settings() -> Settings:
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        image_model=(os.getenv("GPU_IMAGE_MODEL", "black-forest-labs/FLUX.2-klein-4B")
                     if os.getenv("IMAGE_PROVIDER", "gemini").lower() == "zeus"
                     else os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-lite-image")),
        router_model=(os.getenv("GPU_ROUTER_MODEL", "Qwen/Qwen3.5-4B")
                      if os.getenv("ROUTER_PROVIDER", "gemini").lower() == "zeus"
                      else os.getenv("GEMINI_ROUTER_MODEL", "gemini-3.5-flash-lite")),
        router_provider=os.getenv("ROUTER_PROVIDER", "gemini").lower(),
        public_app_origin=os.getenv("PUBLIC_APP_ORIGIN", "http://127.0.0.1:5173"),
        max_requests_per_minute=int(os.getenv("RATE_LIMIT_PER_MIN", "20")),
        port=int(os.getenv("PORT", "8000")),
        print_station_id=os.getenv("PRINT_STATION_ID", "booth-1"),
        print_pairing_token=os.getenv("PRINT_PAIRING_TOKEN", ""),
        print_worker_token=os.getenv("PRINT_WORKER_TOKEN", ""),
        clip_mode=os.getenv("CLIP_MODE", "off"),
        clip_worker_token=os.getenv("CLIP_WORKER_TOKEN", ""),
        clip_timeout=float(os.getenv("CLIP_TIMEOUT_SECONDS", "20")),
        clip_failure_policy=os.getenv("CLIP_FAILURE_POLICY", "fallback"),
        clip_reject_below=(float(os.environ["CLIP_REJECT_BELOW"]) if os.getenv("CLIP_REJECT_BELOW") else None),
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_secret_key=os.getenv("SUPABASE_SECRET_KEY", ""),
        image_provider=os.getenv("IMAGE_PROVIDER", "gemini").lower(),
        gpu_api_url=os.getenv("GPU_API_URL", "").rstrip("/"),
        gpu_api_key=os.getenv("GPU_API_KEY", ""),
        gpu_timeout_seconds=int(os.getenv("GPU_TIMEOUT_SECONDS", "150")),
    )
