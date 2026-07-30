import unittest
from types import SimpleNamespace

from api.services.doubao_assets import DoubaoAssetService
from api.workflow import ViralRemixWorkflow


MANAGED = "https://assets.example.com/"


class FakeFeishu:
    def __init__(self, fields):
        self.fields = dict(fields)
        self.updates = []
        self.deleted = []

    def get_record(self, table_id, record_id):
        return {"record_id": record_id, "fields": dict(self.fields)}

    def update_record(self, table_id, record_id, fields):
        self.updates.append(dict(fields))
        self.fields.update(fields)
        return {"record_id": record_id, "fields": fields}

    def delete_record(self, table_id, record_id):
        self.deleted.append((table_id, record_id))
        return {"record_id": record_id, "deleted": True}

    def download_media(self, file_token, target):
        target.write_bytes(b"\x89PNG\r\n\x1a\nattachment")
        return target, "image/png"


class FakeStorage:
    def __init__(self, uploaded_url=f"{MANAGED}uploaded.png"):
        self.uploaded_url = uploaded_url
        self.uploads = []

    def is_managed_url(self, url):
        return str(url or "").startswith(MANAGED)

    def upload_bytes(self, data, filename, content_type):
        self.uploads.append((data, filename, content_type))
        return {"url": self.uploaded_url}


class FakeGemini:
    def __init__(self, result=None):
        self.result = result or {"status": "clear", "face_count": 0, "confidence": 0.99, "reason": "未发现人脸"}
        self.calls = []

    def detect_human_faces(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)


def make_service(fields, gemini=None):
    feishu = FakeFeishu(fields)
    storage = FakeStorage()
    detector = gemini or FakeGemini()
    service = DoubaoAssetService(
        SimpleNamespace(
            doubao_table_id="doubao",
            gemini_model="gemini-3.1-flash-lite",
            face_image_max_bytes=1024 * 1024,
        ),
        feishu,
        storage,
        detector,
        lambda url: (b"\x89PNG\r\n\x1a\nimage", "image/png"),
    )
    return service, feishu, storage, detector


class DoubaoAssetSafetyTests(unittest.TestCase):
    def test_workflow_face_action_forces_redetection(self):
        calls = []

        class FakeAssets:
            def detect_face(self, record_id, *, force=False, job_id=None):
                calls.append((record_id, force, job_id))
                return {"record_id": record_id, "status": "无人脸"}

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.doubao_assets = FakeAssets()

        result = workflow.detect_doubao_face("record-1", job_id="job-1")

        self.assertEqual(result["status"], "无人脸")
        self.assertEqual(calls, [("record-1", True, "job-1")])

    def test_workflow_runs_full_publish_preflight_before_part_time_sync(self):
        calls = []

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {"record_id": record_id, "fields": {"商品名称": "Missing product ID"}}

        class FakeAssets:
            def require_part_time_ready(self, fields):
                calls.append(("preflight", dict(fields)))
                raise RuntimeError("缺少商品 ID，已阻断兼职同步")

        class FakePartTimeSync:
            def sync_record(self, *args, **kwargs):
                calls.append(("sync", args, kwargs))
                return {"status": "created"}

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao")
        workflow.feishu = FakeFeishu()
        workflow.doubao_assets = FakeAssets()
        workflow.part_time_sync = FakePartTimeSync()

        with self.assertRaisesRegex(RuntimeError, "缺少商品 ID"):
            workflow.sync_doubao_to_part_time("record-1", country_code="MX")

        self.assertEqual(calls, [("preflight", {"商品名称": "Missing product ID"})])

    def test_external_image_is_blocked_before_face_detection(self):
        service, feishu, _, gemini = make_service({
            "商品图存储链接": "https://img.kalocdn.com/product.png",
        })

        with self.assertRaisesRegex(RuntimeError, "先执行步骤 1"):
            service.detect_face("record-1")

        self.assertEqual(gemini.calls, [])
        self.assertEqual(feishu.updates[-1]["人脸检测状态"], "待转存")
        self.assertEqual(service.describe(feishu.fields)["素材下一步"], "store_image")

    def test_managed_image_can_be_scanned_and_result_is_bound_to_url(self):
        url = f"{MANAGED}product.png"
        service, feishu, _, gemini = make_service({"商品图存储链接": url})

        result = service.detect_face("record-1")

        self.assertEqual(result["status"], "无人脸")
        self.assertEqual(len(gemini.calls), 1)
        self.assertEqual(feishu.updates[-1]["人脸检测图片链接"]["link"], url)
        self.assertTrue(service.describe(feishu.fields)["可生成提示词"])

    def test_face_result_blocks_downstream_and_allows_record_deletion(self):
        url = f"{MANAGED}face.png"
        gemini = FakeGemini({
            "status": "face",
            "face_count": 1,
            "confidence": 0.98,
            "reason": "发现一张清晰人脸",
        })
        service, feishu, _, _ = make_service({"商品图存储链接": url}, gemini)
        service.detect_face("record-1")

        with self.assertRaisesRegex(RuntimeError, "已阻断"):
            service.require_safe_image(feishu.fields)

        deleted = service.delete_flagged_record("record-1")
        self.assertEqual(deleted["status"], "已删除")
        self.assertTrue(deleted["deleted"])
        self.assertEqual(feishu.deleted, [("doubao", "record-1")])
        self.assertEqual(len(feishu.updates), 2)

    def test_synced_face_record_is_not_deleted_without_downstream_cleanup(self):
        url = f"{MANAGED}face.png"
        service, feishu, _, _ = make_service({
            "商品图存储链接": url,
            "人脸检测状态": "含人脸",
            "人脸检测图片链接": {"text": url, "link": url},
            "兼职同步状态": "已同步",
        })

        with self.assertRaisesRegex(RuntimeError, "已同步到兼职表"):
            service.delete_flagged_record("record-1")

        self.assertEqual(feishu.deleted, [])

    def test_old_scan_is_invalid_after_managed_url_changes(self):
        service, _, _, _ = make_service({
            "商品图存储链接": f"{MANAGED}new.png",
            "人脸检测状态": "无人脸",
            "人脸检测图片链接": {"text": f"{MANAGED}old.png", "link": f"{MANAGED}old.png"},
        })

        state = service.describe(service.feishu.fields)

        self.assertEqual(state["人脸检测状态"], "待检测")
        self.assertFalse(state["人脸检测通过"])

    def test_missing_product_id_blocks_part_time_sync_before_resource_is_spent(self):
        url = f"{MANAGED}product.png"
        service, _, _, _ = make_service({
            "商品名称": "Product without ID",
            "商品图存储链接": url,
            "人脸检测状态": "无人脸",
            "人脸检测图片链接": {"text": url, "link": url},
            "最终视频生成提示词": "ready prompt",
        })

        state = service.describe(service.feishu.fields)

        self.assertFalse(state["发布前置条件通过"])
        self.assertFalse(state["可同步兼职"])
        self.assertIn("缺少商品 ID", state["发布前置条件问题"])
        with self.assertRaisesRegex(RuntimeError, "缺少商品 ID"):
            service.require_part_time_ready(service.feishu.fields)

    def test_publish_ready_source_can_enter_part_time_pipeline(self):
        url = f"{MANAGED}product.png"
        service, _, _, _ = make_service({
            "商品id": "1732254358741092079",
            "商品名称": "Ready product",
            "商品图存储链接": url,
            "人脸检测状态": "无人脸",
            "人脸检测图片链接": {"text": url, "link": url},
            "最终视频生成提示词": "ready prompt",
        })

        state = service.describe(service.feishu.fields)

        self.assertTrue(state["发布前置条件通过"])
        self.assertTrue(state["可同步兼职"])
        self.assertEqual(state["发布前置条件问题"], [])
        self.assertEqual(
            service.require_part_time_ready(service.feishu.fields),
            url,
        )

    def test_store_invalidates_previous_scan(self):
        service, feishu, storage, _ = make_service({
            "商品图存储链接": "https://img.kalocdn.com/product.png",
            "人脸检测状态": "无人脸",
            "最终视频生成提示词": "stale prompt",
            "目标国家": "菲律宾",
            "兼职同步状态": "同步失败",
        })

        result = service.store_image("record-1")

        self.assertEqual(result["stored_image_url"], storage.uploaded_url)
        self.assertEqual(feishu.updates[-1]["人脸检测状态"], "待检测")
        self.assertEqual(feishu.updates[-1]["最终视频生成提示词"], "")
        self.assertEqual(feishu.updates[-1]["目标国家"], "")
        self.assertEqual(feishu.updates[-1]["兼职同步状态"], "未同步")

    def test_prompt_locks_face_detection_from_regressing_the_workflow(self):
        url = f"{MANAGED}product.png"
        service, feishu, _, gemini = make_service({
            "商品图存储链接": url,
            "人脸检测状态": "无人脸",
            "人脸检测图片链接": {"text": url, "link": url},
            "最终视频生成提示词": "ready prompt",
        })

        with self.assertRaisesRegex(RuntimeError, "已生成提示词"):
            service.detect_face("record-1", force=True)

        self.assertEqual(gemini.calls, [])
        self.assertEqual(feishu.updates, [])

    def test_synced_record_locks_upstream_steps_and_exposes_the_gate(self):
        url = f"{MANAGED}product.png"
        service, _, _, _ = make_service({
            "商品id": "1732254358741092079",
            "商品图存储链接": url,
            "人脸检测状态": "无人脸",
            "人脸检测图片链接": {"text": url, "link": url},
            "最终视频生成提示词": "ready prompt",
            "兼职同步记录ID": "part-time-1",
            "兼职同步状态": "已同步",
        })

        state = service.describe(service.feishu.fields)

        self.assertEqual(state["流程阶段"], "已同步锁定")
        self.assertTrue(state["流程已锁定"])
        self.assertFalse(state["可执行人脸检测"])
        self.assertFalse(state["可执行生成提示词"])
        self.assertFalse(state["可执行同步兼职"])
        self.assertIn("已同步", state["流程锁定原因"])
        with self.assertRaisesRegex(RuntimeError, "已同步"):
            service.require_prompt_generation_ready(service.feishu.fields)


if __name__ == "__main__":
    unittest.main()
