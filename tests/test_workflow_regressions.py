import unittest
from types import SimpleNamespace

from api.services.publish_sync import PublishSyncService
from api.workflow import ViralRemixWorkflow


class WorkflowRegressionTests(unittest.TestCase):
    def test_existing_publish_record_keeps_its_release_state(self):
        updates = []

        class FakeFeishu:
            def list_records(self, table_id):
                return [{
                    "record_id": "publish-1",
                    "fields": {
                        "视频链接": "https://cdn.example.com/video.mp4",
                        "发布状态": "已发布",
                    },
                }]

            def update_record(self, table_id, record_id, fields):
                updates.append(dict(fields))
                return {"record_id": record_id, "fields": fields}

        workflow = PublishSyncService(
            SimpleNamespace(publish_table_id="publish"),
            FakeFeishu(),
            SimpleNamespace(),
            list_tasks=lambda limit: [{
                "record_id": "task-1",
                "生成任务名称": "Travel bag - 视频任务 1",
                "生成视频链接": "https://cdn.example.com/video.mp4",
                "生成标题": "Travel bag",
                "生成文案": "Ready to travel",
                "关键词": "#travel",
                "关联素材记录IDs": ["source-1"],
            }],
        )

        result = workflow.sync_publish_items(["task-1"])

        self.assertEqual(result["updated"], 1)
        self.assertNotIn("发布状态", updates[0])
        self.assertNotIn("生成时间", updates[0])

    def test_regenerated_reference_image_updates_the_existing_video_task(self):
        calls = {"created": [], "updated": []}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {
                    "record_id": record_id,
                    "fields": {
                        "商品名称": "Travel bag",
                        "地区": "墨西哥",
                        "商品组图链接": "https://cdn.example.com/new-reference.png",
                        "视频生成Prompt": "Create a short product video",
                        "商品组图模板": ["viral_first_frame"],
                    },
                }

            def list_records(self, table_id):
                return [{
                    "record_id": "task-existing",
                    "fields": {
                        "来源素材记录ID": "source-1",
                        "任务序号": 1,
                        "首帧/参考图链接": "https://cdn.example.com/old-reference.png",
                    },
                }]

            def update_record(self, table_id, record_id, fields):
                calls["updated"].append((table_id, record_id, dict(fields)))
                return {"record_id": record_id, "fields": fields}

            def create_record(self, table_id, fields):
                calls["created"].append((table_id, dict(fields)))
                return {"record_id": "task-new", "fields": fields}

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(
            source_table_id="source",
            task_table_id="tasks",
            image_scene_styles=("viral_first_frame",),
        )
        workflow.feishu = FakeFeishu()

        result = workflow.create_video_tasks("source-1")

        self.assertEqual(calls["created"], [])
        self.assertEqual(result["updated_task_ids"], ["task-existing"])
        task_update = next(fields for table, _, fields in calls["updated"] if table == "tasks")
        self.assertEqual(
            task_update["首帧/参考图链接"]["link"],
            "https://cdn.example.com/new-reference.png",
        )


if __name__ == "__main__":
    unittest.main()
