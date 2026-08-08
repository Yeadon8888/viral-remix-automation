import unittest
from types import SimpleNamespace

from api.gemini import GeminiClient
from api.workflow import DOUBAO_FALLBACK_PROMPT, ViralRemixWorkflow


class DoubaoPromptTemplateTests(unittest.TestCase):
    def test_direct_snapshot_skips_video_gemini_and_writes_independent_metadata(self):
        captured = {}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {
                    "record_id": record_id,
                    "fields": {
                        "商品名称": "Zapatos",
                        "商品链接": "https://example.com/p",
                        "商品品类": "鞋类",
                        "文案": "Texto existente",
                        "标签": "#zapatos",
                    },
                }

            def update_record(self, table_id, record_id, fields):
                captured["fields"] = fields
                return {"record_id": record_id, "fields": fields}

        class FakeGemini:
            def generate_doubao_video_prompt(self, **kwargs):
                raise AssertionError("direct_prompt must not call Gemini")

            def generate_doubao_publish_metadata(self, **kwargs):
                return {
                    "title": "Zapatos imprescindibles",
                    "caption": "Un estilo nuevo para cada paso.",
                    "tags": "#Zapatos #ModaMexico",
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.gemini = FakeGemini()
        workflow.doubao_assets = SimpleNamespace(
            require_prompt_generation_ready=lambda fields: "https://assets.example.com/image.png",
            download_managed_image=lambda fields: (b"image", "image/png", "https://assets.example.com/image.png"),
        )
        snapshot = {
            "config_record_id": "cfg-shoes",
            "scheme_key": "mx_shoes_direct_10s",
            "scheme_name": "墨西哥鞋类 10 秒",
            "execution_mode": "direct_prompt",
            "version": "v1",
            "category": "鞋类",
            "content": "商品：{product_name}；链接：{product_link}",
        }

        workflow.generate_doubao_prompt("record-1", "MX", prompt_snapshot=snapshot)

        self.assertEqual(captured["fields"]["最终视频生成提示词"], "商品：Zapatos；链接：https://example.com/p")
        self.assertEqual(captured["fields"]["Prompt方案Key"], "mx_shoes_direct_10s")
        self.assertEqual(captured["fields"]["Prompt配置记录ID"], "cfg-shoes")
        self.assertEqual(captured["fields"]["Prompt执行模式"], "direct_prompt")
        self.assertEqual(captured["fields"]["标题"], "Zapatos imprescindibles")
        self.assertEqual(captured["fields"]["文案"], "Un estilo nuevo para cada paso.")
        self.assertEqual(captured["fields"]["标签"], "#Zapatos #ModaMexico")

    def test_direct_snapshot_fills_missing_publish_metadata_without_rewriting_prompt(self):
        captured = {}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {
                    "record_id": record_id,
                    "fields": {
                        "商品名称": "Vestido de mezclilla",
                        "商品链接": "https://example.com/dress",
                        "商品品类": "服装",
                    },
                }

            def update_record(self, table_id, record_id, fields):
                captured["fields"] = fields
                return {"record_id": record_id, "fields": fields}

        class FakeGemini:
            def generate_doubao_video_prompt(self, **kwargs):
                raise AssertionError("direct_prompt must not call video prompt generation")

            def generate_doubao_publish_metadata(self, **kwargs):
                captured["metadata_args"] = kwargs
                return {
                    "title": "Vestido de mezclilla",
                    "caption": "Un vestido versátil para cualquier ocasión.",
                    "tags": "#ModaMexico #Vestido",
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.gemini = FakeGemini()
        workflow.doubao_assets = SimpleNamespace(
            require_prompt_generation_ready=lambda fields: "https://assets.example.com/image.png",
            download_managed_image=lambda fields: (
                b"image",
                "image/png",
                "https://assets.example.com/image.png",
            ),
        )
        snapshot = {
            "config_record_id": "cfg-clothing",
            "scheme_key": "mx_clothing_10s",
            "scheme_name": "墨西哥服装 10 秒",
            "execution_mode": "direct_prompt",
            "version": "v1",
            "category": "服装",
            "content": "服装视频：{product_name}",
        }

        workflow.generate_doubao_prompt("record-1", "MX", prompt_snapshot=snapshot)

        self.assertEqual(captured["fields"]["最终视频生成提示词"], "服装视频：Vestido de mezclilla")
        self.assertEqual(captured["fields"]["文案"], "Un vestido versátil para cualquier ocasión.")
        self.assertEqual(captured["fields"]["标签"], "#ModaMexico #Vestido")

    def test_metadata_repair_updates_only_missing_publish_fields_on_synced_record(self):
        captured = {}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {
                    "record_id": record_id,
                    "fields": {
                        "商品名称": "Vestido",
                        "商品链接": "https://example.com/dress",
                        "最终视频生成提示词": "existing direct prompt",
                        "兼职同步状态": "已同步",
                    },
                }

            def update_record(self, table_id, record_id, fields):
                captured["fields"] = fields
                return {"record_id": record_id, "fields": fields}

        class FakeGemini:
            def generate_doubao_publish_metadata(self, **kwargs):
                return {
                    "title": "Vestido",
                    "caption": "Texto para publicar.",
                    "tags": "#ModaMexico #Vestido",
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.gemini = FakeGemini()
        workflow.doubao_assets = SimpleNamespace(
            require_safe_image=lambda fields: None,
            download_managed_image=lambda fields: (
                b"image",
                "image/png",
                "https://assets.example.com/image.png",
            ),
        )

        result = workflow.fill_doubao_publish_metadata(
            "record-1",
            "MX",
        )

        self.assertEqual(result["status"], "updated")
        self.assertEqual(
            captured["fields"],
            {"标题": "Vestido", "文案": "Texto para publicar.", "标签": "#ModaMexico #Vestido"},
        )

    def test_generate_doubao_prompt_selects_malaysia_template(self):
        captured = {}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {
                    "record_id": record_id,
                    "fields": {
                        "商品名称": "Produk Malaysia",
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

            def generate_doubao_publish_metadata(self, **kwargs):
                return {
                    "title": "metadata title",
                    "caption": "metadata caption",
                    "tags": "#metadata",
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.gemini = FakeGemini()
        workflow.doubao_assets = SimpleNamespace(
            require_prompt_generation_ready=lambda fields: "https://assets.example.com/image.png",
            download_managed_image=lambda fields: (
                b"image",
                "image/png",
                "https://assets.example.com/image.png",
            ),
        )

        def active_prompt(key, region, fallback):
            captured["selection"] = (key, region, fallback)
            return "现有马来西亚模板", "doubao_seedance_video_prompt:v1"

        workflow._active_prompt_by_key = active_prompt

        workflow.generate_doubao_prompt("record-1")

        self.assertEqual(captured["selection"][1], "马来")
        self.assertEqual(captured["gemini_args"]["prompt_rules"], "现有马来西亚模板")

    def test_meta_video_template_cannot_control_publish_metadata(self):
        captured = {}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {
                    "record_id": record_id,
                    "fields": {
                        "商品名称": "Vestido",
                        "商品链接": "https://example.com/dress",
                    },
                }

            def update_record(self, table_id, record_id, fields):
                captured["fields"] = fields
                return {"record_id": record_id, "fields": fields}

        class FakeGemini:
            def generate_doubao_video_prompt(self, **kwargs):
                return {
                    "final_video_prompt": "video prompt from selected template",
                    "title": "wrong title from video template",
                    "caption": "wrong caption from video template",
                    "tags": "#wrong",
                }

            def generate_doubao_publish_metadata(self, **kwargs):
                captured["metadata_args"] = kwargs
                return {
                    "title": "Vestido imprescindible",
                    "caption": "El look que transforma cualquier día.",
                    "tags": "#ModaMexico #Vestido",
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.gemini = FakeGemini()
        workflow.doubao_assets = SimpleNamespace(
            require_prompt_generation_ready=lambda fields: None,
            download_managed_image=lambda fields: (
                b"image",
                "image/png",
                "https://assets.example.com/image.png",
            ),
        )
        workflow._active_prompt_by_key = lambda key, region, fallback: ("video template", "video:v1")

        workflow.generate_doubao_prompt("record-1", "MX")

        self.assertEqual(captured["fields"]["最终视频生成提示词"], "video prompt from selected template")
        self.assertEqual(captured["fields"]["标题"], "Vestido imprescindible")
        self.assertEqual(captured["fields"]["文案"], "El look que transforma cualquier día.")
        self.assertEqual(captured["fields"]["标签"], "#ModaMexico #Vestido")
        self.assertNotIn("prompt_rules", captured["metadata_args"])

    def test_doubao_fallback_is_malaysia_instead_of_mexico(self):
        self.assertIn("马来西亚", DOUBAO_FALLBACK_PROMPT)
        self.assertIn("马来语", DOUBAO_FALLBACK_PROMPT)
        self.assertNotIn("墨西哥", DOUBAO_FALLBACK_PROMPT)

    def test_gemini_wrapper_does_not_override_configured_market_or_language(self):
        captured = {}
        client = object.__new__(GeminiClient)
        client.settings = SimpleNamespace(gemini_model="test-model")

        def fake_post_chat(payload):
            captured["payload"] = payload
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"final_video_prompt":"prompt"}'
                        }
                    }
                ]
            }

        client._post_chat = fake_post_chat

        client.generate_doubao_video_prompt(
            product_name="Produk Malaysia",
            product_link="https://example.com/product",
            prompt_rules="目标市场是马来西亚，所有口播与字幕使用马来语。",
            image_bytes=b"image",
            image_mime="image/png",
        )

        prompt = captured["payload"]["messages"][0]["content"][0]["text"]
        self.assertIn("目标市场是马来西亚，所有口播与字幕使用马来语。", prompt)
        self.assertNotIn("目标市场：墨西哥", prompt)
        self.assertNotIn("墨西哥西班牙语", prompt)
        self.assertNotIn("必须用于 10 秒 4 镜头 TikTok 墨西哥带货视频", prompt)

    def test_configured_template_controls_all_business_rules(self):
        captured = {}
        client = object.__new__(GeminiClient)
        client.settings = SimpleNamespace(gemini_model="test-model")

        def fake_post_chat(payload):
            captured["payload"] = payload
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"final_video_prompt":"prompt"}'
                        }
                    }
                ]
            }

        client._post_chat = fake_post_chat
        template = "手动模板规则\n商品：{product_name}\n链接：{product_link}\n只使用这里写明的创作要求。"

        client.generate_doubao_video_prompt(
            product_name="Produk Malaysia",
            product_link="https://example.com/product",
            prompt_rules=template,
            image_bytes=b"image",
            image_mime="image/png",
        )

        prompt = captured["payload"]["messages"][0]["content"][0]["text"]
        self.assertTrue(prompt.startswith("手动模板规则"))
        self.assertIn("商品：Produk Malaysia", prompt)
        self.assertIn("链接：https://example.com/product", prompt)
        self.assertNotIn("请先理解商品图片中的真实商品", prompt)
        self.assertNotIn("必须严格锁定图片中的商品外观", prompt)
        self.assertIn('"final_video_prompt"', prompt)
        self.assertNotIn('"title"', prompt)
        self.assertNotIn('"caption"', prompt)
        self.assertNotIn('"tags"', prompt)


if __name__ == "__main__":
    unittest.main()
