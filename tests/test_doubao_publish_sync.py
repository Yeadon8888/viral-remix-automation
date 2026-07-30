import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from api.workflow import ViralRemixWorkflow, recent_window_start_ms


class DoubaoPublishSyncTests(unittest.TestCase):
    def test_completed_part_time_videos_are_joined_with_doubao_content_and_upserted(self):
        calls = {"created": [], "updated": []}

        class FakeFeishu:
            def list_records_filtered(self, table_id, **kwargs):
                return self.list_records(table_id)

            def list_records(self, table_id, **kwargs):
                if table_id == "doubao":
                    return [{
                        "record_id": "source-1",
                        "fields": {
                            "商品id": "1732254358741092079",
                            "商品名称": "Travel bag",
                            "标题": "A useful travel bag",
                            "文案": "Carry everything with ease.",
                            "标签": "#Travel #Bag",
                            "最终视频生成提示词": "Create a product video",
                            "目标国家": "墨西哥",
                        },
                    }]
                if table_id == "part-time":
                    return [
                        {
                            "record_id": "part-1",
                            "fields": {
                                "来源记录ID": "source-1",
                                "任务状态": "已完成",
                                "审核": "有效链接",
                                "视频地址": "[video](https://cdn.example/one.mp4)",
                            },
                        },
                        {
                            "record_id": "part-2",
                            "fields": {
                                "来源记录ID": "source-1",
                                "任务状态": "已完成",
                                "视频地址": "https://cdn.example/two.mp4",
                            },
                        },
                        {
                            "record_id": "part-invalid",
                            "fields": {
                                "来源记录ID": "source-1",
                                "任务状态": "已完成",
                                "审核": "无效链接",
                                "视频地址": "https://cdn.example/invalid.mp4",
                            },
                        },
                    ]
                if table_id == "publish":
                    return [{
                        "record_id": "publish-existing",
                        "fields": {
                            "豆包兼职记录ID": "part-1",
                            "视频链接": "https://cdn.example/one.mp4",
                            "发布状态": "已发布",
                        },
                    }]
                return []

            def create_record(self, table_id, fields):
                calls["created"].append((table_id, dict(fields)))
                return {"record_id": "publish-new", "fields": fields}

            def update_record(self, table_id, record_id, fields):
                calls["updated"].append((table_id, record_id, dict(fields)))
                return {"record_id": record_id, "fields": fields}

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(
            doubao_table_id="doubao",
            part_time_table_id="part-time",
            publish_table_id="publish",
        )
        workflow.feishu = FakeFeishu()
        workflow.doubao_assets = SimpleNamespace(
            describe=lambda fields: {
                "人脸检测通过": True,
                "发布前置条件通过": True,
            }
        )

        readiness = workflow.doubao_publish_readiness(["source-1", "source-missing"])
        result = workflow.sync_doubao_publish_items(["source-1", "source-missing"])

        self.assertEqual(readiness, {
            "selected_sources": 2,
            "ready_sources": 1,
            "eligible_videos": 2,
            "pending_sources": 1,
            "blocked_face_sources": 1,
            "missing_product_id_sources": 1,
            "missing_prompt_sources": 1,
            "missing_video_sources": 1,
        })
        self.assertEqual(result["eligible"], 2)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 1)
        failure = next(item for item in result["results"] if item["status"] == "failed")
        self.assertEqual(failure["record_id"], "source-missing")
        self.assertEqual(failure["stage"], "读取素材")
        created_fields = calls["created"][0][1]
        self.assertEqual(created_fields["商品标题"], "Travel bag")
        self.assertEqual(created_fields["商品ID"], "1732254358741092079")
        self.assertEqual(created_fields["标题"], "A useful travel bag")
        self.assertEqual(created_fields["文案"], "Carry everything with ease.")
        self.assertEqual(created_fields["标签"], "#Travel #Bag")
        self.assertEqual(created_fields["视频链接"], "https://cdn.example/two.mp4")
        self.assertEqual(created_fields["豆包素材记录ID"], "source-1")
        self.assertEqual(created_fields["豆包兼职记录ID"], "part-2")
        self.assertEqual(created_fields["目标国家"], "墨西哥")
        self.assertEqual(created_fields["发布状态"], "待发布")

        updated_fields = calls["updated"][0][2]
        self.assertEqual(updated_fields["豆包兼职记录ID"], "part-1")
        self.assertNotIn("发布状态", updated_fields)
        self.assertNotIn("生成时间", updated_fields)


class PartTimePublishSyncTests(unittest.TestCase):
    def build_workflow(self):
        calls = {"created": [], "updated": []}

        class FakeFeishu:
            def list_records_filtered(self, table_id, **kwargs):
                return self.list_records(table_id)

            def list_records(self, table_id, **kwargs):
                if table_id == "doubao":
                    return [
                        {
                            "record_id": "source-ready",
                            "fields": {
                                "商品id": "product-1",
                                "商品名称": "Ready product",
                                "最终视频生成提示词": "Create a video",
                                "目标国家": "墨西哥",
                            },
                        },
                        {
                            "record_id": "source-blocked",
                            "fields": {
                                "商品id": "product-2",
                                "商品名称": "Blocked product",
                                "最终视频生成提示词": "Create a video",
                            },
                        },
                    ]
                if table_id == "part-time":
                    return [
                        {
                            "record_id": "part-ready",
                            "fields": {
                                "来源记录ID": "source-ready",
                                "文本": "Ready video",
                                "任务状态": "已完成",
                                "审核": "有效链接",
                                "视频地址": "https://cdn.example/ready.mp4",
                                "目标国家": "墨西哥",
                                "目标语言": "西班牙语",
                            },
                        },
                        {
                            "record_id": "part-published",
                            "fields": {
                                "来源记录ID": "source-ready",
                                "文本": "Published video",
                                "任务状态": "已完成",
                                "审核": "有效链接",
                                "视频地址": "https://cdn.example/published.mp4",
                            },
                        },
                        {
                            "record_id": "part-invalid",
                            "fields": {
                                "来源记录ID": "source-ready",
                                "任务状态": "已完成",
                                "审核": "无效链接",
                                "视频地址": "https://cdn.example/invalid.mp4",
                            },
                        },
                        {
                            "record_id": "part-source-blocked",
                            "fields": {
                                "来源记录ID": "source-blocked",
                                "任务状态": "已完成",
                                "视频地址": "https://cdn.example/blocked.mp4",
                            },
                        },
                    ]
                if table_id == "publish":
                    return [{
                        "record_id": "publish-existing",
                        "fields": {
                            "豆包兼职记录ID": "part-published",
                            "视频链接": "https://cdn.example/published.mp4",
                            "发布状态": "待发布",
                        },
                    }]
                return []

            def create_record(self, table_id, fields):
                calls["created"].append((table_id, dict(fields)))
                return {"record_id": "publish-new", "fields": fields}

            def update_record(self, table_id, record_id, fields):
                calls["updated"].append((table_id, record_id, dict(fields)))
                return {"record_id": record_id, "fields": fields}

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(
            doubao_table_id="doubao",
            part_time_table_id="part-time",
            publish_table_id="publish",
        )
        workflow.feishu = FakeFeishu()
        workflow.doubao_assets = SimpleNamespace(
            describe=lambda fields: {
                "发布前置条件通过": fields.get("商品id") == "product-1",
            }
        )
        return workflow, calls

    def test_part_time_page_marks_existing_and_invalid_records(self):
        workflow, _ = self.build_workflow()

        rows = workflow.list_part_time_publish_items()

        by_id = {row["record_id"]: row for row in rows}
        self.assertEqual(by_id["part-ready"]["行号"], 1)
        self.assertEqual(by_id["part-ready"]["发布状态"], "待审核")
        self.assertTrue(by_id["part-ready"]["可同步发布"])
        self.assertEqual(by_id["part-published"]["发布状态"], "待发布")
        self.assertTrue(by_id["part-published"]["已同步发布"])
        self.assertFalse(by_id["part-invalid"]["可同步发布"])
        self.assertEqual(by_id["part-invalid"]["发布阻断原因"], "兼职表审核标记为无效链接")

    def test_selected_part_time_rows_are_revalidated_and_idempotently_published(self):
        workflow, calls = self.build_workflow()

        result = workflow.sync_part_time_publish_items([
            "part-ready",
            "part-published",
            "part-invalid",
            "part-source-blocked",
        ])

        self.assertEqual(result["processed"], 4)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["failed"], 2)
        self.assertEqual(calls["updated"][0][1], "publish-existing")
        self.assertEqual(calls["created"][0][1]["豆包兼职记录ID"], "part-ready")
        self.assertEqual(calls["created"][0][1]["商品ID"], "product-1")
        self.assertEqual(calls["created"][0][1]["目标国家"], "墨西哥")
        self.assertEqual(calls["updated"][0][2]["商品ID"], "product-1")
        self.assertEqual(calls["updated"][0][2]["目标国家"], "墨西哥")
        failures = {item["record_id"]: item["message"] for item in result["results"] if item["status"] == "failed"}
        self.assertIn("无效链接", failures["part-invalid"])
        self.assertIn("素材前置条件", failures["part-source-blocked"])

    def test_part_time_page_is_not_capped_at_500_and_keeps_real_row_numbers(self):
        total = 620

        class FakeFeishu:
            def list_records(self, table_id, **kwargs):
                if table_id == "part-time":
                    return [
                        {
                            "record_id": f"part-{index}",
                            "fields": {
                                "来源记录ID": f"source-{index}",
                                "任务状态": "已完成",
                                "视频地址": f"https://cdn.example/{index}.mp4",
                            },
                        }
                        for index in range(1, total + 1)
                    ]
                return []

            def list_records_filtered(self, table_id, **kwargs):
                return []

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(
            part_time_table_id="part-time",
            publish_table_id="publish",
        )
        workflow.feishu = FakeFeishu()

        rows = workflow.list_part_time_publish_items()

        self.assertEqual(len(rows), total)
        self.assertEqual(rows[0]["行号"], 1)
        self.assertEqual(rows[-1]["行号"], total)

    def test_part_time_date_range_uses_recent_update_time_and_keeps_real_row_number(self):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        recent_time = int(now.timestamp() * 1000)
        old_time = recent_window_start_ms(2, now=now) - 1

        class FakeFeishu:
            def list_records(self, table_id, **kwargs):
                if table_id != "part-time":
                    return []
                return [
                    {
                        "record_id": "old",
                        "last_modified_time": str(old_time),
                        "fields": {
                            "任务状态": "已完成",
                            "视频地址": "https://cdn.example/old.mp4",
                        },
                    },
                    {
                        "record_id": "recent",
                        "fields": {
                            "任务状态": "已完成",
                            "视频地址": "https://cdn.example/recent.mp4",
                            "完成时间": recent_time,
                        },
                    },
                    {
                        "record_id": "missing-time",
                        "fields": {
                            "任务状态": "已完成",
                            "视频地址": "https://cdn.example/missing.mp4",
                        },
                    },
                ]

            def list_records_filtered(self, table_id, **kwargs):
                return []

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(
            part_time_table_id="part-time",
            publish_table_id="publish",
        )
        workflow.feishu = FakeFeishu()

        rows = workflow.list_part_time_publish_items(days=2)

        self.assertEqual([row["record_id"] for row in rows], ["recent"])
        self.assertEqual(rows[0]["行号"], 2)


if __name__ == "__main__":
    unittest.main()
