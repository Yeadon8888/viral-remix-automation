import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.feishu import FeishuClient, FeishuError


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class FeishuRetryTests(unittest.TestCase):
    def make_client(self, responses):
        client = object.__new__(FeishuClient)
        client.settings = SimpleNamespace()
        client._session = FakeSession(responses)
        client.headers = lambda: {"Authorization": "Bearer test"}
        return client

    @patch("api.feishu.time.sleep", return_value=None)
    def test_get_retries_rate_limit_with_bounded_backoff(self, _sleep):
        client = self.make_client([
            FakeResponse(429, {"code": 1254291, "msg": "rate limited"}),
            FakeResponse(200, {"code": 0, "data": {"ok": True}}),
        ])

        result = client._request("GET", "/records")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(client._session.calls), 2)

    @patch("api.feishu.time.sleep", return_value=None)
    def test_non_idempotent_post_is_not_retried(self, _sleep):
        client = self.make_client([
            FakeResponse(503, {"code": 503, "msg": "unavailable"}),
            FakeResponse(200, {"code": 0, "data": {"created": True}}),
        ])

        with self.assertRaises(FeishuError):
            client._request("POST", "/records", json={"fields": {"name": "x"}})

        self.assertEqual(len(client._session.calls), 1)


if __name__ == "__main__":
    unittest.main()
