"""Direct image evaluation must work without torch or CLIP installed."""
import io
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image

from server import main
from server.config import Settings
from server.gemini_client import GeminiError, route_verdict
from server.images import ImageError, evaluation_image, validate_image


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(gemini_api_key="fake", image_model="image-test", router_model="router-test",
            router_provider="gemini", public_app_origin="http://localhost", max_requests_per_minute=1000, port=8000)
        self.result = {"verdict": "PASS", "reason": "피사체와 구도가 일치합니다.",
            "criteria": {key: "MATCH" for key in ("subject", "composition", "color", "background")}}
        out = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(out, format="PNG")
        self.raw = out.getvalue()

    def test_sends_two_images_without_numeric_similarity(self):
        provider = Mock(return_value=NS(text=json.dumps(self.result)))
        with patch("server.gemini_client._client", return_value=NS(models=NS(generate_content=provider))):
            actual = route_verdict(self.settings, self.raw, "image/png", self.raw, "image/png")
        self.assertEqual(actual, self.result)
        contents = provider.call_args.kwargs["contents"]
        parts = [part for part in contents if not isinstance(part, str)]
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(part.inline_data.data == self.raw for part in parts))
        self.assertFalse(any("코사인" in part or "점수" in part for part in contents if isinstance(part, str)))

    def test_gemini3_router_uses_low_thinking_for_both_verdicts(self):
        settings = Settings(**{**self.settings.__dict__, "router_model": "gemini-3.8-flash"})
        for verdict in ("PASS", "FAIL"):
            with self.subTest(verdict=verdict):
                result = {**self.result, "verdict": verdict}
                provider = Mock(return_value=NS(text=json.dumps(result)))
                with patch("server.gemini_client._client", return_value=NS(models=NS(generate_content=provider))):
                    self.assertEqual(route_verdict(settings, self.raw, "image/png", self.raw, "image/png")["verdict"], verdict)
                self.assertEqual(provider.call_args.kwargs["config"].thinking_config.thinking_level, "LOW")

    def test_rejects_invalid_router_results(self):
        for value in ([], {**self.result, "verdict": "MAYBE"}, {**self.result, "criteria": {}},
                      {**self.result, "criteria": {**self.result["criteria"], "color": 0.95}}):
            with self.subTest(value=value), patch("server.gemini_client._client",
                    return_value=NS(models=NS(generate_content=Mock(return_value=NS(text=json.dumps(value)))))):
                with self.assertRaisesRegex(GeminiError, "ROUTER_BAD_RESULT"):
                    route_verdict(self.settings, self.raw, "image/png", self.raw, "image/png")

    def test_invalid_image_is_rejected(self):
        with self.assertRaisesRegex(ImageError, "BAD_IMAGE"):
            validate_image(b"not an image")

    def test_oversized_image_is_rejected(self):
        with patch("server.images._MAX_PIXELS", 16):
            with self.assertRaisesRegex(ImageError, "IMAGE_TOO_LARGE"):
                validate_image(self.raw)

    def test_large_router_image_is_bounded_and_original_unchanged(self):
        out = io.BytesIO()
        Image.new("RGBA", (2048, 1024), (255, 0, 0, 0)).save(out, format="PNG")
        original = out.getvalue()
        resized, mime = evaluation_image(original, "image/png")
        self.assertEqual(mime, "image/jpeg")
        with Image.open(io.BytesIO(resized)) as image:
            self.assertEqual(image.size, (1024, 512))
            self.assertTrue(all(channel >= 250 for channel in image.getpixel((0, 0))))
        with Image.open(io.BytesIO(original)) as image:
            self.assertEqual(image.size, (2048, 1024))

    def test_large_png_payload_is_compressed_without_resizing(self):
        out = io.BytesIO()
        Image.effect_noise((512, 512), 100).convert("RGB").save(out, format="PNG")
        original = out.getvalue()
        self.assertGreater(len(original), 300_000)
        compressed, mime = evaluation_image(original, "image/png")
        self.assertEqual(mime, "image/jpeg")
        self.assertLess(len(compressed), len(original))
        with Image.open(io.BytesIO(compressed)) as image:
            self.assertEqual(image.size, (512, 512))

    def test_small_router_image_is_not_reencoded(self):
        self.assertEqual(evaluation_image(self.raw, "image/png"), (self.raw, "image/png"))

    def test_health_reports_direct_evaluation(self):
        with patch.object(main, "settings", self.settings):
            result = TestClient(main.app).get("/health").json()
        self.assertEqual(result["evaluation_method"], "gemini_vision")
        self.assertNotIn("similarity_loaded", result)


if __name__ == "__main__":
    unittest.main()
