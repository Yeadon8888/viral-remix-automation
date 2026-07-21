import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from api.countries import get_country, list_countries
from api.services.part_time_sync import PartTimeSyncService
from api.workflow import ViralRemixWorkflow


class CountryRegistryTests(unittest.TestCase):
    def test_registry_exposes_supported_market_languages(self):
        countries = {item["code"]: item for item in list_countries()}

        self.assertEqual(set(countries), {"MY", "MY_EN", "PH", "TH", "ID", "MX"})
        self.assertEqual(countries["MY"]["prompt_region"], "马来")
        self.assertEqual(countries["MY"]["language_name"], "马来语")
        self.assertEqual(countries["MY_EN"]["prompt_region"], "马来-英语")
        self.assertEqual(countries["MY_EN"]["language_name"], "英语")
        self.assertEqual(countries["PH"]["prompt_region"], "菲律宾")
        self.assertEqual(countries["TH"]["prompt_region"], "泰国")
        self.assertEqual(countries["ID"]["prompt_region"], "印度尼西亚")
        self.assertEqual(countries["ID"]["language_name"], "印尼语")
        self.assertEqual(countries["MX"]["name"], "墨西哥")
        self.assertEqual(countries["MX"]["prompt_region"], "墨西哥")
        self.assertEqual(countries["MX"]["language_name"], "西班牙语")
        self.assertEqual(countries["MX"]["language"], "Español de México（墨西哥西班牙语）")

    def test_registry_rejects_unknown_country_code(self):
        with self.assertRaisesRegex(ValueError, "不支持的国家"):
            get_country("ZZ")


class CountryAwareDoubaoPromptTests(unittest.TestCase):
    def test_generate_doubao_prompt_uses_requested_country_and_tracks_it(self):
        captured = {}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {
                    "record_id": record_id,
                    "fields": {
                        "商品名称": "Thai product",
                        "商品链接": "https://example.com/product",
                    },
                }

            def update_record(self, table_id, record_id, fields):
                captured["updated_fields"] = fields
                return {"record_id": record_id, "fields": fields}

        class FakeGemini:
            def generate_doubao_video_prompt(self, **kwargs):
                captured["gemini_args"] = kwargs
                return {
                    "final_video_prompt": "prompt",
                    "title": "title",
                    "caption": "caption",
                    "tags": "#tag",
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.gemini = FakeGemini()
        workflow._resolve_doubao_image = lambda fields, prefer_stored=True: (b"image", "image/png", "image.png")

        def active_prompt(key, region, fallback):
            captured["selection"] = (key, region, fallback)
            return "泰国模板", "doubao_seedance_video_prompt:th-v1"

        workflow._active_prompt_by_key = active_prompt

        result = workflow.generate_doubao_prompt("record-1", country_code="TH")

        self.assertEqual(captured["selection"][1], "泰国")
        self.assertEqual(captured["gemini_args"]["prompt_rules"], "泰国模板")
        self.assertEqual(captured["updated_fields"]["目标国家"], "泰国")
        self.assertEqual(captured["updated_fields"]["目标语言"], "泰语")
        self.assertEqual(captured["updated_fields"]["Prompt配置版本"], "doubao_seedance_video_prompt:th-v1")
        self.assertEqual(captured["updated_fields"]["兼职同步状态"], "未同步")
        self.assertEqual(result["country_code"], "TH")


class PartTimeSyncServiceTests(unittest.TestCase):
    def make_service(self, source_fields, target_records=None):
        calls = {"created": [], "updated": [], "listed": 0}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {"record_id": record_id, "fields": dict(source_fields)}

            def list_records(self, table_id, **kwargs):
                calls["listed"] += 1
                return list(target_records or [])

            def create_record(self, table_id, fields):
                calls["created"].append((table_id, fields))
                return {"record_id": "target-new", "fields": fields}

            def update_record(self, table_id, record_id, fields):
                calls["updated"].append((table_id, record_id, fields))
                return {"record_id": record_id, "fields": fields}

        settings = SimpleNamespace(
            doubao_table_id="source-table",
            part_time_table_id="target-table",
        )
        return PartTimeSyncService(settings, FakeFeishu()), calls

    def test_create_maps_source_fields_and_writes_tracking_fields(self):
        service, calls = self.make_service({
            "商品名称": "Travel bag",
            "商品图存储链接": "https://cdn.example/bag.jpg",
            "最终视频生成提示词": "Thai Seedance prompt",
            "目标国家": "泰国",
            "目标语言": "泰语",
        })

        result = service.sync_record("source-1", "TH")

        self.assertEqual(result["status"], "created")
        target_fields = calls["created"][0][1]
        self.assertEqual(target_fields["来源记录ID"], "source-1")
        self.assertEqual(target_fields["目标国家"], "泰国")
        self.assertEqual(target_fields["目标语言"], "泰语")
        self.assertEqual(target_fields["文本"], "豆包视频｜泰国｜泰语｜Travel bag")
        self.assertEqual(target_fields["提示词"], "Thai Seedance prompt")
        self.assertEqual(target_fields["图片地址"]["link"], "https://cdn.example/bag.jpg")
        self.assertEqual(target_fields["任务状态"], "待接单")
        source_update = calls["updated"][-1]
        self.assertEqual(source_update[0], "source-table")
        self.assertEqual(source_update[2]["兼职同步记录ID"], "target-new")
        self.assertEqual(source_update[2]["兼职同步状态"], "已同步")

    def test_existing_waiting_record_is_updated_instead_of_duplicated(self):
        service, calls = self.make_service(
            {
                "商品名称": "Travel bag",
                "商品图链接": "https://cdn.example/bag.jpg",
                "最终视频生成提示词": "updated prompt",
                "目标国家": "泰国",
                "目标语言": "泰语",
            },
            [{
                "record_id": "target-existing",
                "fields": {
                    "来源记录ID": "source-1",
                    "目标国家": "泰国",
                    "目标语言": "泰语",
                    "任务状态": "待接单",
                },
            }],
        )

        result = service.sync_record("source-1", "TH")

        self.assertEqual(result["status"], "updated")
        self.assertEqual(calls["created"], [])
        target_updates = [call for call in calls["updated"] if call[0] == "target-table"]
        self.assertEqual(len(target_updates), 1)
        self.assertEqual(target_updates[0][1], "target-existing")

    def test_synced_source_is_skipped_without_target_write(self):
        service, calls = self.make_service({
            "商品名称": "Travel bag",
            "商品图存储链接": "https://cdn.example/bag.jpg",
            "最终视频生成提示词": "prompt",
            "目标国家": "泰国",
            "目标语言": "泰语",
            "兼职同步状态": "已同步",
        })

        result = service.sync_record("source-1", "TH")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(calls["created"], [])
        self.assertEqual(calls["updated"], [])
        self.assertEqual(calls["listed"], 0)

    def test_force_update_processes_synced_source(self):
        service, calls = self.make_service({
            "商品名称": "Travel bag",
            "商品图存储链接": "https://cdn.example/bag.jpg",
            "最终视频生成提示词": "prompt",
            "目标国家": "泰国",
            "目标语言": "泰语",
            "兼职同步状态": "已同步",
        })

        result = service.sync_record("source-1", "TH", force_update=True)

        self.assertEqual(result["status"], "created")
        self.assertEqual(len(calls["created"]), 1)

    def test_claimed_record_is_a_conflict_and_is_never_overwritten(self):
        service, calls = self.make_service(
            {
                "商品名称": "Travel bag",
                "商品图链接": "https://cdn.example/bag.jpg",
                "最终视频生成提示词": "new prompt",
                "目标国家": "泰国",
                "目标语言": "泰语",
            },
            [{
                "record_id": "target-claimed",
                "fields": {
                    "来源记录ID": "source-1",
                    "目标国家": "泰国",
                    "目标语言": "泰语",
                    "任务状态": "接单中",
                },
            }],
        )

        result = service.sync_record("source-1", "TH")

        self.assertEqual(result["status"], "conflict")
        self.assertEqual(calls["created"], [])
        self.assertFalse(any(call[0] == "target-table" for call in calls["updated"]))
        self.assertEqual(calls["updated"][-1][2]["兼职同步状态"], "同步冲突")

    def test_missing_prompt_is_rejected_before_target_write(self):
        service, calls = self.make_service({
            "商品名称": "Travel bag",
            "商品图链接": "https://cdn.example/bag.jpg",
            "目标国家": "泰国",
            "目标语言": "泰语",
        })

        with self.assertRaisesRegex(RuntimeError, "缺少最终视频生成提示词"):
            service.sync_record("source-1", "TH")

        self.assertEqual(calls["created"], [])

    def test_shared_target_index_avoids_reloading_target_table_for_each_record(self):
        service, calls = self.make_service({
            "商品名称": "Travel bag",
            "商品图链接": "https://cdn.example/bag.jpg",
            "最终视频生成提示词": "prompt",
            "目标国家": "泰国",
            "目标语言": "泰语",
        })
        shared_target_records = []

        service.sync_record("source-1", "TH", target_records=shared_target_records)

        self.assertEqual(calls["listed"], 0)
        self.assertEqual(len(shared_target_records), 1)
        self.assertEqual(shared_target_records[0]["record_id"], "target-new")

    def test_prompt_country_must_match_sync_country(self):
        service, calls = self.make_service({
            "商品名称": "Travel bag",
            "商品图链接": "https://cdn.example/bag.jpg",
            "最终视频生成提示词": "Thai prompt",
            "目标国家": "泰国",
            "目标语言": "泰语",
        })

        with self.assertRaisesRegex(RuntimeError, "提示词属于泰国"):
            service.sync_record("source-1", "PH")

        self.assertEqual(calls["listed"], 0)
        self.assertEqual(calls["created"], [])

    def test_malaysia_language_profiles_do_not_overwrite_each_other(self):
        service, calls = self.make_service(
            {
                "商品名称": "Travel bag",
                "商品图链接": "https://cdn.example/bag.jpg",
                "最终视频生成提示词": "English prompt",
                "目标国家": "马来西亚",
                "目标语言": "英语",
            },
            [
                {
                    "record_id": "target-my-ms",
                    "fields": {
                        "来源记录ID": "source-1",
                        "目标国家": "马来西亚",
                        "目标语言": "马来语",
                        "任务状态": "待接单",
                    },
                },
                {
                    "record_id": "target-my-en",
                    "fields": {
                        "来源记录ID": "source-1",
                        "目标国家": "马来西亚",
                        "目标语言": "英语",
                        "任务状态": "待接单",
                    },
                },
            ],
        )

        result = service.sync_record("source-1", "MY_EN")

        self.assertEqual(result["target_record_id"], "target-my-en")
        target_updates = [call for call in calls["updated"] if call[0] == "target-table"]
        self.assertEqual(target_updates[0][1], "target-my-en")

    def test_prompt_language_must_match_sync_language(self):
        service, calls = self.make_service({
            "商品名称": "Travel bag",
            "商品图链接": "https://cdn.example/bag.jpg",
            "最终视频生成提示词": "Malay prompt",
            "目标国家": "马来西亚",
            "目标语言": "马来语",
        })

        with self.assertRaisesRegex(RuntimeError, "提示词语言为马来语"):
            service.sync_record("source-1", "MY_EN")

        self.assertEqual(calls["listed"], 0)

    def test_legacy_single_language_record_is_upgraded_without_duplication(self):
        service, calls = self.make_service(
            {
                "商品名称": "Travel bag",
                "商品图链接": "https://cdn.example/bag.jpg",
                "最终视频生成提示词": "Thai prompt created before language tracking",
                "目标国家": "泰国",
            },
            [{
                "record_id": "target-legacy-th",
                "fields": {
                    "来源记录ID": "source-1",
                    "目标国家": "泰国",
                    "任务状态": "待接单",
                },
            }],
        )

        result = service.sync_record("source-1", "TH")

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["target_record_id"], "target-legacy-th")
        self.assertEqual(calls["created"], [])


class CountryAwareApiTests(unittest.TestCase):
    def setUp(self):
        app_module.clear_list_cache()
        self.client = app_module.app.test_client()

    def test_countries_endpoint_returns_registry(self):
        response = self.client.get("/api/countries")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["code"] for item in response.get_json()["data"]],
            ["MY", "MY_EN", "PH", "TH", "ID", "MX"],
        )

    def test_single_prompt_endpoint_passes_country_code_to_job(self):
        captured = {}

        def fake_job(name, action, payload):
            captured.update({"name": name, "action": action, "payload": payload})
            return "job-1"

        settings = SimpleNamespace(admin_token="")
        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "enqueue_workflow_action", fake_job),
        ):
            response = self.client.post(
                "/api/doubao-items/source-1/generate-prompt",
                json={"country_code": "PH"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["action"], "doubao.generate_prompt")
        self.assertEqual(captured["payload"], {"record_id": "source-1", "country_code": "PH"})

    def test_batch_prompt_endpoint_rejects_unknown_country(self):
        response = self.client.post(
            "/api/doubao-items/batch-generate-prompts",
            json={"record_ids": ["source-1"], "country_code": "ZZ"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("不支持的国家", response.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
