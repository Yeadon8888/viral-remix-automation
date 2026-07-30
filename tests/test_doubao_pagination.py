import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import app as app_module
from api.feishu import FeishuClient, FeishuError, field_value
from api.services.doubao_assets import DoubaoAssetService
from api.workflow import ViralRemixWorkflow, recent_window_start_ms, record_timestamp_ms


class FeishuPaginationTests(unittest.TestCase):
    def test_field_value_flattens_batch_get_rich_text(self):
        fields = {
            "商品名称": [
                {"text": "第一段", "type": "text"},
                {"text": "第二段", "type": "text"},
            ],
            "商品图": [{"file_token": "file-1", "name": "image.png"}],
        }

        self.assertEqual(field_value(fields, "商品名称"), "第一段第二段")
        self.assertEqual(field_value(fields, "商品图"), fields["商品图"])

    def test_list_records_page_preserves_feishu_cursor(self):
        client = object.__new__(FeishuClient)
        client.settings = SimpleNamespace(feishu_app_token="app-token")
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {
                "items": [{"record_id": "rec-501"}],
                "has_more": True,
                "page_token": "next-cursor",
            }

        client._request = fake_request

        page = client.list_records_page(
            "doubao-table",
            page_size=100,
            page_token="current-cursor",
            sort=["后台创建时间 DESC"],
            field_names=["商品名称"],
        )

        self.assertEqual(page["items"], [{"record_id": "rec-501"}])
        self.assertTrue(page["has_more"])
        self.assertEqual(page["page_token"], "next-cursor")
        self.assertEqual(calls[0][2]["params"], {
            "page_size": 100,
            "page_token": "current-cursor",
            "sort": '["后台创建时间 DESC"]',
            "field_names": '["商品名称"]',
        })

    def test_batch_get_records_requests_only_the_selected_record_ids(self):
        client = object.__new__(FeishuClient)
        client.settings = SimpleNamespace(feishu_app_token="app-token")
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {"records": [{"record_id": "rec-1", "fields": {"商品名称": "商品 1"}}]}

        client._request = fake_request

        records = client.batch_get_records("doubao-table", ["rec-1"])

        self.assertEqual(records[0]["record_id"], "rec-1")
        self.assertEqual(calls, [(
            "POST",
            "/bitable/v1/apps/app-token/tables/doubao-table/records/batch_get",
            {"json": {"record_ids": ["rec-1"]}},
        )])


class DoubaoWorkflowPaginationTests(unittest.TestCase):
    def test_recent_window_uses_shanghai_calendar_days(self):
        now = datetime(2026, 7, 27, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

        cutoff = recent_window_start_ms(2, now=now)

        self.assertEqual(
            cutoff,
            int(datetime(2026, 7, 26, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000),
        )

    def test_record_timestamp_supports_seconds_milliseconds_and_preferred_field(self):
        record = {
            "created_time": "1785074400",
            "last_modified_time": "1785160800000",
            "fields": {"后台创建时间": 1785247200000},
        }

        self.assertEqual(record_timestamp_ms(record, ("后台创建时间", "created_time")), 1785247200000)
        self.assertEqual(record_timestamp_ms(record, ("last_modified_time",)), 1785160800000)
        self.assertEqual(record_timestamp_ms(record, ("created_time",)), 1785074400000)

    def test_list_doubao_items_page_keeps_cursor_and_global_row_number(self):
        class FakeFeishu:
            def list_records_page(self, table_id, page_size, page_token, sort):
                self.call = (table_id, page_size, page_token, sort)
                return {
                    "items": [
                        {
                            "record_id": "rec-501",
                            "fields": {
                                "商品名称": "第 501 个商品",
                                "商品图链接": "https://example.com/501.jpg",
                            },
                        }
                    ],
                    "has_more": True,
                    "page_token": "next-cursor",
                    "total": 2501,
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.r2 = SimpleNamespace(is_managed_url=lambda url: False)
        workflow.doubao_assets = DoubaoAssetService(
            workflow.settings,
            workflow.feishu,
            workflow.r2,
            SimpleNamespace(),
            lambda url: (b"", ""),
        )

        page = workflow.list_doubao_items_page(
            page_size=100,
            page_token="current-cursor",
            start_index=501,
        )

        self.assertEqual(
            workflow.feishu.call,
            ("doubao-table", 100, "current-cursor", ["后台创建时间 DESC"]),
        )
        self.assertEqual(page["items"][0]["行号"], "")
        self.assertEqual(page["items"][0]["商品名称"], "第 501 个商品")
        self.assertTrue(page["has_more"])
        self.assertEqual(page["page_token"], "next-cursor")
        self.assertEqual(page["total"], 2501)

    def test_sorted_pages_do_not_invent_feishu_row_numbers(self):
        class FakeFeishu:
            def list_records_page(self, table_id, page_size, page_token, sort):
                return {
                    "items": [{"record_id": "rec-501", "fields": {"商品名称": "第 501 个商品"}}],
                    "has_more": False,
                    "page_token": "",
                    "total": 2501,
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.r2 = SimpleNamespace(is_managed_url=lambda url: False)
        workflow.doubao_assets = DoubaoAssetService(
            workflow.settings,
            workflow.feishu,
            workflow.r2,
            SimpleNamespace(),
            lambda url: (b"", ""),
        )

        page = workflow.list_doubao_items_page(
            page_size=100,
            page_token="current-cursor",
            start_index=501,
            order="oldest",
        )

        self.assertEqual(page["items"][0]["行号"], "")

    def test_recent_doubao_page_filters_old_records_and_stops_pagination(self):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        recent_time = int(now.timestamp() * 1000)
        old_time = recent_window_start_ms(2, now=now) - 1

        class FakeFeishu:
            def list_records_page(self, table_id, page_size, page_token, sort):
                return {
                    "items": [
                        {
                            "record_id": "recent",
                            "created_time": str(recent_time),
                            "fields": {"商品名称": "最近素材", "后台创建时间": recent_time},
                        },
                        {
                            "record_id": "old",
                            "created_time": str(old_time),
                            "fields": {"商品名称": "旧素材", "后台创建时间": old_time},
                        },
                    ],
                    "has_more": True,
                    "page_token": "next-cursor",
                    "total": 2501,
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.r2 = SimpleNamespace(is_managed_url=lambda url: False)
        workflow.doubao_assets = DoubaoAssetService(
            workflow.settings,
            workflow.feishu,
            workflow.r2,
            SimpleNamespace(),
            lambda url: (b"", ""),
        )

        page = workflow.list_doubao_items_page(days=2)

        self.assertEqual([row["record_id"] for row in page["items"]], ["recent"])
        self.assertFalse(page["has_more"])
        self.assertEqual(page["page_token"], "")
        self.assertEqual(page["total"], 0)

    def test_original_record_order_builds_exact_feishu_row_index(self):
        class FakeFeishu:
            def list_records_page(self, table_id, page_size, page_token="", field_names=None):
                self.call = (table_id, page_size, page_token, field_names)
                return {
                    "items": [{"record_id": "rec-a"}, {"record_id": "rec-c"}],
                    "has_more": False,
                    "page_token": "",
                    "total": 2,
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()

        index = workflow.list_doubao_row_index()

        self.assertEqual(index, {"rec-a": 1, "rec-c": 2})
        self.assertEqual(
            workflow.feishu.call,
            ("doubao-table", 500, "", ["商品名称"]),
        )

    def test_row_range_stops_after_the_page_containing_the_requested_end(self):
        class FakeFeishu:
            def __init__(self):
                self.calls = []
                self.batch_calls = []

            def list_records_page(self, table_id, page_size, page_token="", field_names=None):
                self.calls.append((table_id, page_size, page_token, field_names))
                if not page_token:
                    return {
                        "items": [
                            {"record_id": f"rec-{index}", "fields": {}}
                            for index in range(1, 501)
                        ],
                        "has_more": True,
                        "page_token": "page-2",
                        "total": 1000,
                    }
                return {
                    "items": [
                        {"record_id": f"rec-{index}", "fields": {}}
                        for index in range(501, 1001)
                    ],
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

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.r2 = SimpleNamespace(is_managed_url=lambda _url: False)
        workflow.doubao_assets = SimpleNamespace(describe=lambda _fields: {})

        result = workflow.list_doubao_items_range(501, 502)

        self.assertEqual(
            [(row["record_id"], row["行号"]) for row in result["items"]],
            [("rec-501", 501), ("rec-502", 502)],
        )
        self.assertEqual(result["total"], 1000)
        self.assertEqual(
            workflow.feishu.calls,
            [
                ("doubao-table", 500, "", ["商品名称"]),
                ("doubao-table", 500, "page-2", ["商品名称"]),
            ],
        )
        self.assertEqual(
            workflow.feishu.batch_calls,
            [("doubao-table", ["rec-501", "rec-502"])],
        )

    def test_row_range_reuses_persistent_record_id_index_without_rescanning_feishu(self):
        class FakeFeishu:
            def __init__(self):
                self.page_calls = []
                self.batch_calls = []

            def list_records_page(self, table_id, page_size, page_token="", field_names=None):
                self.page_calls.append((table_id, page_size, page_token, field_names))
                return {
                    "items": [{"record_id": f"rec-{index}"} for index in range(1, 501)],
                    "has_more": True,
                    "page_token": "page-2",
                    "total": 1000,
                }

            def batch_get_records(self, table_id, record_ids):
                self.batch_calls.append((table_id, record_ids))
                return [{"record_id": record_id, "fields": {}} for record_id in record_ids]

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table", redis_url="")
        workflow.feishu = FakeFeishu()
        workflow.r2 = SimpleNamespace(is_managed_url=lambda _url: False)
        workflow.doubao_assets = SimpleNamespace(describe=lambda _fields: {})
        workflow._doubao_record_ids_cache = [f"rec-{index}" for index in range(1, 1001)]

        result = workflow.list_doubao_items_range(501, 502)

        self.assertEqual(
            [(row["record_id"], row["行号"]) for row in result["items"]],
            [("rec-501", 501), ("rec-502", 502)],
        )
        self.assertEqual(len(workflow.feishu.page_calls), 0)
        self.assertEqual(
            workflow.feishu.batch_calls,
            [("doubao-table", ["rec-501", "rec-502"])],
        )

    def test_expired_record_id_index_is_rebuilt_before_serving_row_numbers(self):
        class FakeFeishu:
            def __init__(self):
                self.page_calls = 0

            def list_records_page(self, table_id, page_size, page_token="", field_names=None):
                self.page_calls += 1
                return {
                    "items": [{"record_id": "rec-new"}],
                    "has_more": False,
                    "page_token": "",
                    "total": 1,
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table", redis_url="")
        workflow.feishu = FakeFeishu()
        workflow._doubao_record_ids_cache = ["rec-old"]
        workflow._doubao_record_ids_cached_at = 1

        with patch("api.workflow.time.time", return_value=1000):
            index = workflow.list_doubao_row_index()

        self.assertEqual(index, {"rec-new": 1})
        self.assertEqual(workflow.feishu.page_calls, 1)


class DoubaoPaginationApiTests(unittest.TestCase):
    def setUp(self):
        app_module.clear_list_cache()
        self.client = app_module.app.test_client()

    def test_doubao_page_endpoint_returns_cursor_metadata(self):
        class FakeWorkflow:
            def __init__(self):
                self.call = None

            def list_doubao_items_page(self, page_size, page_token, start_index, order, days):
                self.call = (page_size, page_token, start_index, order, days)
                return {
                    "items": [{"record_id": "rec-501", "行号": start_index}],
                    "has_more": True,
                    "page_token": "next-cursor",
                    "total": 2501,
                }

        fake_workflow = FakeWorkflow()
        settings = SimpleNamespace(admin_token="")
        with patch.object(app_module, "workflow", fake_workflow), patch.object(app_module, "settings", settings):
            response = self.client.get(
                "/api/doubao-items/page?page_size=100&page_token=current-cursor&offset=500&order=oldest&days=7&force=1"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_workflow.call, (100, "current-cursor", 501, "oldest", 7))
        payload = response.get_json()
        self.assertEqual(payload["data"], [{"record_id": "rec-501", "行号": 501}])
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["page_token"], "next-cursor")
        self.assertEqual(payload["total"], 2501)

    def test_doubao_range_endpoint_returns_only_requested_real_rows(self):
        class FakeWorkflow:
            def __init__(self):
                self.call = None

            def list_doubao_items_range(self, start_row, end_row, force_refresh=False):
                self.call = (start_row, end_row, force_refresh)
                return {
                    "items": [
                        {"record_id": "rec-6453", "行号": 6453},
                        {"record_id": "rec-6552", "行号": 6552},
                    ],
                    "total": 7038,
                }

        fake_workflow = FakeWorkflow()
        settings = SimpleNamespace(admin_token="")
        with patch.object(app_module, "workflow", fake_workflow), patch.object(app_module, "settings", settings):
            response = self.client.get(
                "/api/doubao-items/range?start_row=6453&end_row=6552&force=1"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_workflow.call, (6453, 6552, True))
        payload = response.get_json()
        self.assertEqual(
            payload["data"],
            [
                {"record_id": "rec-6453", "行号": 6453},
                {"record_id": "rec-6552", "行号": 6552},
            ],
        )
        self.assertEqual(payload["total"], 7038)

    def test_row_index_endpoint_returns_original_feishu_positions(self):
        class FakeWorkflow:
            def __init__(self):
                self.force_refresh = None

            def list_doubao_row_index(self, force_refresh=False):
                self.force_refresh = force_refresh
                return {"rec-a": 1, "rec-c": 2}

        fake_workflow = FakeWorkflow()
        settings = SimpleNamespace(admin_token="")
        with (
            patch.object(app_module, "workflow", fake_workflow),
            patch.object(app_module, "settings", settings),
        ):
            response = self.client.get("/api/doubao-items/row-index?force=1")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(fake_workflow.force_refresh)
        self.assertEqual(response.get_json()["data"], {"rec-a": 1, "rec-c": 2})
        self.assertEqual(response.get_json()["total"], 2)

    def test_feishu_page_failure_returns_json_service_error(self):
        class FakeWorkflow:
            def list_doubao_items_page(self, **_kwargs):
                raise FeishuError("Data not ready, please try again later")

        settings = SimpleNamespace(admin_token="")
        with patch.object(app_module, "workflow", FakeWorkflow()), patch.object(app_module, "settings", settings):
            response = self.client.get("/api/doubao-items/page?force=1")

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.is_json)
        self.assertEqual(response.get_json()["success"], False)
        self.assertIn("飞书数据暂未准备好", response.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
