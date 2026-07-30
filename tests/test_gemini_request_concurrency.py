import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from unittest.mock import patch

from api.config import Settings
from api.gemini import GeminiClient


class GeminiRequestConcurrencyTests(unittest.TestCase):
    def test_configured_gemini_requests_can_overlap(self):
        active = 0
        max_active = 0
        lock = Lock()

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"choices": []}

        def fake_post(*args, **kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return FakeResponse()

        client = GeminiClient(Settings(
            gemini_api_key="test-key",
            gemini_request_concurrency=4,
            gemini_min_interval_seconds=0,
        ))
        with (
            patch("api.gemini.requests.post", side_effect=fake_post),
            ThreadPoolExecutor(max_workers=4) as pool,
        ):
            list(pool.map(lambda _: client._post_chat({}), range(4)))

        self.assertEqual(max_active, 4)

    def test_bad_gateway_is_retried(self):
        calls = []

        class FakeResponse:
            def __init__(self, status_code, text="", payload=None):
                self.status_code = status_code
                self.text = text
                self.headers = {}
                self._payload = payload or {}

            def json(self):
                return self._payload

        responses = [
            FakeResponse(502, "<html><h1>502 Bad Gateway</h1></html>"),
            FakeResponse(200, payload={"choices": []}),
        ]

        def fake_post(*args, **kwargs):
            calls.append((args, kwargs))
            return responses.pop(0)

        client = GeminiClient(Settings(
            gemini_api_key="test-key",
            gemini_request_concurrency=1,
            gemini_min_interval_seconds=0,
        ))
        with (
            patch("api.gemini.requests.post", side_effect=fake_post),
            patch("api.gemini.time.sleep"),
        ):
            result = client._post_chat({})

        self.assertEqual(result, {"choices": []})
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
