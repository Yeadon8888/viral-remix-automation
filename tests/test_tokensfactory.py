import unittest
from types import SimpleNamespace

from api.tokensfactory import TokensFactoryImageClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, post_responses=None, get_responses=None):
        self.post_responses = list(post_responses or [])
        self.get_responses = list(get_responses or [])
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.post_responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.get_responses.pop(0)


def settings():
    return SimpleNamespace(
        tokensfactory_api_key="secret-key-must-not-leak",
        tokensfactory_base_url="https://tokensfactory.example",
        tokensfactory_image_model="nano-banana-2",
        tokensfactory_image_aspect_ratio="1:1",
        tokensfactory_image_resolution="2k",
        tokensfactory_image_timeout_seconds=30,
        tokensfactory_poll_interval_seconds=0,
        tokensfactory_request_retries=2,
    )


class TokensFactoryClientTests(unittest.TestCase):
    def test_generates_with_id_and_nested_result_url(self):
        session = FakeSession(
            post_responses=[FakeResponse({"id": "task-1"})],
            get_responses=[
                FakeResponse({"status": "processing"}),
                FakeResponse({"status": "succeeded", "metadata": {"result_urls": ["https://cdn.example/result.png"]}}),
            ],
        )
        client = TokensFactoryImageClient(settings(), session=session, sleep_fn=lambda _: None)

        result = client.generate_white_background("https://assets.example.com/source.png", "prompt")

        self.assertEqual(result, {"task_id": "task-1", "image_url": "https://cdn.example/result.png"})
        create_payload = session.calls[0][2]["json"]
        self.assertEqual(create_payload["model"], "nano-banana-2")
        self.assertEqual(create_payload["images"], ["https://assets.example.com/source.png"])
        self.assertEqual(create_payload["aspect_ratio"], "1:1")
        self.assertEqual(create_payload["resolution"], "2k")

    def test_accepts_task_id_and_top_level_image_url(self):
        session = FakeSession(
            post_responses=[FakeResponse({"task_id": "task-2"})],
            get_responses=[FakeResponse({"status": "completed", "image_url": "https://cdn.example/image.png"})],
        )
        client = TokensFactoryImageClient(settings(), session=session, sleep_fn=lambda _: None)

        result = client.generate_white_background("https://assets.example.com/source.png", "prompt")

        self.assertEqual(result["task_id"], "task-2")
        self.assertEqual(result["image_url"], "https://cdn.example/image.png")

    def test_failed_task_returns_public_error_without_key(self):
        session = FakeSession(
            post_responses=[FakeResponse({"id": "task-3"})],
            get_responses=[FakeResponse({"status": "failed", "error": "provider rejected image"})],
        )
        client = TokensFactoryImageClient(settings(), session=session, sleep_fn=lambda _: None)

        with self.assertRaises(RuntimeError) as raised:
            client.generate_white_background("https://assets.example.com/source.png", "prompt")

        self.assertIn("生成失败", str(raised.exception))
        self.assertNotIn("secret-key-must-not-leak", str(raised.exception))

    def test_missing_key_fails_before_http_request(self):
        config = settings()
        config.tokensfactory_api_key = ""
        session = FakeSession()
        client = TokensFactoryImageClient(config, session=session, sleep_fn=lambda _: None)

        with self.assertRaisesRegex(RuntimeError, "TOKENSFACTORY_API_KEY"):
            client.generate_white_background("https://assets.example.com/source.png", "prompt")

        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
