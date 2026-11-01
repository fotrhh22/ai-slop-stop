"""Run OpenAI CLIP locally and return only image-image cosine similarity."""
from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
import hashlib
import io
import json
import os
from pathlib import Path
import time
from urllib.parse import urlparse

MODEL = "openai/ViT-B/32"
PIPELINE = "clip-rgb-exif-white-1024-v1"


class Encoder:
    def __init__(self, device, cache):
        import clip
        import torch
        self.torch = torch
        if device == "auto":
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = device
        torch.set_num_threads(2)
        self.model, self.preprocess = clip.load("ViT-B/32", device="cpu", jit=False, download_root=cache)
        self.model = self.model.float().eval().to(device)
        self.features = OrderedDict()

    def encode(self, raw):
        from PIL import Image, ImageOps
        # Bounded cache contains only 512-float CPU vectors, never image payloads.
        key = hashlib.sha256(raw).hexdigest()
        if key in self.features:
            self.features.move_to_end(key)
            return self.features[key]
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 20_000_000:
                raise ValueError("IMAGE_TOO_LARGE")
            image = ImageOps.exif_transpose(source).convert("RGBA")
        with image, Image.new("RGB", image.size, "white") as background:
            background.paste(image, mask=image.getchannel("A"))
            tensor = self.preprocess(background).unsqueeze(0).to(self.device)
        with self.torch.inference_mode():
            vector = self.model.encode_image(tensor).float()
            vector = (vector / vector.norm(dim=-1, keepdim=True)).cpu()
        self.features[key] = vector
        if len(self.features) > 128:
            self.features.popitem(last=False)
        return vector

    def compare(self, target, generated):
        a, b = self.encode(target), self.encode(generated)
        score = float((a @ b.T).item())
        if not self.torch.isfinite(self.torch.tensor(score)):
            raise ValueError("NONFINITE_SCORE")
        return max(-1.0, min(1.0, score))


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="clip_worker/.env.local")
    parser.add_argument("--device", choices=("auto", "cpu", "mps"), default="auto")
    parser.add_argument("--compare", nargs=2, metavar=("TARGET", "GENERATED"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    load_env(Path(args.env))
    import httpx
    base = os.getenv("CLIP_API_BASE", "http://127.0.0.1:8000").rstrip("/")
    token = os.getenv("CLIP_WORKER_TOKEN", "")
    url = urlparse(base)
    if not args.compare:
        if len(token) < 32:
            parser.error("CLIP_WORKER_TOKEN must have at least 32 characters")
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost")):
            parser.error("Use HTTPS except for localhost")
    encoder = Encoder(args.device, os.getenv("CLIP_CACHE_DIR", ".cache/clip"))
    if args.compare:
        start = time.perf_counter()
        score = encoder.compare(*(Path(p).read_bytes() for p in args.compare))
        print(json.dumps({"model": MODEL, "pipeline": PIPELINE, "device": encoder.device,
                          "cosine": score, "elapsed_ms": (time.perf_counter() - start) * 1000}))
        return
    print(f"CLIP ready: {MODEL}, device={encoder.device}, server={base}", flush=True)
    with httpx.Client(base_url=base, headers={"X-Clip-Worker-Token": token}, timeout=10, follow_redirects=False) as client:
        while True:
            try:
                response = client.post("/api/clip-worker/claim")
                response.raise_for_status()
                job = response.json()["job"]
                if job:
                    started = time.perf_counter()
                    result = {"lease_token": job["lease_token"], "model": MODEL, "pipeline": PIPELINE}
                    try:
                        if job["model"] != MODEL or job["pipeline"] != PIPELINE:
                            raise ValueError("PIPELINE_MISMATCH")
                        result["cosine"] = encoder.compare(*(base64.b64decode(job[name + "_base64"], validate=True)
                                                             for name in ("target", "generated")))
                    except Exception as exc:
                        print(f"Inference failed: {type(exc).__name__}", flush=True)
                        result["error"] = "INFERENCE_FAILED"
                    result["elapsed_ms"] = (time.perf_counter() - started) * 1000
                    # Retry only the result POST, never a second model invocation.
                    for attempt in range(3):
                        try:
                            response = client.post(f"/api/clip-worker/jobs/{job['id']}/complete", json=result)
                            if response.status_code == 409:
                                break  # Expired or server restarted; discard stale result.
                            response.raise_for_status()
                            print(json.dumps({"job": job["id"], **{k: v for k, v in result.items() if k != "lease_token"}}), flush=True)
                            break
                        except httpx.HTTPError:
                            if attempt == 2:
                                raise
                            time.sleep(0.5)
            except KeyboardInterrupt:
                break
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                print(f"Worker request failed: {type(exc).__name__}", flush=True)
                if args.once:
                    raise SystemExit(1)
                time.sleep(3)
            if args.once:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
