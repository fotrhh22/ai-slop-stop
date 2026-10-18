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
    clip_cache_dir: str = ".cache/clip"

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)


def load_settings() -> Settings:
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        image_model=os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
        router_model=os.getenv("GEMINI_ROUTER_MODEL", "gemini-3.8-flash"),
        router_provider=os.getenv("ROUTER_PROVIDER", "gemini").lower(),
        public_app_origin=os.getenv("PUBLIC_APP_ORIGIN", "http://127.0.0.1:5173"),
        max_requests_per_minute=int(os.getenv("RATE_LIMIT_PER_MIN", "20")),
        port=int(os.getenv("PORT", "8000")),
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_secret_key=os.getenv("SUPABASE_SECRET_KEY", ""),
        clip_cache_dir=os.getenv("CLIP_CACHE_DIR", ".cache/clip"),
    )
