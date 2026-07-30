import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import app as app_module
from api.services.batch_queue_export import (
    BatchQueueExportError,
    BatchQueueItem,
    BatchQueuePackage,
    build_batch_queue_package,
)
from api.workflow import ViralRemixWorkflow


NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class BatchQueueExportTests(unittest.TestCase):
    def setUp(self):
        self.template_path = (
            Path(__file__).resolve().parents[1]
            / "api"
            / "assets"
            / "批量队列模板.xlsx"
        )

    def test_package_contains_ready_to_use_d_drive_paths_and_images(self):
        images = {
            "https://cdn.example/one.png": (b"\x89PNG\r\n\x1a\none", "image/png"),
            "https://cdn.example/two.jpg": (b"\xff\xd8\xfftwo", "image/jpeg"),
        }
        package = build_batch_queue_package(
            [
                BatchQueueItem("part-1", "source-1", "第一条提示词", "https://cdn.example/one.png"),
                BatchQueueItem("part-2", "source-2", "第二条提示词", "https://cdn.example/two.jpg"),
            ],
            download_url=lambda url: images[url],
            template_path=self.template_path,
            now=datetime(2026, 7, 28, 10, 20, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

        self.assertEqual(package.filename, "批量视频队列_20260728_102030.zip")
        with ZipFile(BytesIO(package.content)) as archive:
            root = "批量视频队列_20260728_102030"
            self.assertIn(f"{root}/批量队列.xlsx", archive.namelist())
            self.assertIn(f"{root}/images/0001_source-1.png", archive.namelist())
            self.assertIn(f"{root}/images/0002_source-2.jpg", archive.namelist())
            workbook = archive.read(f"{root}/批量队列.xlsx")

        with ZipFile(BytesIO(workbook)) as xlsx:
            strings_root = ET.fromstring(xlsx.read("xl/sharedStrings.xml"))
            strings = [
                "".join(node.itertext())
                for node in strings_root.findall("x:si", NS)
            ]
            sheet_root = ET.fromstring(xlsx.read("xl/worksheets/sheet1.xml"))
            cells = {
                cell.attrib["r"]: strings[int(cell.find("x:v", NS).text)]
                for cell in sheet_root.findall(".//x:c", NS)
            }

        self.assertEqual(cells["A2"], "第一条提示词")
        self.assertEqual(
            cells["B2"],
            r"D:\批量视频队列_20260728_102030\images\0001_source-1.png",
        )
        self.assertEqual(cells["A3"], "第二条提示词")
        self.assertEqual(
            cells["B3"],
            r"D:\批量视频队列_20260728_102030\images\0002_source-2.jpg",
        )

    def test_package_fails_closed_when_any_reference_image_cannot_download(self):
        with self.assertRaisesRegex(BatchQueueExportError, "1 张参考图下载失败"):
            build_batch_queue_package(
                [BatchQueueItem("part-1", "source-1", "提示词", "https://cdn.example/missing.png")],
                download_url=lambda _url: (_ for _ in ()).throw(RuntimeError("HTTP 403")),
                template_path=self.template_path,
            )


class BatchQueueExportRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_route_returns_zip_attachment_for_selected_records(self):
        package = BatchQueuePackage(
            filename="批量视频队列_20260728_102030.zip",
            folder_name="批量视频队列_20260728_102030",
            content=b"zip-content",
            item_count=2,
        )

        class ReadyWorkflow:
            def export_doubao_batch_queue(self, record_ids):
                self.record_ids = record_ids
                return package

        workflow = ReadyWorkflow()
        configured = SimpleNamespace(admin_token="", admin_users_json="")
        with (
            patch.object(app_module, "settings", configured),
            patch.object(app_module, "get_workflow", return_value=workflow),
        ):
            response = self.client.post(
                "/api/doubao-items/export-batch-queue",
                json={"record_ids": ["source-1", "source-2"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"zip-content")
        self.assertEqual(response.headers["X-Exported-Items"], "2")
        self.assertEqual(workflow.record_ids, ["source-1", "source-2"])


class DoubaoBatchQueueWorkflowTests(unittest.TestCase):
    def test_export_reads_prompt_and_managed_reference_image_from_doubao_materials(self):
        class FakeFeishu:
            def get_record(self, table_id, record_id):
                self.table_id = table_id
                return {
                    "record_id": record_id,
                    "fields": {
                        "商品ID": "product-1",
                        "商品图存储链接": "https://r2.example/source-1.png",
                        "最终视频生成提示词": "使用这张商品图生成带货视频",
                    },
                }

        workflow = object.__new__(ViralRemixWorkflow)
        workflow.settings = SimpleNamespace(doubao_table_id="doubao")
        workflow.feishu = FakeFeishu()
        workflow.doubao_assets = SimpleNamespace(
            require_part_time_ready=lambda fields: fields["商品图存储链接"],
        )
        workflow._download_url = lambda _url: (b"\x89PNG\r\n\x1a\nimage", "image/png")

        package = workflow.export_doubao_batch_queue(["source-1"])

        self.assertEqual(package.item_count, 1)
        self.assertEqual(workflow.feishu.table_id, "doubao")
        with ZipFile(BytesIO(package.content)) as archive:
            workbook_name = next(name for name in archive.namelist() if name.endswith("/批量队列.xlsx"))
            with ZipFile(BytesIO(archive.read(workbook_name))) as workbook:
                strings_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
                strings = ["".join(node.itertext()) for node in strings_root.findall("x:si", NS)]
        self.assertIn("使用这张商品图生成带货视频", strings)
        self.assertTrue(any(value.endswith(r"\images\0001_source-1.png") for value in strings))


if __name__ == "__main__":
    unittest.main()
