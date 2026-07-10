import unittest
from types import SimpleNamespace

from api.gemini import GeminiClient
from api.workflow import DOUBAO_FALLBACK_PROMPT, ViralRemixWorkflow


class DoubaoPromptTemplateTests(unittest.TestCase):
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

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
        workflow.feishu = FakeFeishu()
        workflow.gemini = FakeGemini()
        workflow._resolve_doubao_image = lambda fields, prefer_stored=True: (b"image", "image/png", "image.png")

        def active_prompt(key, region, fallback):
            captured["selection"] = (key, region, fallback)
            return "现有马来西亚模板", "doubao_seedance_video_prompt:v1"

        workflow._active_prompt_by_key = active_prompt

        workflow.generate_doubao_prompt("record-1")

        self.assertEqual(captured["selection"][1], "马来")
        self.assertEqual(captured["gemini_args"]["prompt_rules"], "现有马来西亚模板")

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
                            "content": '{"final_video_prompt":"prompt","title":"title","caption":"caption","tags":"#tag"}'
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


if __name__ == "__main__":
    unittest.main()
