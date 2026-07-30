from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo


SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELATIONSHIP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MARKUP_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
X14AC_NS = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
XR_NS = "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"
XR2_NS = "http://schemas.microsoft.com/office/spreadsheetml/2015/revision2"
XR3_NS = "http://schemas.microsoft.com/office/spreadsheetml/2016/revision3"

ET.register_namespace("", SPREADSHEET_NS)
ET.register_namespace("r", RELATIONSHIP_NS)
ET.register_namespace("mc", MARKUP_NS)
ET.register_namespace("x14ac", X14AC_NS)
ET.register_namespace("xr", XR_NS)
ET.register_namespace("xr2", XR2_NS)
ET.register_namespace("xr3", XR3_NS)


class BatchQueueExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchQueueItem:
    record_id: str
    source_record_id: str
    prompt: str
    image_url: str


@dataclass(frozen=True)
class BatchQueuePackage:
    filename: str
    folder_name: str
    content: bytes
    item_count: int


def _image_extension(data: bytes, content_type: str) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        return ".avif"
    mime_extensions = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/avif": ".avif",
    }
    extension = mime_extensions.get(str(content_type or "").split(";")[0].strip().lower())
    if extension:
        return extension
    raise BatchQueueExportError("参考图返回的内容不是支持的图片格式")


def _xlsx_bytes(template_path: Path, rows: list[tuple[str, str]]) -> bytes:
    with ZipFile(template_path, "r") as source:
        source_files = {name: source.read(name) for name in source.namelist()}

    sheet = ET.fromstring(source_files["xl/worksheets/sheet1.xml"])
    dimension = sheet.find(f"{{{SPREADSHEET_NS}}}dimension")
    if dimension is not None:
        dimension.set("ref", f"A1:D{len(rows) + 1}")

    sheet_data = sheet.find(f"{{{SPREADSHEET_NS}}}sheetData")
    if sheet_data is None:
        raise BatchQueueExportError("批量队列模板缺少工作表数据")
    sheet_data.clear()

    shared_strings = ["提示词", "参考图1(选填)", "参考图2(选填)", "参考图3(选填)"]
    header_row = ET.SubElement(
        sheet_data,
        f"{{{SPREADSHEET_NS}}}row",
        {"r": "1", "spans": "1:4", f"{{{X14AC_NS}}}dyDescent": "0.3"},
    )
    for column, string_index in zip(("A", "B", "C", "D"), range(4)):
        cell = ET.SubElement(
            header_row,
            f"{{{SPREADSHEET_NS}}}c",
            {"r": f"{column}1", "t": "s"},
        )
        ET.SubElement(cell, f"{{{SPREADSHEET_NS}}}v").text = str(string_index)

    for row_number, (prompt, image_path) in enumerate(rows, start=2):
        prompt_index = len(shared_strings)
        shared_strings.append(prompt)
        image_index = len(shared_strings)
        shared_strings.append(image_path)
        row = ET.SubElement(
            sheet_data,
            f"{{{SPREADSHEET_NS}}}row",
            {
                "r": str(row_number),
                "spans": "1:4",
                "ht": "15",
                f"{{{X14AC_NS}}}dyDescent": "0.3",
            },
        )
        for column, string_index in (("A", prompt_index), ("B", image_index)):
            cell = ET.SubElement(
                row,
                f"{{{SPREADSHEET_NS}}}c",
                {"r": f"{column}{row_number}", "t": "s"},
            )
            ET.SubElement(cell, f"{{{SPREADSHEET_NS}}}v").text = str(string_index)

    shared_root = ET.Element(
        f"{{{SPREADSHEET_NS}}}sst",
        {"count": str(len(shared_strings)), "uniqueCount": str(len(set(shared_strings)))},
    )
    for value in shared_strings:
        item = ET.SubElement(shared_root, f"{{{SPREADSHEET_NS}}}si")
        text = ET.SubElement(item, f"{{{SPREADSHEET_NS}}}t")
        if value[:1].isspace() or value[-1:].isspace():
            text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text.text = value

    source_files["xl/worksheets/sheet1.xml"] = ET.tostring(
        sheet,
        encoding="utf-8",
        xml_declaration=True,
    )
    source_files["xl/sharedStrings.xml"] = ET.tostring(
        shared_root,
        encoding="utf-8",
        xml_declaration=True,
    )

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as target:
        for name, content in source_files.items():
            target.writestr(name, content)
    return output.getvalue()


def build_batch_queue_package(
    items: list[BatchQueueItem],
    download_url: Callable[[str], tuple[bytes, str]],
    template_path: Path,
    max_workers: int = 6,
    now: datetime | None = None,
) -> BatchQueuePackage:
    if not items:
        raise BatchQueueExportError("请先选择要导出的豆包素材")
    if len(items) > 200:
        raise BatchQueueExportError("每次最多导出 200 条")
    if not template_path.exists():
        raise BatchQueueExportError("服务器缺少批量队列模板")

    downloaded: dict[int, tuple[bytes, str]] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 8))) as executor:
        futures = {
            executor.submit(download_url, item.image_url): (index, item)
            for index, item in enumerate(items)
        }
        for future in as_completed(futures):
            index, item = futures[future]
            try:
                data, content_type = future.result()
                extension = _image_extension(data, content_type)
                downloaded[index] = (data, extension)
            except Exception as exc:
                failures.append(f"{item.record_id}: {exc}")
    if failures:
        detail = "；".join(failures[:5])
        suffix = f"；另有 {len(failures) - 5} 条" if len(failures) > 5 else ""
        raise BatchQueueExportError(f"有 {len(failures)} 张参考图下载失败：{detail}{suffix}")

    created_at = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    folder_name = f"批量视频队列_{created_at.strftime('%Y%m%d_%H%M%S')}"
    workbook_rows: list[tuple[str, str]] = []
    image_entries: list[tuple[str, bytes]] = []
    for index, item in enumerate(items):
        data, extension = downloaded[index]
        image_name = f"{index + 1:04d}_{item.source_record_id or item.record_id}{extension}"
        relative_path = f"images/{image_name}"
        windows_path = f"D:\\{folder_name}\\images\\{image_name}"
        workbook_rows.append((item.prompt, windows_path))
        image_entries.append((relative_path, data))

    workbook = _xlsx_bytes(template_path, workbook_rows)
    readme = (
        "使用方法\r\n"
        f"1. 将压缩包直接解压到 D 盘根目录，最终目录应为 D:\\{folder_name}\\\r\n"
        "2. 不要单独移动 Excel 或 images 文件夹，否则表格中的绝对路径会失效。\r\n"
        "3. 打开“批量队列.xlsx”即可用于批量自动化视频生成。\r\n"
        f"\r\n本次共导出 {len(items)} 条任务。\r\n"
    ).encode("utf-8-sig")

    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        output.writestr(f"{folder_name}/批量队列.xlsx", workbook)
        output.writestr(f"{folder_name}/使用说明.txt", readme)
        for relative_path, data in image_entries:
            output.writestr(f"{folder_name}/{relative_path}", data)
    return BatchQueuePackage(
        filename=f"{folder_name}.zip",
        folder_name=folder_name,
        content=archive.getvalue(),
        item_count=len(items),
    )
