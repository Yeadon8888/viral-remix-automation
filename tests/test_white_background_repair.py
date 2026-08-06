import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from api.jobs.actions import execute_action
from api.services.doubao_assets import DoubaoAssetService
from api.services.white_background import WhiteBackgroundService


MANAGED = "https://assets.example.com/"


class FakeFeishu:
    def __init__(self, fields):
        self.fields = dict(fields)
        self.updates = []

    def get_record(self, table_id, record_id):
        return {"record_id": record_id, "fields": dict(self.fields)}

    def update_record(self, table_id, record_id, patch):
        self.updates.append(dict(patch))
        self.fields.update(patch)
        return {"record_id": record_id, "fields": dict(self.fields)}


class FakeStorage:
    def __init__(self, result_url=f"{MANAGED}white.png", error=None):
        self.result_url = result_url
        self.error = error
        self.uploads = []

    def is_managed_url(self, url):
        return str(url or "").startswith(MANAGED)

    def upload_bytes(self, data, filename, content_type):
        self.uploads.append((data, filename, content_type))
        if self.error:
            raise self.error
        return {"url": self.result_url, "size": len(data)}


class FakeGenerator:
    def __init__(self, result_url="https://tokensfactory.example/result.png"):
        self.result_url = result_url
        self.calls = []

    def generate_white_background(self, image_url, prompt):
        self.calls.append((image_url, prompt))
        return {"task_id": "external-1", "image_url": self.result_url}


class FakeGemini:
    def __init__(self, status="clear"):
        self.status = status
        self.calls = []

    def detect_human_faces(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": self.status,
            "face_count": 0 if self.status == "clear" else 1,
            "confidence": 0.99,
            "reason": "复检结果",
        }


def make_services(fields, *, storage=None, generator=None, gemini=None):
    feishu = FakeFeishu(fields)
    storage = storage or FakeStorage()
    generator = generator or FakeGenerator()
    gemini = gemini or FakeGemini()
    settings = SimpleNamespace(
        doubao_table_id="doubao",
        gemini_model="gemini-test",
        face_image_max_bytes=1024 * 1024,
    )
    download = lambda url: (b"\x89PNG\r\n\x1a\nimage", "image/png")
    assets = DoubaoAssetService(settings, feishu, storage, gemini, download)
    service = WhiteBackgroundService(
        settings,
        feishu,
        storage,
        assets,
        generator,
        download,
    )
    return service, feishu, storage, generator, gemini


def flagged_fields(url=f"{MANAGED}face.png"):
    return {
        "商品名称": "Test shoes",
        "商品图存储链接": url,
        "人脸检测状态": "含人脸",
        "人脸检测图片链接": {"text": url, "link": url},
    }


class WhiteBackgroundRepairTests(unittest.TestCase):
    def test_job_action_invokes_single_white_background_repair(self):
        calls = []

        class FakeWorkflow:
            def generate_doubao_white_background(self, record_id, job_id=None):
                calls.append((record_id, job_id))
                return {"record_id": record_id, "status": "完成"}

        result = execute_action(
            "doubao.generate_white_background",
            {"record_id": "record-1"},
            "job-1",
            "operator",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(result["status"], "完成")
        self.assertEqual(calls, [("record-1", "job-1")])

    def test_api_enqueues_single_paid_action(self):
        captured = {}
        client = app_module.app.test_client()

        def enqueue(name, action, payload):
            captured.update({"name": name, "action": action, "payload": payload})
            return "job-white-1"

        with (
            patch.object(app_module, "settings", SimpleNamespace(admin_token="", admin_users_json="")),
            patch.object(app_module, "enqueue_workflow_action", enqueue),
        ):
            response = client.post("/api/doubao-items/record-1/generate-white-background", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["job_id"], "job-white-1")
        self.assertEqual(captured["action"], "doubao.generate_white_background")
        self.assertEqual(captured["payload"], {"record_id": "record-1"})

    def test_only_current_unlocked_flagged_image_is_eligible(self):
        service, _, _, _, _ = make_services(flagged_fields())
        self.assertTrue(service.describe_eligibility(service.feishu.fields)["可生成白底图"])

        service.feishu.fields["最终视频生成提示词"] = "locked prompt"
        state = service.describe_eligibility(service.feishu.fields)
        self.assertFalse(state["可生成白底图"])
        self.assertIn("已生成提示词", state["白底图阻断原因"])

    def test_upload_failure_preserves_current_storage_link(self):
        original = f"{MANAGED}face.png"
        storage = FakeStorage(error=RuntimeError("R2 upload failed"))
        service, feishu, _, _, _ = make_services(flagged_fields(original), storage=storage)

        with self.assertRaisesRegex(RuntimeError, "R2 upload failed"):
            service.generate("record-1")

        self.assertEqual(feishu.fields["商品图存储链接"], original)
        self.assertEqual(feishu.fields["白底图处理状态"], "失败")

    def test_concurrent_image_change_is_not_overwritten(self):
        original = f"{MANAGED}face.png"
        service, feishu, storage, _, gemini = make_services(flagged_fields(original))
        replacement = f"{MANAGED}someone-else.png"

        original_upload = storage.upload_bytes

        def upload_and_change(*args):
            result = original_upload(*args)
            feishu.fields["商品图存储链接"] = replacement
            return result

        storage.upload_bytes = upload_and_change

        with self.assertRaisesRegex(RuntimeError, "已变更"):
            service.generate("record-1")

        self.assertEqual(feishu.fields["商品图存储链接"], replacement)
        self.assertEqual(gemini.calls, [])

    def test_success_replaces_link_and_automatically_rechecks_face(self):
        service, feishu, storage, generator, gemini = make_services(flagged_fields())

        result = service.generate("record-1")

        self.assertEqual(result["status"], "完成")
        self.assertEqual(feishu.fields["商品图存储链接"]["link"], storage.result_url)
        self.assertEqual(feishu.fields["人脸检测图片链接"]["link"], storage.result_url)
        self.assertEqual(feishu.fields["人脸检测状态"], "无人脸")
        self.assertEqual(feishu.fields["白底图处理状态"], "完成")
        self.assertEqual(len(generator.calls), 1)
        self.assertEqual(len(gemini.calls), 1)
        self.assertIn("纯白背景", generator.calls[0][1])


if __name__ == "__main__":
    unittest.main()
