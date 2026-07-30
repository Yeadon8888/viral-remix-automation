import unittest
from types import SimpleNamespace

from api.services.doubao_record_index import DoubaoRecordIndex


class DoubaoRecordIndexTests(unittest.TestCase):
    def test_original_record_order_builds_exact_row_numbers(self):
        class FakeFeishu:
            def __init__(self):
                self.call = None

            def list_records_page(self, table_id, page_size, page_token="", field_names=None):
                self.call = (table_id, page_size, page_token, field_names)
                return {
                    "items": [{"record_id": "rec-a"}, {"record_id": "rec-c"}],
                    "has_more": False,
                    "page_token": "",
                    "total": 2,
                }

        feishu = FakeFeishu()
        index = DoubaoRecordIndex(
            SimpleNamespace(doubao_table_id="doubao-table", redis_url=""),
            feishu,
        )

        self.assertEqual(index.row_numbers(), {"rec-a": 1, "rec-c": 2})
        self.assertEqual(
            feishu.call,
            ("doubao-table", 500, "", ["商品名称"]),
        )

    def test_range_fetches_only_selected_details_and_preserves_row_order(self):
        class FakeFeishu:
            def __init__(self):
                self.page_calls = []
                self.batch_calls = []

            def list_records_page(self, table_id, page_size, page_token="", field_names=None):
                self.page_calls.append((table_id, page_size, page_token, field_names))
                if not page_token:
                    return {
                        "items": [{"record_id": f"rec-{index}"} for index in range(1, 501)],
                        "has_more": True,
                        "page_token": "page-2",
                        "total": 1000,
                    }
                return {
                    "items": [{"record_id": f"rec-{index}"} for index in range(501, 1001)],
                    "has_more": False,
                    "page_token": "",
                    "total": 1000,
                }

            def batch_get_records(self, table_id, record_ids):
                self.batch_calls.append((table_id, record_ids))
                return [
                    {"record_id": record_id, "fields": {}}
                    for record_id in reversed(record_ids)
                ]

        feishu = FakeFeishu()
        index = DoubaoRecordIndex(
            SimpleNamespace(doubao_table_id="doubao-table", redis_url=""),
            feishu,
        )

        result = index.range(501, 502)

        self.assertEqual(
            [record["record_id"] for record in result.records],
            ["rec-501", "rec-502"],
        )
        self.assertEqual(result.row_numbers, {"rec-501": 501, "rec-502": 502})
        self.assertEqual(result.total, 1000)
        self.assertEqual(
            feishu.page_calls,
            [
                ("doubao-table", 500, "", ["商品名称"]),
                ("doubao-table", 500, "page-2", ["商品名称"]),
            ],
        )
        self.assertEqual(
            feishu.batch_calls,
            [("doubao-table", ["rec-501", "rec-502"])],
        )

    def test_range_reuses_a_fresh_index_without_rescanning_feishu(self):
        class FakeFeishu:
            def __init__(self):
                self.page_calls = 0
                self.batch_calls = []

            def list_records_page(self, table_id, page_size, page_token="", field_names=None):
                self.page_calls += 1
                return {
                    "items": [{"record_id": f"rec-{index}"} for index in range(1, 1001)],
                    "has_more": False,
                    "page_token": "",
                    "total": 1000,
                }

            def batch_get_records(self, table_id, record_ids):
                self.batch_calls.append((table_id, record_ids))
                return [{"record_id": record_id, "fields": {}} for record_id in record_ids]

        feishu = FakeFeishu()
        index = DoubaoRecordIndex(
            SimpleNamespace(doubao_table_id="doubao-table", redis_url=""),
            feishu,
            clock=lambda: 100,
        )
        index.row_numbers()
        feishu.page_calls = 0

        result = index.range(501, 502)

        self.assertEqual(
            [record["record_id"] for record in result.records],
            ["rec-501", "rec-502"],
        )
        self.assertEqual(feishu.page_calls, 0)

    def test_expired_index_is_rebuilt_before_serving_row_numbers(self):
        now = [100.0]

        class FakeFeishu:
            def __init__(self):
                self.record_id = "rec-old"
                self.page_calls = 0

            def list_records_page(self, table_id, page_size, page_token="", field_names=None):
                self.page_calls += 1
                return {
                    "items": [{"record_id": self.record_id}],
                    "has_more": False,
                    "page_token": "",
                    "total": 1,
                }

        feishu = FakeFeishu()
        index = DoubaoRecordIndex(
            SimpleNamespace(doubao_table_id="doubao-table", redis_url=""),
            feishu,
            clock=lambda: now[0],
        )
        self.assertEqual(index.row_numbers(), {"rec-old": 1})
        feishu.record_id = "rec-new"
        now[0] = 1000

        self.assertEqual(index.row_numbers(), {"rec-new": 1})
        self.assertEqual(feishu.page_calls, 2)


if __name__ == "__main__":
    unittest.main()
