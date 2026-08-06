import unittest
from types import SimpleNamespace

from api.services.prompt_configs import (
    DOUBAO_PROMPT_KEY,
    PromptConfigService,
    PromptConflictError,
)


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

        service = PromptConfigService(
            SimpleNamespace(prompt_table_id="prompt-table"),
            FakeFeishu(),
        )
        return service, fields, updates, records

    def test_prompt_list_exposes_revision(self):
        workflow, _, _, _ = self.make_workflow()

        rows = workflow.list_configs()

        self.assertRegex(rows[0]["revision"], r"^[0-9a-f]{64}$")

    def test_active_country_template_overrides_the_fallback(self):
        workflow, _, _, _ = self.make_workflow()

        template, version = workflow.active_template(
            DOUBAO_PROMPT_KEY,
            "墨西哥",
            "fallback",
        )

        self.assertEqual(template, "原始模板内容")
        self.assertEqual(version, f"{DOUBAO_PROMPT_KEY}:mx-v1")

    def test_legacy_active_template_ignores_other_schemes(self):
        workflow, _, _, records = self.make_workflow()
        records.append({
            "record_id": "prompt-t2",
            "fields": {
                "Prompt名称": "T2",
                "Prompt类型": "豆包视频",
                "模板Key": DOUBAO_PROMPT_KEY,
                "方案Key": "pain_physical_proof",
                "版本": "t2-v1",
                "是否启用": "启用",
                "目标地区": "墨西哥",
                "模板内容": "T2 不应覆盖旧请求",
            },
        })

        template, version = workflow.active_template(
            DOUBAO_PROMPT_KEY,
            "墨西哥",
            "fallback",
        )

        self.assertEqual(template, "原始模板内容")
        self.assertEqual(version, f"{DOUBAO_PROMPT_KEY}:mx-v1")

    def test_stale_prompt_revision_is_rejected_before_write(self):
        workflow, _, updates, _ = self.make_workflow()
        with self.assertRaises(PromptConflictError):
            workflow.update(
                "prompt-1",
                {"模板内容": "用户 A 的旧页面修改"},
                expected_revision="stale-revision",
            )

        self.assertEqual(updates, [])

    def test_enabled_prompt_content_is_immutable(self):
        workflow, _, updates, _ = self.make_workflow()
        current = workflow.list_configs()[0]["revision"]

        with self.assertRaisesRegex(ValueError, "新版本"):
            workflow.update(
                "prompt-1",
                {"模板内容": "用户的新模板"},
                expected_revision=current,
            )

        self.assertEqual(updates, [])

    def test_once_enabled_prompt_stays_immutable_after_disabling(self):
        workflow, fields, updates, _ = self.make_workflow()
        current = workflow.list_configs()[0]["revision"]
        workflow.update(
            "prompt-1",
            {"是否启用": "停用"},
            expected_revision=current,
        )
        self.assertEqual(fields["版本已锁定"], "是")
        current = workflow.list_configs()[0]["revision"]

        with self.assertRaisesRegex(ValueError, "新版本"):
            workflow.update(
                "prompt-1",
                {"模板内容": "改写已使用版本"},
                expected_revision=current,
            )

        self.assertEqual(len(updates), 1)

    def test_new_prompt_is_saved_as_draft_by_default(self):
        workflow, _, _, records = self.make_workflow()

        created = workflow.create({
            "Prompt名称": "豆包视频-墨西哥-方案二",
            "版本": "mx-v2",
            "目标地区": "墨西哥",
            "模板内容": "方案二内容 {product_name}",
        })

        self.assertEqual(created["record_id"], "prompt-2")
        self.assertEqual(records[1]["fields"]["Prompt类型"], "豆包视频")
        self.assertEqual(records[1]["fields"]["模板Key"], "doubao_seedance_video_prompt")
        self.assertEqual(records[1]["fields"]["是否启用"], "停用")
        self.assertEqual(records[1]["fields"]["方案Key"], "current_conversion")
        self.assertEqual(records[1]["fields"]["执行模式"], "meta_generate")

    def test_enabling_new_prompt_disables_active_sibling(self):
        workflow, fields, updates, records = self.make_workflow()

        workflow.create({
            "Prompt名称": "豆包视频-墨西哥-方案二",
            "版本": "mx-v2",
            "是否启用": "启用",
            "目标地区": "墨西哥",
            "模板内容": "方案二内容",
        })

        self.assertEqual(fields["是否启用"], "停用")
        self.assertEqual(records[1]["fields"]["是否启用"], "启用")
        self.assertEqual(records[1]["fields"]["版本已锁定"], "是")
        self.assertIn({"是否启用": "停用", "版本已锁定": "是"}, updates)

    def test_duplicate_version_in_same_country_is_rejected(self):
        workflow, _, _, records = self.make_workflow()

        with self.assertRaisesRegex(ValueError, "版本.*已存在"):
            workflow.create({
                "Prompt名称": "重复版本",
                "版本": "mx-v1",
                "目标地区": "墨西哥",
                "模板内容": "内容",
            })

        self.assertEqual(len(records), 1)

    def test_enabling_draft_by_edit_disables_active_sibling(self):
        workflow, fields, _, records = self.make_workflow()
        created = workflow.create({
            "Prompt名称": "豆包视频-墨西哥-方案二",
            "版本": "mx-v2",
            "目标地区": "墨西哥",
            "模板内容": "方案二内容",
        })

        workflow.update(
            created["record_id"],
            {"是否启用": "启用"},
            expected_revision=created["revision"],
        )

        self.assertEqual(fields["是否启用"], "停用")
        self.assertEqual(records[1]["fields"]["是否启用"], "启用")
        self.assertEqual(records[1]["fields"]["版本已锁定"], "是")

    def test_same_version_is_allowed_for_different_language_or_category(self):
        workflow, _, _, records = self.make_workflow()

        workflow.create({
            "Prompt名称": "马来西亚英语鞋类",
            "方案Key": "current_conversion",
            "方案名称": "T1",
            "执行模式": "meta_generate",
            "版本": "mx-v1",
            "目标地区": "墨西哥",
            "目标语言": "英语",
            "适用品类": "鞋类",
            "模板内容": "English shoes",
        })

        self.assertEqual(len(records), 2)

    def test_legacy_row_maps_to_t1_meta_generate(self):
        workflow, _, _, _ = self.make_workflow()

        row = workflow.list_configs()[0]

        self.assertEqual(row["方案Key"], "current_conversion")
        self.assertEqual(row["方案名称"], "T1 当前转化模板")
        self.assertEqual(row["执行模式"], "meta_generate")


if __name__ == "__main__":
    unittest.main()
