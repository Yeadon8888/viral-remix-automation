import json
import unittest
from types import SimpleNamespace

from api.gemini import GeminiClient, _normalize_face_result


class GeminiFaceDetectionTests(unittest.TestCase):
    def test_cartoon_character_is_not_normalized_as_a_human_face(self):
        result = _normalize_face_result(json.dumps({
            "status": "face",
            "subject_type": "cartoon",
            "human_face_count": 0,
            "face_count": 2,
            "confidence": 0.95,
            "reason": "耳机壳上有两个卡通角色图案",
        }))

        self.assertEqual(result["status"], "clear")
        self.assertEqual(result["face_count"], 0)

    def test_face_prompt_only_blocks_clear_human_faces(self):
        captured = {}
        client = object.__new__(GeminiClient)
        client.settings = SimpleNamespace(gemini_model="test-model")

        def fake_post_chat(payload):
            captured["payload"] = payload
            return {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "status": "clear",
                            "subject_type": "cartoon",
                            "human_face_count": 0,
                            "confidence": 0.99,
                            "reason": "只有卡通商品图案",
                        }, ensure_ascii=False)
                    }
                }]
            }

        client._post_chat = fake_post_chat
        result = client.detect_human_faces(image_bytes=b"image", image_mime="image/png")

        prompt = captured["payload"]["messages"][0]["content"][0]["text"]
        self.assertEqual(result["status"], "clear")
        self.assertIn("只有明确、可辨认的人类脸部", prompt)
        self.assertIn("卡通、动漫、游戏角色、吉祥物、玩偶、动物脸", prompt)
        self.assertIn("模糊、过小、严重遮挡", prompt)
        self.assertNotIn("清晰的人形插画、卡通或生成式人物脸，也判为 face", prompt)


if __name__ == "__main__":
    unittest.main()
