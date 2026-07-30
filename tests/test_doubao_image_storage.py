import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.r2 import R2Storage
from api.services.doubao_assets import DoubaoAssetService
from api.workflow import ViralRemixWorkflow


MANAGED_ORIGIN = "https://vc-upload.example.com/"


class FakeFeishu:
    def __init__(self, fields=None, records=None):
        self.fields = fields or {}
        self.records = records or []
        self.updates = []

    def get_record(self, table_id, record_id):
        return {"record_id": record_id, "fields": self.fields}

    def list_records(self, table_id, max_records):
        return self.records

    def update_record(self, table_id, record_id, fields):
        self.updates.append(fields)
        return {"record_id": record_id, "fields": fields}

    def download_media(self, file_token, target):
        target.write_bytes(b"attachment-bytes")
        return target, "image/png"


class FakeStorage:
    def __init__(self, result_url=f"{MANAGED_ORIGIN}doubao/stored.png", error=None):
        self.result_url = result_url
        self.error = error

    def is_managed_url(self, url):
        return str(url or "").startswith(MANAGED_ORIGIN)

    def upload_bytes(self, data, filename, content_type):
        if self.error:
            raise self.error
        return {"url": self.result_url, "key": "doubao/stored.png", "size": len(data)}


def make_workflow(feishu, storage=None):
    workflow = object.__new__(ViralRemixWorkflow)
    workflow.settings = SimpleNamespace(doubao_table_id="doubao-table")
    workflow.feishu = feishu
    workflow.r2 = storage or FakeStorage()
    workflow._download_url = lambda url: (b"image-bytes", "image/png")
    workflow.doubao_assets = DoubaoAssetService(
        workflow.settings,
        workflow.feishu,
        workflow.r2,
        SimpleNamespace(),
        workflow._download_url,
    )
    return workflow


class DoubaoImageStorageTests(unittest.TestCase):
    def test_download_retries_without_referer_when_cdn_rejects_hotlink_header(self):
        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(newtoken_api_key="", newtoken_base_url="https://api.example.com")

        class Response:
            def __init__(self, status_code, content=b"", content_type=""):
                self.status_code = status_code
                self.content = content
                self.headers = {"content-type": content_type}

        responses = [Response(403), Response(200, b"image-bytes", "image/png")]
        with patch("api.workflow.requests.get", side_effect=responses) as get:
            data, mime = workflow._download_url("https://img.kalocdn.com/product/cover.png")

        self.assertEqual((data, mime), (b"image-bytes", "image/png"))
        self.assertEqual(get.call_count, 2)
        self.assertIn("Referer", get.call_args_list[0].kwargs["headers"])
        self.assertNotIn("Referer", get.call_args_list[1].kwargs["headers"])

    def test_list_marks_external_storage_urls_for_transfer(self):
        feishu = FakeFeishu(records=[
            {"record_id": "external", "fields": {"商品图存储链接": "https://img.kalocdn.com/product/cover.png"}},
            {"record_id": "managed", "fields": {"商品图存储链接": f"{MANAGED_ORIGIN}product/cover.png"}},
        ])

        rows = make_workflow(feishu).list_doubao_items(limit=20)

        self.assertEqual(rows[0]["商品图已入库"], False)
        self.assertEqual(rows[0]["商品图可转存"], True)
        self.assertEqual(rows[0]["图片来源"], "链接")
        self.assertEqual(rows[1]["商品图已入库"], True)
        self.assertEqual(rows[1]["商品图可转存"], False)

    def test_external_storage_field_url_is_transferred_and_replaced(self):
        managed_url = f"{MANAGED_ORIGIN}doubao/record-1.png"
        feishu = FakeFeishu({
            "商品名称": "Test product",
            "商品图存储链接": "https://img.kalocdn.com/tiktok.product/123/cover.png",
        })

        result = make_workflow(feishu, FakeStorage(managed_url)).store_doubao_image("record-1")

        self.assertEqual(result["stored_image_url"], managed_url)
        self.assertEqual(feishu.updates[0]["商品图存储链接"], {"text": managed_url, "link": managed_url})
        self.assertEqual(feishu.updates[0]["人脸检测状态"], "待检测")

    def test_managed_storage_url_is_skipped_without_reuploading(self):
        managed_url = f"{MANAGED_ORIGIN}doubao/record-1.png"
        feishu = FakeFeishu({"商品名称": "Stored product", "商品图存储链接": managed_url})
        workflow = make_workflow(feishu, FakeStorage(error=AssertionError("已入库地址不应重复上传")))
        workflow._download_url = lambda url: (_ for _ in ()).throw(AssertionError("已入库地址不应重复下载"))

        result = workflow.store_doubao_image("record-1")

        self.assertEqual(result, {"record_id": "record-1", "stored_image_url": managed_url, "skipped": True})
        self.assertEqual(feishu.updates, [])

    def test_new_attachment_replaces_an_old_managed_image(self):
        old_url = f"{MANAGED_ORIGIN}doubao/old.png"
        new_url = f"{MANAGED_ORIGIN}doubao/new.png"
        feishu = FakeFeishu({
            "商品名称": "Updated product",
            "商品图存储链接": old_url,
            "商品图": [{"file_token": "new-file", "name": "new-product.png"}],
        })

        result = make_workflow(feishu, FakeStorage(new_url)).store_doubao_image("record-1")

        self.assertEqual(result["stored_image_url"], new_url)
        self.assertEqual(feishu.updates[0]["商品图存储链接"]["link"], new_url)
        self.assertEqual(feishu.updates[0]["人脸检测状态"], "待检测")

    def test_uploaded_attachment_still_transfers_to_managed_storage(self):
        managed_url = f"{MANAGED_ORIGIN}doubao/attachment.png"
        feishu = FakeFeishu({
            "商品名称": "Attachment product",
            "商品图": [{"file_token": "file-1", "name": "product.png"}],
        })

        result = make_workflow(feishu, FakeStorage(managed_url)).store_doubao_image("record-1")

        self.assertEqual(result["stored_image_url"], managed_url)
        self.assertEqual(feishu.updates[0]["商品图存储链接"]["link"], managed_url)

    def test_upload_failure_preserves_external_storage_field_url(self):
        feishu = FakeFeishu({"商品图存储链接": "https://img.kalocdn.com/product/cover.png"})
        workflow = make_workflow(feishu, FakeStorage(error=RuntimeError("R2 upload failed")))

        with self.assertRaisesRegex(RuntimeError, "R2 upload failed"):
            workflow.store_doubao_image("record-1")

        self.assertEqual(feishu.updates, [])


class ManagedStorageUrlTests(unittest.TestCase):
    def test_upload_gateway_and_r2_origins_are_managed(self):
        storage = object.__new__(R2Storage)
        storage.settings = SimpleNamespace(
            upload_api_url="https://vc-upload.example.com/api",
            r2_public_url="https://assets.example.com/media",
        )

        self.assertTrue(storage.is_managed_url("https://vc-upload.example.com/files/product.png"))
        self.assertTrue(storage.is_managed_url("https://assets.example.com/media/product.png"))
        self.assertFalse(storage.is_managed_url("https://img.kalocdn.com/product/cover.png"))


if __name__ == "__main__":
    unittest.main()
