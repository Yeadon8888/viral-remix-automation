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
        records = [{"record_id": "prompt-1", "fields": fields}]
        updates = []

        class FakeFeishu:
            def list_records(self, table_id):
                return [
                    {"record_id": row["record_id"], "fields": dict(row["fields"])}
                    for row in records
                ]

            def get_record(self, table_id, record_id):
                row = next(row for row in records if row["record_id"] == record_id)
                return {"record_id": record_id, "fields": dict(row["fields"])}

            def update_record(self, table_id, record_id, patch):
                updates.append(dict(patch))
                row = next(row for row in records if row["record_id"] == record_id)
                row["fields"].update(patch)
                return {"record_id": record_id, "fields": dict(row["fields"])}

            def create_record(self, table_id, new_fields):
                row = {"record_id": f"prompt-{len(records) + 1}", "fields": dict(new_fields)}
                records.append(row)
                return {"record_id": row["record_id"], "fields": dict(row["fields"])}

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(prompt_table_id="prompt-table")
        workflow.feishu = FakeFeishu()
        return workflow, fields, updates, records

    def test_prompt_list_exposes_revision(self):
        workflow, _, _, _ = self.make_workflow()

        rows = workflow.list_prompt_configs()

        self.assertRegex(rows[0]["revision"], r"^[0-9a-f]{64}$")

    def test_stale_prompt_revision_is_rejected_before_write(self):
        workflow, _, updates, _ = self.make_workflow()
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
        workflow, _, updates, _ = self.make_workflow()
        current = workflow.list_prompt_configs()[0]["revision"]

        workflow.update_prompt_config(
            "prompt-1",
            {"模板内容": "用户的新模板"},
            expected_revision=current,
        )

        self.assertEqual(updates[0]["模板内容"], "用户的新模板")

    def test_new_prompt_is_saved_as_draft_by_default(self):
        workflow, _, _, records = self.make_workflow()

        created = workflow.create_prompt_config({
            "Prompt名称": "豆包视频-墨西哥-方案二",
            "版本": "mx-v2",
            "目标地区": "墨西哥",
            "模板内容": "方案二内容 {product_name}",
        })

        self.assertEqual(created["record_id"], "prompt-2")
        self.assertEqual(records[1]["fields"]["Prompt类型"], "豆包视频")
        self.assertEqual(records[1]["fields"]["模板Key"], "doubao_seedance_video_prompt")
        self.assertEqual(records[1]["fields"]["是否启用"], "停用")

    def test_enabling_new_prompt_disables_active_sibling(self):
        workflow, fields, updates, records = self.make_workflow()

        workflow.create_prompt_config({
            "Prompt名称": "豆包视频-墨西哥-方案二",
            "版本": "mx-v2",
            "是否启用": "启用",
            "目标地区": "墨西哥",
            "模板内容": "方案二内容",
        })

        self.assertEqual(fields["是否启用"], "停用")
        self.assertEqual(records[1]["fields"]["是否启用"], "启用")
        self.assertIn({"是否启用": "停用"}, updates)

    def test_duplicate_version_in_same_country_is_rejected(self):
        workflow, _, _, records = self.make_workflow()

        with self.assertRaisesRegex(ValueError, "版本.*已存在"):
            workflow.create_prompt_config({
                "Prompt名称": "重复版本",
                "版本": "mx-v1",
                "目标地区": "墨西哥",
                "模板内容": "内容",
            })

        self.assertEqual(len(records), 1)

    def test_enabling_draft_by_edit_disables_active_sibling(self):
        workflow, fields, _, records = self.make_workflow()
        created = workflow.create_prompt_config({
            "Prompt名称": "豆包视频-墨西哥-方案二",
            "版本": "mx-v2",
            "目标地区": "墨西哥",
            "模板内容": "方案二内容",
        })

        workflow.update_prompt_config(
            created["record_id"],
            {"是否启用": "启用"},
            expected_revision=created["revision"],
        )

        self.assertEqual(fields["是否启用"], "停用")
        self.assertEqual(records[1]["fields"]["是否启用"], "启用")


if __name__ == "__main__":
    unittest.main()
