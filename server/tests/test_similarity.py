"""External API contract and failure checks; no paid requests or API keys needed."""
from __future__ import annotations

import base64
import io
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from server.config import Settings
from server.similarity import SimilarityError, compute_similarity
from server import main


def image_bytes(size=(16, 16)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="PNG")
    return buffer.getvalue()


class SimilarityTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            gemini_api_key="fake-gemini-key", image_model="image-test", router_model="router-test",
            router_provider="gemini", public_app_origin="http://127.0.0.1:5173",
            max_requests_per_minute=100, port=8000, jina_api_key="fake-jina-key",
        )
        self.image = image_bytes()
        self.client_class = httpx.Client

    def transport_patch(self, handler):
        def factory(**kwargs):
            return self.client_class(transport=httpx.MockTransport(handler), **kwargs)
        return patch("server.similarity.httpx.Client", side_effect=factory)

    def response(self, request, vectors):
        return httpx.Response(200, request=request, text=json.dumps({"data": vectors}))

    def test_one_batched_request_resizes_and_respects_indices(self):
        requests = []

        def handler(request):
            requests.append(request)
            body = json.loads(request.content)
            self.assertEqual(request.url, "https://api.jina.ai/v1/embeddings")
            self.assertEqual(request.headers["Authorization"], "Bearer fake-jina-key")
            self.assertEqual(body["model"], "jina-clip-v2")
            self.assertEqual(body["embedding_type"], "float")
            self.assertEqual(len(body["input"]), 2)
            with Image.open(io.BytesIO(base64.b64decode(body["input"][0]["image"]))) as sent:
                self.assertEqual(sent.size, (512, 256))
            return self.response(request, [
                {"index": 1, "embedding": [3, 4]},
                {"index": 0, "embedding": [1, 0]},
            ])

        with self.transport_patch(handler):
            result = compute_similarity(image_bytes((1024, 512)), self.image, self.settings)
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["raw"], 0.6)
        self.assertEqual(result["score"], 0.6)  # old 0.45..0.90 remapping is gone
        self.assertEqual(result["model"], "jina-clip-v2")

    def test_negative_cosine_preserved_for_router(self):
        with self.transport_patch(lambda r: self.response(r, [
            {"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [-1, 0]}
        ])):
            result = compute_similarity(self.image, self.image, self.settings)
        self.assertEqual(result["raw"], -1)
        self.assertEqual(result["score"], 0)

    def test_missing_key_and_invalid_images_make_no_external_request(self):
        with patch("server.similarity.httpx.Client") as client:
            with self.assertRaisesRegex(SimilarityError, "JINA_API_KEY_MISSING"):
                compute_similarity(self.image, self.image, replace(self.settings, jina_api_key=""))
            with self.assertRaisesRegex(SimilarityError, "BAD_IMAGE"):
                compute_similarity(b"not an image", self.image, self.settings)
            client.assert_not_called()

    def test_http_errors_do_not_echo_provider_body(self):
        for code, expected in [(401, "EMBEDDING_AUTH_FAILED"), (403, "EMBEDDING_AUTH_FAILED"),
                               (429, "EMBEDDING_RATE_LIMITED"), (500, "EMBEDDING_HTTP_500")]:
            with self.subTest(code=code), self.transport_patch(
                lambda r: httpx.Response(code, request=r, text="fake-jina-key private payload")
            ):
                with self.assertRaisesRegex(SimilarityError, f"^{expected}$"):
                    compute_similarity(self.image, self.image, self.settings)

    def test_timeout_and_connection_errors(self):
        for error, expected in [(httpx.ReadTimeout, "EMBEDDING_TIMEOUT"),
                                (httpx.ConnectError, "EMBEDDING_UNAVAILABLE")]:
            def handler(request):
                raise error("private request info", request=request)
            with self.subTest(error=error), self.transport_patch(handler):
                with self.assertRaisesRegex(SimilarityError, f"^{expected}$"):
                    compute_similarity(self.image, self.image, self.settings)

    def test_malformed_outputs_are_rejected(self):
        invalid = [
            [], [{"index": 0, "embedding": [1]}],
            [{"index": 0, "embedding": [1]}, {"index": 0, "embedding": [1]}],
            [{"index": False, "embedding": [1]}, {"index": 1, "embedding": [1]}],
            [{"index": 0, "embedding": [1]}, {"index": 1, "embedding": [1, 2]}],
        ]
        for vector in [[], [0, 0], [True, 1], ["1", 0], [None, 1], [float("inf"), 0]]:
            invalid.append([{"index": 0, "embedding": vector}, {"index": 1, "embedding": [1, 0]}])
        for records in invalid:
            with self.subTest(records=records), self.transport_patch(lambda r: self.response(r, records)):
                with self.assertRaisesRegex(SimilarityError, "EMBEDDING_BAD_RESPONSE"):
                    compute_similarity(self.image, self.image, self.settings)
        with self.transport_patch(lambda r: httpx.Response(200, request=r, text="invalid json")):
            with self.assertRaisesRegex(SimilarityError, "EMBEDDING_BAD_RESPONSE"):
                compute_similarity(self.image, self.image, self.settings)

    def test_api_calls_router_only_after_similarity_success(self):
        data_url = "data:image/png;base64," + base64.b64encode(self.image).decode()
        body = {"target_image": data_url, "generated_image": data_url}
        with patch.object(main, "settings", self.settings), self.transport_patch(
            lambda r: self.response(r, [{"index": 0, "embedding": [1, 0]},
                                       {"index": 1, "embedding": [3, 4]}])
        ), patch.object(main, "route_verdict", return_value={"verdict": "PASS", "reason": "일치"}) as router:
            response = TestClient(main.app).post("/api/evaluate", json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["similarity_model"], "jina-clip-v2")
            self.assertEqual(response.json()["router_model"], "router-test")
            self.assertEqual(router.call_args.args[-1], 0.6)
        with patch.object(main, "settings", replace(self.settings, jina_api_key="")), patch.object(main, "route_verdict") as router:
            response = TestClient(main.app).post("/api/evaluate", json=body)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"], "JINA_API_KEY_MISSING")
            router.assert_not_called()


if __name__ == "__main__":
    unittest.main()
