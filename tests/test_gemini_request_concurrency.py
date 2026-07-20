import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from unittest.mock import patch

from api.config import Settings
from api.gemini import GeminiClient


class GeminiRequestConcurrencyTests(unittest.TestCase):
    def test_gemini_requests_can_overlap(self):
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

        client = GeminiClient(Settings(gemini_api_key="test-key"))
        with (
            patch("api.gemini.GEMINI_MIN_INTERVAL_SECONDS", 0),
            patch("api.gemini.requests.post", side_effect=fake_post),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            list(pool.map(lambda _: client._post_chat({}), range(2)))

        self.assertEqual(max_active, 2)


if __name__ == "__main__":
    unittest.main()
