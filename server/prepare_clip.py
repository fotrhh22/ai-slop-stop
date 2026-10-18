"""Download weights during Render build, without constructing the CLIP model."""
from pathlib import Path

from .config import load_settings
from .similarity import MODEL, PRETRAINED


def main():
    from open_clip.pretrained import download_pretrained, get_pretrained_cfg

    settings = load_settings()
    Path(settings.clip_cache_dir).mkdir(parents=True, exist_ok=True)
    cfg = get_pretrained_cfg(MODEL, PRETRAINED)
    if not cfg:
        raise RuntimeError("CLIP pretrained configuration not found")
    download_pretrained(cfg, cache_dir=settings.clip_cache_dir)
    print("CLIP weights cached for local CPU inference")


if __name__ == "__main__":
    main()
