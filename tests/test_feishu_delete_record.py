import unittest
from types import SimpleNamespace

from api.feishu import FeishuClient


class FeishuDeleteRecordTests(unittest.TestCase):
    def test_delete_record_uses_bitable_record_endpoint(self):
        client = object.__new__(FeishuClient)
        client.settings = SimpleNamespace(feishu_app_token="base-token")
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {}

        client._request = request

        result = client.delete_record("table-1", "record-1")

        self.assertEqual(result, {"record_id": "record-1", "deleted": True})
        self.assertEqual(
            calls,
            [(
                "DELETE",
                "/bitable/v1/apps/base-token/tables/table-1/records/record-1",
                {},
            )],
        )


if __name__ == "__main__":
    unittest.main()
