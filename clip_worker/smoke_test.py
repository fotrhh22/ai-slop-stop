"""Exercise the actual CLIP model through server API contracts, without paid APIs."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import json
import resource
import secrets
import time
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from server import clip_bridge as bridge, main
from server.config import load_settings
from .worker import Encoder


def main_test():
    encoder = Encoder("auto", ".cache/clip")
    pictures = []
    for color, shape_color in (("white", "red"), ("navy", "yellow")):
        with Image.new("RGB", (224, 224), color) as image:
            ImageDraw.Draw(image).rectangle((40, 40, 180, 180), fill=shape_color)
            output = io.BytesIO()
            image.save(output, format="PNG")
            pictures.append(output.getvalue())
    identical = encoder.compare(pictures[0], pictures[0])
    assert identical > 0.999, identical
    settings = replace(load_settings(), clip_mode="observe", clip_timeout=20,
                       clip_worker_token=secrets.token_urlsafe(32), supabase_url="", supabase_secret_key="")
    route = Mock(return_value={"verdict": "FAIL", "reason": "Smoke test stub"})
    client = TestClient(main.app)
    started = time.perf_counter()
    with patch.object(main, "settings", settings), patch.object(bridge, "load_settings", return_value=settings), \
            patch.object(bridge, "broker", bridge.Broker()), patch.object(main, "route_verdict", route):
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(client.post, "/api/evaluate", json={
                name + "_image": "data:image/png;base64," + base64.b64encode(raw).decode()
                for name, raw in zip(("target", "generated"), pictures)})
            job = None
            headers = {"X-Clip-Worker-Token": settings.clip_worker_token}
            deadline = time.monotonic() + 10
            while job is None and time.monotonic() < deadline:
                job = client.post("/api/clip-worker/claim", headers=headers).json()["job"]
                time.sleep(0.01)
            assert job is not None
            route.assert_not_called()
            inference_start = time.perf_counter()
            score = encoder.compare(*(base64.b64decode(job[name + "_base64"]) for name in ("target", "generated")))
            response = client.post(f"/api/clip-worker/jobs/{job['id']}/complete", headers=headers, json={
                "lease_token": job["lease_token"], "model": bridge.MODEL, "pipeline": bridge.PIPELINE,
                "cosine": score, "elapsed_ms": (time.perf_counter() - inference_start) * 1000})
            assert response.status_code == 200, response.text
            response = future.result(timeout=20)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["similarity"]["cosine"] == score
            route.assert_called_once()
    print(json.dumps({"device": encoder.device, "identical_cosine": identical, "different_cosine": score,
                      "api_roundtrip_ms": round((time.perf_counter() - started) * 1000, 1),
                      "peak_process_memory_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, 1),
                      "paid_api_calls": 0}, indent=2))


if __name__ == "__main__":
    main_test()
