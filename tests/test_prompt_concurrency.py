import unittest
from types import SimpleNamespace

from api.workflow import ViralRemixWorkflow


class PromptConcurrencyTests(unittest.TestCase):
    def make_workflow(self):
        fields = {
            "Prompt名称": "豆包视频-Seedance-墨西哥西班牙语",
            "Prompt类型": "豆包视频",
            "模板Key": "doubao_seedance_video_prompt",
            "版本": "mx-v1",
            "是否启用": "启用",
            "目标地区": "墨西哥",
            "模板内容": "原始模板内容",
            "变量说明": "变量",
            "备注": "备注",
        }
        updates = []

        class FakeFeishu:
            def list_records(self, table_id):
                return [{"record_id": "prompt-1", "fields": dict(fields)}]

            def get_record(self, table_id, record_id):
                return {"record_id": record_id, "fields": dict(fields)}

            def update_record(self, table_id, record_id, patch):
                updates.append(dict(patch))
                fields.update(patch)
                return {"record_id": record_id, "fields": dict(fields)}

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(prompt_table_id="prompt-table")
        workflow.feishu = FakeFeishu()
        return workflow, fields, updates

    def test_prompt_list_exposes_revision(self):
        workflow, _, _ = self.make_workflow()

        rows = workflow.list_prompt_configs()

        self.assertRegex(rows[0]["revision"], r"^[0-9a-f]{64}$")

    def test_stale_prompt_revision_is_rejected_before_write(self):
        workflow, _, updates = self.make_workflow()
        try:
            from api.workflow import PromptConflictError
        except ImportError as exc:
            self.fail(f"缺少 Prompt 冲突错误: {exc}")

        with self.assertRaises(PromptConflictError):
            workflow.update_prompt_config(
                "prompt-1",
                {"模板内容": "用户 A 的旧页面修改"},
                expected_revision="stale-revision",
            )

        self.assertEqual(updates, [])

    def test_current_prompt_revision_can_be_saved(self):
        workflow, _, updates = self.make_workflow()
        current = workflow.list_prompt_configs()[0]["revision"]

        workflow.update_prompt_config(
            "prompt-1",
            {"模板内容": "用户的新模板"},
            expected_revision=current,
        )

        self.assertEqual(updates[0]["模板内容"], "用户的新模板")


if __name__ == "__main__":
    unittest.main()

