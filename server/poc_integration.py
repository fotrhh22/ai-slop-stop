"""Optional PoC sub-app; campaign routes and credentials remain independent."""
import os
from pathlib import Path


def create_poc_app():
    if os.getenv("POC_ENABLED", "false").lower() != "true":
        return None
    mode = os.getenv("POC_GPU_MODE", "mock")
    if mode in ("runpod", "gemini"):
        required = ("POC_ADMIN_TOKEN",
                    "POC_ALLOWED_ORIGINS", "POC_MEDIA_PATH")
        if mode == "runpod":
            required += ("POC_GPU_API_URL", "POC_GPU_API_KEY")
        missing = [key for key in required if not os.getenv(key, "").strip()]
        if not os.getenv("POC_DATABASE_URL") and not os.getenv("POC_DB_PATH"):
            missing.append("POC_DB_PATH or POC_DATABASE_URL")
        if missing:
            raise RuntimeError("PoC configuration required: " + ", ".join(missing))
        if len(os.environ["POC_ADMIN_TOKEN"]) < 32:
            raise RuntimeError("POC_ADMIN_TOKEN must contain at least 32 characters")
        if mode == "gemini" and not os.getenv("POC_GEMINI_API_KEY", os.getenv("GEMINI_API_KEY", "")):
            raise RuntimeError("Gemini PoC requires POC_GEMINI_API_KEY or GEMINI_API_KEY")
        if mode == "runpod" and (len(os.environ["POC_GPU_API_KEY"]) < 32 or not os.environ["POC_GPU_API_URL"].startswith("https://")):
            raise RuntimeError("PoC requires an HTTPS GPU origin and a key of at least 32 characters")
        for key in ("POC_MEDIA_PATH", "POC_DB_PATH"):
            if os.getenv(key) and not Path(os.environ[key]).is_absolute():
                raise RuntimeError(key + " must be an absolute persistent-storage path")
    from aimi_poc.api.app import create_app
    from aimi_poc.api.store import Store
    # An explicit empty URL prevents falling back to the campaign DATABASE_URL.
    store = Store(url=os.getenv("POC_DATABASE_URL", ""),
                  path=os.getenv("POC_DB_PATH", "/tmp/aimi-poc/poc.sqlite3"))
    return create_app(store=store, mode=mode)
