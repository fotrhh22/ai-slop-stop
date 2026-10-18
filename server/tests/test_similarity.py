"""Local CLIP lifecycle, cosine and API checks without downloading weights."""
from __future__ import annotations

import base64
import io
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import torch
from fastapi.testclient import TestClient
from PIL import Image

from server import main, similarity
from server.config import Settings
from server.similarity import SimilarityError, compute_similarity


def image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buffer, format="PNG")
    return buffer.getvalue()


class SimilarityTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            gemini_api_key="fake-key", image_model="image-test", router_model="router-test",
            router_provider="gemini", public_app_origin="http://127.0.0.1:5173",
            max_requests_per_minute=100, port=8000,
        )
        self.image = image_bytes()
        self.encoder_patch = patch.object(similarity, "_encoder", None)
        self.preprocess_patch = patch.object(similarity, "_preprocess", None)
        self.encoder_patch.start()
        self.preprocess_patch.start()
        self.addCleanup(self.encoder_patch.stop)
        self.addCleanup(self.preprocess_patch.stop)

    def prepared_encoder(self, encoder):
        similarity._encoder = encoder
        similarity._preprocess = lambda image: torch.ones(3, 2, 2)

    def test_cpu_load_happens_once_and_retains_only_visual_encoder(self):
        import open_clip
        visual = torch.nn.Identity()
        full_model = type("CLIP", (), {"visual": visual})()
        with patch.object(open_clip, "create_model_and_transforms", return_value=(full_model, None, object())) as create:
            with similarity._inference_lock:
                similarity._ensure_encoder(self.settings)
                similarity._ensure_encoder(self.settings)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(create.call_args.kwargs["device"], "cpu")
        self.assertEqual(create.call_args.kwargs["precision"], "fp32")
        self.assertIs(similarity._encoder, visual)
        self.assertFalse(visual.training)

    def test_cosine_normalization_and_negative_raw_score(self):
        for vector, raw, score in [([3., 4.], 0.6, 0.6), ([-1., 0.], -1., 0.)]:
            with self.subTest(vector=vector):
                outputs = iter([torch.tensor([[1., 0.]]), torch.tensor([vector])])
                self.prepared_encoder(lambda tensor: next(outputs))
                result = compute_similarity(self.image, self.image, self.settings)
                self.assertEqual(result["raw"], raw)
                self.assertEqual(result["score"], score)
                self.assertEqual(result["provider"], "local")
                self.assertEqual(result["model"], "CLIP ViT-B/32 (openai)")

    def test_invalid_image_is_rejected_before_model_load(self):
        with patch.object(similarity, "_ensure_encoder") as load:
            with self.assertRaisesRegex(SimilarityError, "^BAD_IMAGE$"):
                compute_similarity(b"bad image", self.image, self.settings)
            load.assert_not_called()

    def test_load_failure_is_reported_without_upstream_details(self):
        import open_clip
        with patch.object(open_clip, "create_model_and_transforms", side_effect=RuntimeError("private path")):
            with self.assertRaisesRegex(SimilarityError, "^CLIP_LOAD_FAILED$"):
                compute_similarity(self.image, self.image, self.settings)
        self.assertFalse(similarity.model_loaded())

    def test_inference_errors_and_nonfinite_results(self):
        def failed(tensor):
            raise RuntimeError("private path")
        for encoder, expected in [(failed, "CLIP_INFERENCE_FAILED"),
                                  (lambda tensor: torch.tensor([[float("nan"), 1.]]), "CLIP_INVALID_RESULT")]:
            self.prepared_encoder(encoder)
            with self.assertRaisesRegex(SimilarityError, f"^{expected}$"):
                compute_similarity(self.image, self.image, self.settings)

    def test_concurrent_requests_are_serialized(self):
        state = {"active": 0, "peak": 0}
        lock = threading.Lock()
        def encode(tensor):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["active"], state["peak"])
            time.sleep(0.01)
            with lock:
                state["active"] -= 1
            return torch.tensor([[1., 0.]])
        self.prepared_encoder(encode)
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(lambda _: compute_similarity(self.image, self.image, self.settings), range(3)))
        self.assertEqual(state["peak"], 1)
        self.assertTrue(all(r["score"] == 1 for r in results))

    def test_api_uses_local_similarity_and_skips_router_on_failure(self):
        data_url = "data:image/png;base64," + base64.b64encode(self.image).decode()
        body = {"target_image": data_url, "generated_image": data_url}
        outputs = iter([torch.tensor([[1., 0.]]), torch.tensor([[3., 4.]])])
        self.prepared_encoder(lambda tensor: next(outputs))
        with patch.object(main, "settings", self.settings), patch.object(main, "route_verdict", return_value={"verdict": "PASS", "reason": "일치"}) as router:
            response = TestClient(main.app).post("/api/evaluate", json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["similarity_provider"], "local")
            self.assertEqual(router.call_args.args[-1], 0.6)
        with patch.object(main, "route_verdict") as router:
            response = TestClient(main.app).post("/api/evaluate", json={**body, "target_image": "data:image/png;base64,YmFk"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"], "BAD_IMAGE")
            router.assert_not_called()


if __name__ == "__main__":
    unittest.main()
