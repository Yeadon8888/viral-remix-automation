import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api.feishu import FeishuClient, FeishuError


class TransientReadTests(unittest.TestCase):
    def client(self, responses):
        client = FeishuClient(SimpleNamespace())
        client.headers = Mock(return_value={})
        client._session = Mock()
        client._session.request.side_effect = [
            Mock(status_code=200, json=Mock(return_value=data)) for data in responses
        ]
        return client

    @patch("api.feishu.time.sleep")
    def test_chinese_not_ready_recovers_after_old_retry_limit(self, sleep):
        client = self.client(
            [{"code": 1, "msg": "上游数据尚未准备好，请稍后重试"}] * 5
            + [{"code": 0, "data": {"record_id": "rec"}}]
        )
        self.assertEqual(client._request("GET", "/record"), {"record_id": "rec"})
        self.assertEqual(sleep.call_count, 5)

    @patch("api.feishu.time.sleep")
    def test_batch_read_retries_but_create_does_not(self, sleep):
        error = {"code": 1, "msg": "data not ready"}
        client = self.client([error, {"code": 0, "data": {"records": []}}])
        self.assertEqual(client._request("POST", "/records/batch_get"), {"records": []})
        client = self.client([error])
        with self.assertRaises(FeishuError):
            client._request("POST", "/records")
        self.assertEqual(client._session.request.call_count, 1)

    @patch("api.feishu.time.sleep")
    def test_retry_budget_is_bounded(self, sleep):
        client = self.client([{"code": 1, "msg": "data not ready"}] * 8)
        with self.assertRaises(FeishuError):
            client._request("GET", "/record")
        self.assertEqual(client._session.request.call_count, 8)
        self.assertLessEqual(max(call.args[0] for call in sleep.call_args_list), 8)

    @patch("api.feishu.time.sleep")
    def test_permission_errors_fail_without_retry(self, sleep):
        client = self.client([{"code": 999, "msg": "permission denied"}])
        with self.assertRaises(FeishuError):
            client._request("GET", "/record")
        sleep.assert_not_called()
