import unittest
from types import SimpleNamespace

from api.workflow import ViralRemixWorkflow


class DoubaoPublishSyncTests(unittest.TestCase):
    def test_completed_part_time_videos_are_joined_with_doubao_content_and_upserted(self):
        calls = {"created": [], "updated": []}

        class FakeFeishu:
            def list_records(self, table_id, **kwargs):
                if table_id == "doubao":
                    return [{
                        "record_id": "source-1",
                        "fields": {
                            "商品名称": "Travel bag",
                            "标题": "A useful travel bag",
                            "文案": "Carry everything with ease.",
                            "标签": "#Travel #Bag",
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

        readiness = workflow.doubao_publish_readiness(["source-1", "source-missing"])
        result = workflow.sync_doubao_publish_items(["source-1"])

        self.assertEqual(readiness, {
            "selected_sources": 2,
            "ready_sources": 1,
            "eligible_videos": 2,
            "pending_sources": 1,
        })
        self.assertEqual(result, {"eligible": 2, "created": 1, "updated": 1, "skipped": 0})
        created_fields = calls["created"][0][1]
        self.assertEqual(created_fields["商品标题"], "Travel bag")
        self.assertEqual(created_fields["标题"], "A useful travel bag")
        self.assertEqual(created_fields["文案"], "Carry everything with ease.")
        self.assertEqual(created_fields["标签"], "#Travel #Bag")
        self.assertEqual(created_fields["视频链接"], "https://cdn.example/two.mp4")
        self.assertEqual(created_fields["豆包素材记录ID"], "source-1")
        self.assertEqual(created_fields["豆包兼职记录ID"], "part-2")
        self.assertEqual(created_fields["发布状态"], "待发布")

        updated_fields = calls["updated"][0][2]
        self.assertEqual(updated_fields["豆包兼职记录ID"], "part-1")
        self.assertNotIn("发布状态", updated_fields)
        self.assertNotIn("生成时间", updated_fields)


if __name__ == "__main__":
    unittest.main()
