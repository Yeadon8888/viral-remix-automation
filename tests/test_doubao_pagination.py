import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from api.feishu import FeishuClient
from api.workflow import ViralRemixWorkflow


class FeishuPaginationTests(unittest.TestCase):
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

        page = client.list_records_page("doubao-table", page_size=100, page_token="current-cursor")

        self.assertEqual(page["items"], [{"record_id": "rec-501"}])
        self.assertTrue(page["has_more"])
        self.assertEqual(page["page_token"], "next-cursor")
        self.assertEqual(calls[0][2]["params"], {"page_size": 100, "page_token": "current-cursor"})


class DoubaoWorkflowPaginationTests(unittest.TestCase):
    def test_list_doubao_items_page_keeps_cursor_and_global_row_number(self):
        class FakeFeishu:
            def list_records_page(self, table_id, page_size, page_token):
                self.call = (table_id, page_size, page_token)
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
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()

        page = workflow.list_doubao_items_page(
            page_size=100,
            page_token="current-cursor",
            start_index=501,
        )

        self.assertEqual(workflow.feishu.call, ("doubao-table", 100, "current-cursor"))
        self.assertEqual(page["items"][0]["行号"], 501)
        self.assertEqual(page["items"][0]["商品名称"], "第 501 个商品")
        self.assertTrue(page["has_more"])
        self.assertEqual(page["page_token"], "next-cursor")


class DoubaoPaginationApiTests(unittest.TestCase):
    def setUp(self):
        app_module.clear_list_cache()
        self.client = app_module.app.test_client()

    def test_doubao_page_endpoint_returns_cursor_metadata(self):
        class FakeWorkflow:
            def __init__(self):
                self.call = None

            def list_doubao_items_page(self, page_size, page_token, start_index):
                self.call = (page_size, page_token, start_index)
                return {
                    "items": [{"record_id": "rec-501", "行号": start_index}],
                    "has_more": True,
                    "page_token": "next-cursor",
                }

        fake_workflow = FakeWorkflow()
        settings = SimpleNamespace(admin_token="")
        with patch.object(app_module, "workflow", fake_workflow), patch.object(app_module, "settings", settings):
            response = self.client.get(
                "/api/doubao-items/page?page_size=100&page_token=current-cursor&offset=500&force=1"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_workflow.call, (100, "current-cursor", 501))
        payload = response.get_json()
        self.assertEqual(payload["data"], [{"record_id": "rec-501", "行号": 501}])
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["page_token"], "next-cursor")


if __name__ == "__main__":
    unittest.main()
