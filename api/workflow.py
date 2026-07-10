from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import requests

from .config import Settings
from .cpa import CpaVideoClient
from .feishu import FeishuClient, attachment_count, field_value, first_attachment
from .gemini import GeminiClient, local_language
from .image_gen import NewTokenImageClient, normalize_styles
from .logging_bus import add_log
from .prompting import DEFAULT_IMAGE_PROMPTS, DEFAULT_VIDEO_ANALYSIS_TEMPLATE, STYLE_LABELS, image_prompt_snapshot
from .r2 import R2Storage
from .services import ViralVideoAssetService
from .tikhub import TikHubClient

MATERIAL_PROMPT_FIELD = "视频生成Prompt"
DOUBAO_PROMPT_KEY = "doubao_seedance_video_prompt"
DOUBAO_FALLBACK_PROMPT = "你是 TikTok 马来西亚带货视频 AI 导演。根据商品图生成适合马来西亚市场的 Seedance 视频生成提示词；中文描述画面，口播和字幕使用马来语，严格保持商品一致性。"
DOLA_STATUS_FIELD = "Dola生成状态"
DOLA_VIDEO_FIELD = "Dola视频链接"
DOLA_ERROR_FIELD = "Dola错误信息"
DOLA_LOCAL_STATUSES = {"待Dola生成", "准备中", "等待发送", "等待下载", "已完成", "失败"}


def now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def safe_int(value: Any, default: int = 1) -> int:
    try:
        return max(1, min(int(float(value)), 20))
    except Exception:
        return default


def split_urls(value: Any) -> list[str]:
    return [
        line.strip()
        for line in str(value or "").replace(",", "\n").splitlines()
        if line.strip().startswith("http")
    ]


def url_cell(url: str) -> dict[str, str]:
    return {"text": url, "link": url}


def first_available_field(fields: dict[str, Any], names: tuple[str, ...], default: Any = "") -> Any:
    for name in names:
        value = field_value(fields, name, "")
        if str(value or "").strip():
            return value
    return default


def build_dola_local_task(record: dict[str, Any], row_number: int | None = None) -> dict[str, Any]:
    fields = record.get("fields", {})
    stored_image_url = str(field_value(fields, "商品图存储链接", "") or "").strip()
    image_url = str(field_value(fields, "商品图链接", "") or "").strip()
    prompt = str(field_value(fields, "最终视频生成提示词", "") or "").strip()
    video_url = first_available_field(fields, (DOLA_VIDEO_FIELD, "视频生成链接", "视频链接"), "")
    status = str(field_value(fields, DOLA_STATUS_FIELD, "") or "").strip()
    if not status:
        status = "已完成" if str(video_url or "").startswith("http") else "待Dola生成"

    attachment = first_attachment(fields, "商品图")
    reference_image_url = stored_image_url if stored_image_url.startswith("http") else image_url
    return {
        "行号": row_number,
        "record_id": record.get("record_id"),
        "商品名称": field_value(fields, "商品名称", ""),
        "商品链接": field_value(fields, "商品链接", ""),
        "参考图链接": reference_image_url,
        "商品图存储链接": stored_image_url,
        "商品图链接": image_url,
        "商品图数量": len(fields.get("商品图") or []),
        "商品图文件名": (attachment or {}).get("name", ""),
        "最终视频生成提示词": prompt,
        "标题": field_value(fields, "标题", ""),
        "文案": field_value(fields, "文案", ""),
        "标签": field_value(fields, "标签", ""),
        DOLA_STATUS_FIELD: status,
        DOLA_VIDEO_FIELD: video_url,
        DOLA_ERROR_FIELD: field_value(fields, DOLA_ERROR_FIELD, ""),
        "可准备": bool(prompt and reference_image_url.startswith("http") and not str(video_url or "").startswith("http")),
    }


def public_error_message(error: Any, limit: int = 180) -> str:
    text = " ".join(str(error or "").split())
    if not text:
        return "未知错误"
    lowered = text.lower()
    if (
        "chromium_headless_shell" in lowered
        or "playwright install" in lowered
        or "executable doesn't exist" in lowered
        or "playwright_browsers_path" in lowered
    ):
        return "本地 Worker 缺少浏览器运行时，请换用最新版离线包后重启 Worker。"
    if "自动化系统请求失败" in text and "500" in text:
        return "自动化系统请求失败：500，请检查本地 Worker 是否已启动并使用最新版离线包。"
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def linked_record_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ids = []
    for item in value:
        if isinstance(item, dict):
            record_id = item.get("id") or item.get("record_id")
            if record_id:
                ids.append(str(record_id))
        elif isinstance(item, str):
            ids.append(item)
    return ids


def style_labels(styles: tuple[str, ...]) -> list[str]:
    return [STYLE_LABELS.get(style, style) for style in styles]


def first_style(styles: tuple[str, ...]) -> tuple[str, ...]:
    return (styles[0],) if styles else ()


def infer_material_retry_step(fields: dict[str, Any], has_tasks: bool) -> str:
    prompt = str(field_value(fields, MATERIAL_PROMPT_FIELD, "") or "").strip()
    main_image = str(field_value(fields, "商品主图存储链接", "") or "").strip()
    group_images = split_urls(field_value(fields, "商品组图链接", ""))
    error_text = str(field_value(fields, "自动化错误", "") or "")
    if not prompt:
        return "analyze-video"
    if not main_image.startswith("http"):
        return "store-image"
    if not group_images:
        return "generate-images"
    if not has_tasks:
        return "create-tasks"
    if any(key in error_text for key in ("任务创建", "视频生成任务", "URLFieldConvFail")):
        return "create-tasks"
    if any(key in error_text for key in ("商品组图", "组图", "NewToken")):
        return "generate-images"
    if "主图" in error_text:
        return "store-image"
    if any(key in error_text for key in ("视频拆解", "Gemini", "爆款视频")):
        return "analyze-video"
    return "create-tasks"


def has_task_payload(fields: dict[str, Any]) -> bool:
    checks = (
        "生成任务名称",
        "视频生成Prompt",
        "首帧/参考图链接",
        "来源素材记录ID",
        "生成状态",
        "生成标题",
        "生成文案",
        "关键词",
        "生成视频链接",
        "CPA任务ID",
    )
    if any(str(field_value(fields, name, "") or "").strip() for name in checks):
        return True
    if linked_record_ids(fields.get("关联复刻素材")):
        return True
    for name in ("首帧/参考图", "生成视频"):
        value = fields.get(name)
        if isinstance(value, list):
            if any(isinstance(item, dict) and (item.get("file_token") or item.get("url") or item.get("name")) for item in value):
                return True
        elif value:
            return True
    return False


TASK_ANGLES = (
    "开头钩子更偏生活痛点，第一秒直接出现商品和目标用户困扰。",
    "重点放在商品上手/上身/使用动作，用连续近景展示真实使用过程。",
    "重点放在商品细节和质感，用特写镜头突出材质、结构、口袋、纹理或做工。",
    "重点放在场景结果，展示使用后状态、搭配效果或生活方式画面。",
    "重点放在 UGC 种草感，像普通用户发现好物后的自然分享。",
    "重点放在购买决策，用清晰结果镜头和短 CTA 收尾。",
)


def build_video_prompt(base_prompt: str, region: str, task_index: int, total_tasks: int, visual_angle: str) -> str:
    lang = local_language(region)
    base = str(base_prompt or "").strip()
    if not base:
        raise RuntimeError("视频生成 Prompt 为空，请先执行视频拆解")
    angle = TASK_ANGLES[(task_index - 1) % len(TASK_ANGLES)]
    variant_note = f"""
本条任务是 {total_tasks} 条视频任务中的第 {task_index} 条，只对应当前这一张参考图。
差异化方向：{visual_angle or angle}
不要复用其他任务的场景、人设、开场动作和收尾方式；保持爆款视频的节奏优点，但不要逐帧照抄参考视频。
本条视频只表达一个主要卖点、一个核心动作、一个清晰结果，避免塞太多镜头导致相似或混乱。
""".strip()
    constraints = f"""
补充硬性要求：
- 目标地区：{region}。
- 当前参考图就是本条视频的唯一商品依据，必须保持商品颜色、材质、形状、Logo、包装和关键细节一致。
- 如果爆款视频与商品参考图冲突，以商品参考图为准；爆款视频只用于学习节奏、镜头语言、转场和带货表达。
- 商品必须在主镜头和卖点镜头中清晰可见。
- 所有口播、旁白、字幕、屏幕文字必须使用 {lang}，不要使用英文。
- 避免低清、模糊、手部畸形、商品变形、错误 Logo、替换成其他商品、文字遮挡商品、人物或场景与其他任务高度重复。
""".strip()
    return "\n\n".join([base, variant_note, constraints]).strip()


class ViralRemixWorkflow:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.feishu = FeishuClient(settings)
        self.r2 = R2Storage(settings)
        self.tikhub = TikHubClient(settings)
        self.gemini = GeminiClient(settings)
        self.image_client = NewTokenImageClient(settings)
        self.cpa = CpaVideoClient(settings)
        self.video_assets = ViralVideoAssetService(settings, self.feishu, self.r2, self.tikhub)

    def list_prompt_configs(self) -> list[dict[str, Any]]:
        if not self.settings.prompt_table_id:
            return []
        records = self.feishu.list_records(self.settings.prompt_table_id)
        out = []
        for record in records:
            fields = record.get("fields", {})
            out.append({
                "record_id": record.get("record_id"),
                "Prompt名称": field_value(fields, "Prompt名称", ""),
                "Prompt类型": field_value(fields, "Prompt类型", ""),
                "模板Key": field_value(fields, "模板Key", ""),
                "版本": field_value(fields, "版本", ""),
                "是否启用": field_value(fields, "是否启用", ""),
                "目标地区": field_value(fields, "目标地区", ""),
                "模板内容": field_value(fields, "模板内容", ""),
                "变量说明": field_value(fields, "变量说明", ""),
                "备注": field_value(fields, "备注", ""),
            })
        return out

    def update_prompt_config(self, record_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed_fields = {
            "Prompt名称",
            "版本",
            "是否启用",
            "目标地区",
            "模板内容",
            "变量说明",
            "备注",
        }
        fields = {
            key: str(value or "").strip()
            for key, value in patch.items()
            if key in allowed_fields
        }
        if not fields:
            raise RuntimeError("没有可更新的 Prompt 字段")
        if "是否启用" in fields and fields["是否启用"] not in {"启用", "停用"}:
            raise RuntimeError("是否启用只能是：启用 / 停用")
        if "目标地区" in fields and fields["目标地区"] not in {"墨西哥", "马来", "通用"}:
            raise RuntimeError("目标地区只能是：墨西哥 / 马来 / 通用")
        if "模板内容" in fields and not fields["模板内容"]:
            raise RuntimeError("模板内容不能为空")
        return self.feishu.update_record(self.settings.prompt_table_id, record_id, fields)

    def _active_prompt_templates(self, region: str) -> tuple[dict[str, str], dict[str, str]]:
        templates: dict[str, tuple[int, str]] = {
            "video_analysis": (0, DEFAULT_VIDEO_ANALYSIS_TEMPLATE),
            **{f"image_{style}": (0, prompt) for style, prompt in DEFAULT_IMAGE_PROMPTS.items()},
        }
        versions: dict[str, str] = {key: "default" for key in templates}
        if not self.settings.prompt_table_id:
            return {key: value for key, (_, value) in templates.items()}, versions

        for row in self.list_prompt_configs():
            key = str(row.get("模板Key") or "").strip()
            content = str(row.get("模板内容") or "").strip()
            enabled = str(row.get("是否启用") or "").strip()
            target_region = str(row.get("目标地区") or "").strip()
            if not key or not content or enabled != "启用":
                continue
            rank = 2 if target_region == region else 1 if target_region in ("", "通用") else -1
            if rank < 0:
                continue
            current_rank = templates.get(key, (-1, ""))[0]
            if rank >= current_rank:
                templates[key] = (rank, content)
                version = str(row.get("版本") or "未标版本").strip()
                versions[key] = f"{key}:{version}"
        return {key: value for key, (_, value) in templates.items()}, versions

    def _active_prompt_by_key(self, key: str, region: str, fallback: str) -> tuple[str, str]:
        templates, versions = self._active_prompt_templates(region)
        return templates.get(key, fallback), versions.get(key, f"{key}:default")

    def list_materials(self, limit: int = 100) -> list[dict[str, Any]]:
        records = self.feishu.list_records(self.settings.source_table_id, max_records=limit)
        task_counts: dict[str, dict[str, int]] = {}
        for task in self.feishu.list_records(self.settings.task_table_id):
            fields = task.get("fields", {})
            source_ids = linked_record_ids(fields.get("关联复刻素材"))
            source_text = str(field_value(fields, "来源素材记录ID", "") or "").strip()
            if source_text and source_text not in source_ids:
                source_ids.append(source_text)
            status_value = str(field_value(fields, "生成状态", "") or "")
            for source_id in source_ids:
                bucket = task_counts.setdefault(source_id, {"total": 0, "done": 0})
                bucket["total"] += 1
                if status_value == "已完成":
                    bucket["done"] += 1
        out = []
        for index, record in enumerate(records, start=1):
            fields = record.get("fields", {})
            record_id = record.get("record_id")
            group_image_urls = split_urls(field_value(fields, "商品组图链接", ""))
            template_styles = first_style(normalize_styles(field_value(fields, "商品组图模板", []), self.settings.image_scene_styles))
            counts = task_counts.get(str(record_id), {"total": 0, "done": 0})
            video_prompt = str(field_value(fields, MATERIAL_PROMPT_FIELD, "") or "").strip()
            out.append({
                "行号": index,
                "record_id": record_id,
                "商品名称": field_value(fields, "商品名称"),
                "爆款视频链接": field_value(fields, "爆款视频链接"),
                "地区": field_value(fields, "地区", "墨西哥"),
                "生成标题": field_value(fields, "生成标题", ""),
                "生成文案": field_value(fields, "生成文案", ""),
                "关键词": field_value(fields, "关键词", ""),
                "自动化状态": field_value(fields, "自动化状态", ""),
                "自动化处理时间": field_value(fields, "自动化处理时间", ""),
                "商品组图链接": field_value(fields, "商品组图链接", ""),
                "商品组图URLs": group_image_urls,
                "商品组图数量": len(group_image_urls),
                "商品主图存储链接": field_value(fields, "商品主图存储链接", ""),
                "爆款首帧链接": field_value(fields, "爆款首帧链接", ""),
                "爆款视频直链": field_value(fields, "爆款视频直链", ""),
                "爆款视频直链解析时间": field_value(fields, "爆款视频直链解析时间", ""),
                "商品组图模板": style_labels(template_styles),
                "商品组图模板值": list(template_styles),
                "视频拆解提示词模板": field_value(fields, "视频拆解提示词模板", ""),
                "视频拆解实际提示词": field_value(fields, "视频拆解实际提示词", ""),
                "商品组图提示词模板": field_value(fields, "商品组图提示词模板", ""),
                "商品组图实际提示词": field_value(fields, "商品组图实际提示词", ""),
                "Prompt配置版本": field_value(fields, "Prompt配置版本", ""),
                "任务数量": counts["total"],
                "已完成视频数量": counts["done"],
                "视频生成Prompt": video_prompt,
                "has_script": bool(video_prompt),
                "has_prompt": bool(video_prompt),
            })
        return out

    def update_material_templates(self, record_id: str, styles: list[str]) -> dict[str, Any]:
        selected_styles = first_style(normalize_styles(styles, self.settings.image_scene_styles))
        labels = style_labels(selected_styles)
        persisted_labels = [] if selected_styles == ("viral_first_frame",) else labels
        self.feishu.update_record(
            self.settings.source_table_id,
            record_id,
            {"商品组图模板": persisted_labels},
        )
        return {"record_id": record_id, "商品组图模板": labels, "商品组图模板值": list(selected_styles)}

    def _material_has_tasks(self, record_id: str) -> bool:
        for task in self.feishu.list_records(self.settings.task_table_id):
            fields = task.get("fields", {})
            source_ids = linked_record_ids(fields.get("关联复刻素材"))
            source_text = str(field_value(fields, "来源素材记录ID", "") or "").strip()
            if source_text and source_text not in source_ids:
                source_ids.append(source_text)
            if record_id in source_ids and has_task_payload(fields):
                return True
        return False

    def retry_failed_material_step(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.source_table_id, record_id)
        fields = record.get("fields", {})
        step = infer_material_retry_step(fields, self._material_has_tasks(record_id))
        handlers = {
            "analyze-video": self.analyze_viral_video,
            "store-image": self.store_product_image,
            "generate-images": self.generate_product_images,
            "create-tasks": self.create_video_tasks,
        }
        add_log("info", f"重试失败素材步骤: {record_id} -> {step}", "workflow", job_id)
        return {"record_id": record_id, "step": step, "result": handlers[step](record_id, job_id=job_id)}

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        records = self.feishu.list_records(self.settings.task_table_id)
        out = []
        for index, record in enumerate(records, start=1):
            fields = record.get("fields", {})
            if not has_task_payload(fields):
                continue
            source_ids = linked_record_ids(fields.get("关联复刻素材"))
            source_text = str(field_value(fields, "来源素材记录ID", "") or "").strip()
            if source_text and source_text not in source_ids:
                source_ids.append(source_text)
            out.append({
                "行号": index,
                "record_id": record.get("record_id"),
                "生成任务名称": field_value(fields, "生成任务名称"),
                "生成状态": field_value(fields, "生成状态", ""),
                "生成进度": field_value(fields, "生成进度", 0),
                "生成标题": field_value(fields, "生成标题", ""),
                "生成文案": field_value(fields, "生成文案", ""),
                "关键词": field_value(fields, "关键词", ""),
                "任务序号": field_value(fields, "任务序号", ""),
                "生成视频链接": field_value(fields, "生成视频链接", ""),
                "来源素材记录ID": source_text,
                "关联素材记录IDs": source_ids,
                "首帧/参考图链接": field_value(fields, "首帧/参考图链接", ""),
                "首帧附件数": attachment_count(fields, "首帧/参考图"),
                "生成视频附件数": attachment_count(fields, "生成视频"),
                "失败原因": field_value(fields, "失败原因", ""),
                "生成时间": field_value(fields, "生成时间", ""),
            })
            if len(out) >= limit:
                break
        return out

    def _doubao_items_from_records(
        self,
        records: list[dict[str, Any]],
        start_index: int = 1,
    ) -> list[dict[str, Any]]:
        out = []
        for index, record in enumerate(records, start=start_index):
            fields = record.get("fields", {})
            attachment = first_attachment(fields, "商品图")
            image_url = str(field_value(fields, "商品图链接", "") or "").strip()
            stored_image_url = str(field_value(fields, "商品图存储链接", "") or "").strip()
            video_url = first_available_field(fields, (DOLA_VIDEO_FIELD, "视频生成链接", "视频链接"), "")
            dola_status = str(field_value(fields, DOLA_STATUS_FIELD, "") or "").strip()
            if not dola_status:
                dola_status = "已完成" if str(video_url or "").startswith("http") else "待Dola生成"
            out.append({
                "行号": index,
                "record_id": record.get("record_id"),
                "商品名称": field_value(fields, "商品名称", ""),
                "商品链接": field_value(fields, "商品链接", ""),
                "商品图链接": image_url,
                "商品图存储链接": stored_image_url,
                "图片来源": "附件" if attachment else "链接" if image_url.startswith("http") else "",
                "商品图数量": len(fields.get("商品图") or []),
                "商品图文件名": (attachment or {}).get("name", ""),
                "最终视频生成提示词": field_value(fields, "最终视频生成提示词", ""),
                "标题": field_value(fields, "标题", ""),
                "文案": field_value(fields, "文案", ""),
                "标签": field_value(fields, "标签", ""),
                "视频生成链接": video_url,
                DOLA_STATUS_FIELD: dola_status,
                DOLA_VIDEO_FIELD: field_value(fields, DOLA_VIDEO_FIELD, ""),
                DOLA_ERROR_FIELD: field_value(fields, DOLA_ERROR_FIELD, ""),
                "更新时间": record.get("last_modified_time") or record.get("modified_time") or record.get("updated_time") or "",
                "创建时间": record.get("created_time") or "",
            })
        return out

    def list_doubao_items(self, limit: int = 100) -> list[dict[str, Any]]:
        records = self.feishu.list_records(self.settings.doubao_table_id, max_records=limit)
        return self._doubao_items_from_records(records)

    def list_doubao_items_page(
        self,
        page_size: int = 100,
        page_token: str = "",
        start_index: int = 1,
    ) -> dict[str, Any]:
        page = self.feishu.list_records_page(
            self.settings.doubao_table_id,
            page_size=page_size,
            page_token=page_token,
        )
        return {
            "items": self._doubao_items_from_records(page["items"], start_index=start_index),
            "has_more": page["has_more"],
            "page_token": page["page_token"],
        }

    def list_dola_local_tasks(self, limit: int = 100, include_done: bool = False) -> list[dict[str, Any]]:
        records = self.feishu.list_records(self.settings.doubao_table_id, max_records=limit)
        tasks = [build_dola_local_task(record, index) for index, record in enumerate(records, start=1)]
        if include_done:
            return tasks
        return [
            task
            for task in tasks
            if task.get("可准备")
            or str(task.get(DOLA_STATUS_FIELD) or "") in {"准备中", "等待发送", "等待下载", "失败"}
        ]

    def get_dola_local_task(self, record_id: str) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.doubao_table_id, record_id)
        task = build_dola_local_task(record, self._doubao_row_number(record_id))
        if not task.get("参考图链接", "").startswith("http"):
            stored = self.store_doubao_image(record_id)
            record = self.feishu.get_record(self.settings.doubao_table_id, record_id)
            task = build_dola_local_task(record, self._doubao_row_number(record_id))
            if not task.get("参考图链接", "").startswith("http"):
                task["参考图链接"] = str(stored.get("stored_image_url") or "")
        if not str(task.get("最终视频生成提示词") or "").strip():
            raise RuntimeError("缺少最终视频生成提示词，请先在豆包视频页生成提示词")
        if not str(task.get("参考图链接") or "").startswith("http"):
            raise RuntimeError("缺少可用参考图链接，请先完成图片入库")
        return task

    def _doubao_row_number(self, record_id: str) -> int | None:
        records = self.feishu.list_records(self.settings.doubao_table_id, max_records=1000)
        for index, record in enumerate(records, start=1):
            if str(record.get("record_id") or "") == str(record_id):
                return index
        return None

    def update_dola_local_status(self, record_id: str, status: str, error: str = "") -> dict[str, Any]:
        status = str(status or "").strip()
        if status not in DOLA_LOCAL_STATUSES:
            raise RuntimeError(f"Dola 状态不支持：{status}")
        patch: dict[str, Any] = {DOLA_STATUS_FIELD: status}
        if error or status != "失败":
            patch[DOLA_ERROR_FIELD] = public_error_message(error) if error else ""
        else:
            patch[DOLA_ERROR_FIELD] = "未填写失败原因"
        return self.feishu.update_record(self.settings.doubao_table_id, record_id, patch)

    def store_dola_local_video_file(
        self,
        record_id: str,
        file_path: Path,
        content_type: str = "video/mp4",
    ) -> dict[str, Any]:
        if not file_path.exists():
            raise RuntimeError(f"视频文件不存在: {file_path}")
        task = self.get_dola_local_task(record_id)
        safe_name = str(task.get("商品名称") or record_id).strip() or record_id
        stored = self.r2.upload_bytes(file_path.read_bytes(), f"dola-{record_id}-{safe_name}.mp4", content_type)
        self.feishu.update_record(
            self.settings.doubao_table_id,
            record_id,
            {
                DOLA_STATUS_FIELD: "已完成",
                DOLA_VIDEO_FIELD: url_cell(stored["url"]),
                DOLA_ERROR_FIELD: "",
            },
        )
        return {"record_id": record_id, "video_url": stored["url"]}

    def _resolve_doubao_image(self, fields: dict[str, Any], prefer_stored: bool = True) -> tuple[bytes, str, str]:
        stored_url = str(field_value(fields, "商品图存储链接", "") or "").strip()
        if prefer_stored and stored_url.startswith("http"):
            data, mime = self._download_url(stored_url)
            return data, mime, stored_url.rsplit("/", 1)[-1] or "doubao-stored-image"

        attachment = first_attachment(fields, "商品图")
        if attachment:
            file_token = attachment.get("file_token")
            if not file_token:
                raise RuntimeError("商品图附件缺少 file_token，无法下载")
            with TemporaryDirectory() as tmpdir:
                filename = str(attachment.get("name") or "doubao-product.png")
                path, mime = self.feishu.download_media(str(file_token), Path(tmpdir) / filename)
                return path.read_bytes(), mime, path.name

        image_url = str(field_value(fields, "商品图链接", "") or "").strip()
        if image_url.startswith("http"):
            data, mime = self._download_url(image_url)
            return data, mime, image_url.rsplit("/", 1)[-1] or "doubao-product-url"

        raise RuntimeError("缺少商品图：请上传商品图附件，或填写商品图链接")

    def store_doubao_image(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.doubao_table_id, record_id)
        fields = record.get("fields", {})
        product_name = str(field_value(fields, "商品名称", record_id) or record_id).strip()
        add_log("info", f"开始豆包商品图入库: {product_name}", "workflow", job_id)
        data, mime, filename = self._resolve_doubao_image(fields, prefer_stored=False)
        stored = self.r2.upload_bytes(data, filename or "doubao-product-image", mime)
        self.feishu.update_record(
            self.settings.doubao_table_id,
            record_id,
            {"商品图存储链接": url_cell(stored["url"])},
        )
        add_log("success", f"豆包商品图入库完成: {stored['url']}", "workflow", job_id)
        return {"record_id": record_id, "stored_image_url": stored["url"]}

    def generate_doubao_prompt(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.doubao_table_id, record_id)
        fields = record.get("fields", {})
        product_name = str(field_value(fields, "商品名称", "") or "").strip()
        product_link = str(field_value(fields, "商品链接", "") or "").strip()
        image_bytes, image_mime, filename = self._resolve_doubao_image(fields, prefer_stored=True)
        prompt_rules, prompt_version = self._active_prompt_by_key(DOUBAO_PROMPT_KEY, "马来", DOUBAO_FALLBACK_PROMPT)
        add_log("info", f"开始生成豆包视频提示词: {product_name or filename}", "workflow", job_id)
        result = self.gemini.generate_doubao_video_prompt(
            product_name=product_name,
            product_link=product_link,
            prompt_rules=prompt_rules,
            image_bytes=image_bytes,
            image_mime=image_mime,
        )
        self.feishu.update_record(
            self.settings.doubao_table_id,
            record_id,
            {
                "最终视频生成提示词": result["final_video_prompt"],
                "标题": result["title"],
                "文案": result["caption"],
                "标签": result["tags"],
            },
        )
        add_log("success", f"豆包视频提示词已回填: {product_name or filename} / {prompt_version}", "workflow", job_id)
        return {"record_id": record_id, "prompt_version": prompt_version, "has_prompt": True}

    def _download_url(self, url: str, with_provider_auth: bool = False) -> tuple[bytes, str]:
        headers = {
            "Accept": "image/avif,image/webp,image/png,image/jpeg,video/mp4,video/*,*/*;q=0.8",
            "Referer": "https://www.tiktok.com/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        }
        if with_provider_auth:
            headers["Authorization"] = f"Bearer {self.settings.newtoken_api_key}"
        resp = requests.get(url, headers=headers, timeout=120)
        if resp.status_code >= 400 and not with_provider_auth and self.settings.newtoken_base_url in url:
            return self._download_url(url, with_provider_auth=True)
        if resp.status_code >= 400:
            raise RuntimeError(f"download failed: HTTP {resp.status_code}: {url[:120]}")
        return resp.content, resp.headers.get("content-type", "application/octet-stream").split(";")[0]

    def _resolve_product_image(self, fields: dict[str, Any], job_id: str | None) -> tuple[str, bytes | None, str | None]:
        stored_direct = str(field_value(fields, "商品主图存储链接", "") or "").strip()
        if stored_direct.startswith("http"):
            data, mime = self._download_url(stored_direct)
            return stored_direct, data, mime

        attachment = first_attachment(fields, "商品图片")
        if attachment:
            file_token = attachment.get("file_token")
            if file_token:
                with TemporaryDirectory() as tmpdir:
                    filename = str(attachment.get("name") or "product-image.png")
                    path, mime = self.feishu.download_media(str(file_token), Path(tmpdir) / filename)
                    data = path.read_bytes()
                    stored = self.r2.upload_bytes(data, path.name, mime)
                    add_log("info", f"商品图片附件已转存对象存储: {stored['url']}", "workflow", job_id)
                    return stored["url"], data, mime

            for key in ("url", "tmp_url", "preview_url"):
                value = attachment.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    data, mime = self._download_url(value)
                    stored = self.r2.upload_bytes(data, "product-image.png", mime)
                    add_log("info", f"商品图片附件 URL 已转存对象存储: {stored['url']}", "workflow", job_id)
                    return stored["url"], data, mime

        raise RuntimeError("缺少可下载的商品图片：请上传商品图片附件")

    def _rehost_image(self, url: str, style: str) -> str:
        data, mime = self._download_url(url)
        suffix = ".jpg"
        if "png" in mime:
            suffix = ".png"
        elif "webp" in mime:
            suffix = ".webp"
        stored = self.r2.upload_bytes(data, f"{style}{suffix}", mime)
        return stored["url"]

    def analyze_viral_video(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.source_table_id, record_id)
        fields = record.get("fields", {})
        product_name = str(field_value(fields, "商品名称", "") or "").strip()
        region = str(field_value(fields, "地区", "墨西哥") or "墨西哥").strip()
        viral_url = str(field_value(fields, "爆款视频链接", "") or "").strip()
        custom_prompt = str(field_value(fields, "生图自定义提示词", "") or "").strip()
        prompt_override = str(field_value(fields, "视频拆解提示词模板", "") or "").strip()

        if not product_name:
            raise RuntimeError("商品名称为空")
        if not viral_url:
            raise RuntimeError("爆款视频链接为空")

        self.feishu.update_record(
            self.settings.source_table_id,
            record_id,
            {"自动化状态": "视频拆解中", "自动化错误": "", "自动化处理时间": now_ms()},
        )
        add_log("info", f"开始拆解爆款视频: {product_name}", "workflow", job_id)

        try:
            video_bytes, video_mime = self.video_assets.download_cached(record_id, fields, job_id)
            first_frame_url = ""
            try:
                first_frame_url = self.video_assets.extract_first_frame(video_bytes, job_id)
            except Exception as exc:
                add_log("error", f"爆款视频首帧截取失败，将继续拆解视频: {exc}", "workflow", job_id)
            prompt_templates, prompt_versions = self._active_prompt_templates(region)
            prompt_template = prompt_override or prompt_templates.get("video_analysis", DEFAULT_VIDEO_ANALYSIS_TEMPLATE)
            analysis = self.gemini.analyze_video(
                product_name=product_name,
                region=region,
                custom_prompt=custom_prompt,
                prompt_template=prompt_template,
                video_bytes=video_bytes,
                video_mime=video_mime,
                image_bytes=None,
                image_mime=None,
            )
            version_label = "视频拆解:素材覆盖" if prompt_override else prompt_versions.get("video_analysis", "video_analysis:default")
            self.feishu.update_record(
                self.settings.source_table_id,
                record_id,
                {
                    MATERIAL_PROMPT_FIELD: analysis["video_prompt"],
                    "生成标题": analysis["publish_title"],
                    "生成文案": analysis["publish_caption"],
                    "关键词": analysis["keywords"],
                    "爆款首帧链接": url_cell(first_frame_url) if first_frame_url else "",
                    "视频拆解实际提示词": analysis.get("analysis_prompt", ""),
                    "Prompt配置版本": version_label,
                    "自动化状态": "视频已拆解",
                    "自动化错误": "",
                    "自动化处理时间": now_ms(),
                },
            )
            add_log("success", "爆款视频拆解完成", "workflow", job_id)
            return {"record_id": record_id, "has_script": True}
        except Exception as exc:
            self.feishu.update_record(
                self.settings.source_table_id,
                record_id,
                {"自动化状态": "失败", "自动化错误": public_error_message(exc), "自动化处理时间": now_ms()},
            )
            add_log("error", f"爆款视频拆解失败: {exc}", "workflow", job_id)
            raise

    def store_product_image(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.source_table_id, record_id)
        fields = record.get("fields", {})
        product_name = str(field_value(fields, "商品名称", record_id) or record_id).strip()

        self.feishu.update_record(
            self.settings.source_table_id,
            record_id,
            {"自动化状态": "主图入库中", "自动化错误": "", "自动化处理时间": now_ms()},
        )
        add_log("info", f"开始商品主图入库: {product_name}", "workflow", job_id)

        try:
            product_image_url, _, _ = self._resolve_product_image(fields, job_id)
            self.feishu.update_record(
                self.settings.source_table_id,
                record_id,
                {
                    "商品主图存储链接": url_cell(product_image_url),
                    "自动化状态": "主图已入库",
                    "自动化错误": "",
                    "自动化处理时间": now_ms(),
                },
            )
            add_log("success", f"商品主图入库完成: {product_image_url}", "workflow", job_id)
            return {"record_id": record_id, "product_image_url": product_image_url}
        except Exception as exc:
            self.feishu.update_record(
                self.settings.source_table_id,
                record_id,
                {"自动化状态": "失败", "自动化错误": public_error_message(exc), "自动化处理时间": now_ms()},
            )
            add_log("error", f"商品主图入库失败: {exc}", "workflow", job_id)
            raise

    def generate_product_images(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.source_table_id, record_id)
        fields = record.get("fields", {})
        product_image_url = str(field_value(fields, "商品主图存储链接", "") or "").strip()
        custom_prompt = str(field_value(fields, "生图自定义提示词", "") or "").strip()
        region = str(field_value(fields, "地区", "墨西哥") or "墨西哥").strip()
        prompt_override = str(field_value(fields, "商品组图提示词模板", "") or "").strip()
        selected_styles = first_style(normalize_styles(field_value(fields, "商品组图模板", []), self.settings.image_scene_styles))

        if not product_image_url.startswith("http"):
            raise RuntimeError("商品主图还没有入库，请先执行主图入库")

        self.feishu.update_record(
            self.settings.source_table_id,
            record_id,
            {"自动化状态": "组图生成中", "自动化错误": "", "自动化处理时间": now_ms()},
        )
        add_log("info", "开始生成商品组图", "workflow", job_id)

        try:
            prompt_templates, prompt_versions = self._active_prompt_templates(region)
            first_frame_url = ""
            effective_styles = selected_styles
            first_frame_warning = ""
            if selected_styles == ("viral_first_frame",):
                try:
                    first_frame_url = self.video_assets.ensure_first_frame(record_id, fields, job_id)
                except Exception as exc:
                    first_frame_warning = f"爆款首帧获取失败，已回退生活场景模板：{exc}"
                    effective_styles = ("lifestyle",)
                    add_log("error", first_frame_warning, "workflow", job_id)
                if not first_frame_url and effective_styles == selected_styles:
                    first_frame_warning = "爆款首帧为空，已回退生活场景模板"
                    effective_styles = ("lifestyle",)
                    add_log("error", first_frame_warning, "workflow", job_id)
            generated = self.image_client.generate_group_images(
                product_image_url,
                custom_prompt,
                effective_styles,
                prompt_templates=prompt_templates,
                override_template=prompt_override,
                region=region,
                language=local_language(region),
                reference_image_url=first_frame_url,
            )
            image_urls = []
            failures = []
            for item in generated:
                if item.get("error"):
                    message = f"{item['style']}: {item['error']}"
                    failures.append(message)
                    add_log("error", f"商品组图生成失败({item['style']}): {item['error']}", "workflow", job_id)
                    continue
                try:
                    image_urls.append(self._rehost_image(item["url"], item["style"]))
                except Exception as exc:
                    failures.append(f"{item['style']}: {exc}")
                    add_log("error", f"商品组图转存失败({item['style']}): {exc}", "workflow", job_id)
            if not image_urls:
                raise RuntimeError("商品组图全部生成或转存失败，请检查图片模型返回 URL 是否可下载")
            if prompt_override:
                version_label = "商品组图:素材覆盖"
            else:
                used_versions = [prompt_versions.get(f"image_{style}", f"image_{style}:default") for style in effective_styles]
                version_label = " / ".join(dict.fromkeys(used_versions))
            warning_text = "；".join([item for item in [first_frame_warning, "部分组图失败：" + "；".join(failures) if failures else ""] if item])
            self.feishu.update_record(
                self.settings.source_table_id,
                record_id,
                {
                    "商品组图链接": "\n".join(image_urls),
                    "商品组图模板": [] if selected_styles == ("viral_first_frame",) else style_labels(selected_styles),
                    "商品组图实际提示词": image_prompt_snapshot(generated),
                    "Prompt配置版本": version_label,
                    "自动化状态": "组图已生成",
                    "自动化错误": warning_text[:1000],
                    "自动化处理时间": now_ms(),
                },
            )
            add_log("success", f"商品组图生成完成: {len(image_urls)} 张 / 失败 {len(failures)} 张", "workflow", job_id)
            return {"record_id": record_id, "image_urls": image_urls, "failures": failures}
        except Exception as exc:
            self.feishu.update_record(
                self.settings.source_table_id,
                record_id,
                {"自动化状态": "失败", "自动化错误": public_error_message(exc), "自动化处理时间": now_ms()},
            )
            add_log("error", f"商品组图生成失败: {exc}", "workflow", job_id)
            raise

    def create_video_tasks(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.source_table_id, record_id)
        fields = record.get("fields", {})
        product_name = str(field_value(fields, "商品名称", "") or "").strip()
        region = str(field_value(fields, "地区", "墨西哥") or "墨西哥").strip()
        image_urls = split_urls(field_value(fields, "商品组图链接", ""))[:1]
        base_prompt = str(field_value(fields, MATERIAL_PROMPT_FIELD, "") or "").strip()
        publish_title = str(field_value(fields, "生成标题", "") or product_name).strip()
        publish_caption = str(field_value(fields, "生成文案", "") or "").strip()
        keywords = str(field_value(fields, "关键词", "") or "").strip()
        selected_styles = first_style(normalize_styles(field_value(fields, "商品组图模板", []), self.settings.image_scene_styles))

        if not product_name:
            raise RuntimeError("商品名称为空")
        if not base_prompt:
            raise RuntimeError("视频生成 Prompt 为空，请先执行视频拆解")
        if not image_urls:
            raise RuntimeError("商品组图为空，请先生成商品组图")

        self.feishu.update_record(
            self.settings.source_table_id,
            record_id,
            {"自动化状态": "任务创建中", "自动化错误": "", "自动化处理时间": now_ms()},
        )
        add_log("info", f"开始创建视频生成任务: {product_name}", "workflow", job_id)

        try:
            task_total = len(image_urls)
            existing_tasks: dict[str, str] = {}
            for task_record in self.feishu.list_records(self.settings.task_table_id):
                task_fields = task_record.get("fields", {})
                source_ids = linked_record_ids(task_fields.get("关联复刻素材"))
                source_text = str(field_value(task_fields, "来源素材记录ID", "") or "").strip()
                if source_text and source_text not in source_ids:
                    source_ids.append(source_text)
                if record_id in source_ids:
                    existing_url = str(field_value(task_fields, "首帧/参考图链接", "") or "").strip()
                    if existing_url:
                        existing_tasks[existing_url] = str(task_record.get("record_id") or "")
            created_task_ids = []
            updated_task_ids = []
            for index, image_url in enumerate(image_urls, start=1):
                style = selected_styles[(index - 1) % len(selected_styles)] if selected_styles else ""
                visual_angle = STYLE_LABELS.get(style, "") if style else ""
                prompt = build_video_prompt(base_prompt, region, index, task_total, visual_angle)
                existing_task_id = existing_tasks.get(image_url)
                if existing_task_id:
                    self.feishu.update_record(
                        self.settings.task_table_id,
                        existing_task_id,
                        {
                            "关联复刻素材": [record_id],
                            "视频拆解脚本": base_prompt,
                            "生成标题": publish_title or product_name,
                            "生成文案": publish_caption,
                            "关键词": keywords,
                            "视频生成Prompt": prompt,
                        },
                    )
                    updated_task_ids.append(existing_task_id)
                    continue
                fields_to_create = {
                    "生成任务名称": f"{product_name} - 视频任务 {index}",
                    "来源素材记录ID": record_id,
                    "关联复刻素材": [record_id],
                    "任务序号": index,
                    "视频拆解脚本": base_prompt,
                    "生成标题": publish_title or product_name,
                    "生成文案": publish_caption,
                    "关键词": keywords,
                    "首帧/参考图链接": url_cell(image_url),
                    "视频生成Prompt": prompt,
                    "生成状态": "待生成视频",
                    "生成进度": 0,
                }
                try:
                    task = self.feishu.create_record(self.settings.task_table_id, fields_to_create)
                except Exception:
                    fields_to_create.pop("关联复刻素材", None)
                    task = self.feishu.create_record(self.settings.task_table_id, fields_to_create)
                created_task_ids.append(task.get("record_id"))

            self.feishu.update_record(
                self.settings.source_table_id,
                record_id,
                {"自动化状态": "已创建任务", "自动化错误": "", "自动化处理时间": now_ms()},
            )
            add_log(
                "success",
                f"视频生成任务创建完成: 新增 {len(created_task_ids)} 条 / 更新 {len(updated_task_ids)} 条 / 应有 {task_total} 条",
                "workflow",
                job_id,
            )
            return {"record_id": record_id, "task_ids": created_task_ids, "updated_task_ids": updated_task_ids}
        except Exception as exc:
            self.feishu.update_record(
                self.settings.source_table_id,
                record_id,
                {"自动化状态": "失败", "自动化错误": public_error_message(exc), "自动化处理时间": now_ms()},
            )
            add_log("error", f"视频生成任务创建失败: {exc}", "workflow", job_id)
            raise

    def prepare_material(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        self.analyze_viral_video(record_id, job_id=job_id)
        self.store_product_image(record_id, job_id=job_id)
        images = self.generate_product_images(record_id, job_id=job_id)
        tasks = self.create_video_tasks(record_id, job_id=job_id)
        return {"record_id": record_id, "task_ids": tasks["task_ids"], "image_urls": images["image_urls"]}

    def generate_task_video(self, task_record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.task_table_id, task_record_id)
        fields = record.get("fields", {})
        prompt = str(field_value(fields, "视频生成Prompt", "") or "").strip()
        image_url = str(field_value(fields, "首帧/参考图链接", "") or "").strip()
        task_name = str(field_value(fields, "生成任务名称", task_record_id) or task_record_id)

        if not prompt:
            prompt = str(field_value(fields, "视频拆解脚本", "") or "").strip()
        if not image_url:
            raise RuntimeError("任务缺少首帧/参考图链接")

        self.feishu.update_record(
            self.settings.task_table_id,
            task_record_id,
            {"生成状态": "视频生成中", "生成进度": 0, "失败原因": ""},
        )
        add_log("info", f"开始生成视频: {task_name}", "workflow", job_id)

        try:
            last_progress = {"value": -1}

            def update_progress(progress: int, cpa_status: str) -> None:
                if progress <= last_progress["value"] and cpa_status != "completed":
                    return
                last_progress["value"] = progress
                self.feishu.update_record(
                    self.settings.task_table_id,
                    task_record_id,
                    {"生成状态": "视频生成中", "生成进度": round(progress / 100, 2)},
                )
                add_log("info", f"视频生成进度 {progress}%: {task_name}", "workflow", job_id)

            def update_submission(video_id: str) -> None:
                self.feishu.update_record(
                    self.settings.task_table_id,
                    task_record_id,
                    {"CPA任务ID": video_id, "生成状态": "视频生成中"},
                )
                add_log("info", f"CPA任务已提交: {video_id}", "workflow", job_id)

            result = self.cpa.generate(
                prompt,
                image_url,
                progress_callback=update_progress,
                submit_callback=update_submission,
            )
            stored = self.r2.upload_bytes(result["data"], f"{result['video_id']}.mp4", result["mime_type"])
            self.feishu.update_record(
                self.settings.task_table_id,
                task_record_id,
                {
                    "生成状态": "已完成",
                    "生成进度": 1,
                    "生成视频链接": url_cell(stored["url"]),
                    "CPA任务ID": result["video_id"],
                    "失败原因": "",
                    "生成时间": now_ms(),
                },
            )
            add_log("success", f"视频完成: {stored['url']}", "workflow", job_id)
            return {"task_record_id": task_record_id, "video_url": stored["url"], "cpa_video_id": result["video_id"]}
        except Exception as exc:
            self.feishu.update_record(
                self.settings.task_table_id,
                task_record_id,
                {"生成状态": "失败", "失败原因": public_error_message(exc)},
            )
            add_log("error", f"视频生成失败: {exc}", "workflow", job_id)
            raise

    def _filename_for_download(self, url: str, content_type: str, fallback: str) -> str:
        suffix = Path(str(url).split("?", 1)[0]).suffix
        if not suffix:
            if "png" in content_type:
                suffix = ".png"
            elif "webp" in content_type:
                suffix = ".webp"
            elif "jpeg" in content_type or "jpg" in content_type:
                suffix = ".jpg"
            elif "mp4" in content_type:
                suffix = ".mp4"
            else:
                suffix = ".bin"
        return f"{fallback}{suffix}"

    def sync_task_reference_image(self, task_record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.task_table_id, task_record_id)
        fields = record.get("fields", {})
        task_name = str(field_value(fields, "生成任务名称", task_record_id) or task_record_id)
        image_url = str(field_value(fields, "首帧/参考图链接", "") or "").strip()

        if attachment_count(fields, "首帧/参考图"):
            add_log("info", f"参考图已在任务表附件中，跳过: {task_name}", "workflow", job_id)
            return {"task_record_id": task_record_id, "skipped": True, "reason": "attachment_exists"}
        if not image_url.startswith("http"):
            raise RuntimeError("任务缺少首帧/参考图链接，无法渲染成飞书图片附件")

        add_log("info", f"开始参考图入表: {task_name}", "workflow", job_id)
        data, mime = self._download_url(image_url)
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / self._filename_for_download(image_url, mime, f"reference-{task_record_id}")
            path.write_bytes(data)
            uploaded = self.feishu.upload_base_attachment(
                self.settings.task_table_id,
                task_record_id,
                "首帧/参考图",
                path,
                mime,
            )
        add_log("success", f"参考图入表完成: {task_name}", "workflow", job_id)
        return {"task_record_id": task_record_id, "file_token": uploaded["file_token"]}

    def _is_managed_storage_url(self, url: str) -> bool:
        if not url.startswith("http"):
            return False
        public_base = self.settings.r2_public_url.rstrip("/")
        if public_base and url.startswith(public_base):
            return True
        return "vidclaw-assets/viral-remix-7d/" in url or "viral-remix-7d/" in url

    def store_manual_task_video(self, task_record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.task_table_id, task_record_id)
        fields = record.get("fields", {})
        task_name = str(field_value(fields, "生成任务名称", task_record_id) or task_record_id)
        existing_url = str(field_value(fields, "生成视频链接", "") or "").strip()

        if self._is_managed_storage_url(existing_url):
            add_log("info", f"视频已是对象存储链接，跳过入库: {task_name}", "workflow", job_id)
            return {"task_record_id": task_record_id, "video_url": existing_url, "skipped": True}

        add_log("info", f"开始人工视频入库: {task_name}", "workflow", job_id)
        attachment = first_attachment(fields, "生成视频")
        if attachment and attachment.get("file_token"):
            with TemporaryDirectory() as tmpdir:
                filename = str(attachment.get("name") or f"manual-video-{task_record_id}.mp4")
                path, mime = self.feishu.download_media(str(attachment["file_token"]), Path(tmpdir) / filename)
                stored = self.r2.upload_file(path, mime if mime.startswith("video/") else "video/mp4")
        elif existing_url.startswith("http"):
            data, mime = self._download_url(existing_url)
            filename = self._filename_for_download(existing_url, mime, f"manual-video-{task_record_id}")
            stored = self.r2.upload_bytes(data, filename, mime if mime.startswith("video/") else "video/mp4")
        else:
            raise RuntimeError("没有可入库的视频：请先在任务表上传“生成视频”附件，或填写“生成视频链接”")

        self.feishu.update_record(
            self.settings.task_table_id,
            task_record_id,
            {
                "生成状态": "已完成",
                "生成进度": 1,
                "生成视频链接": url_cell(stored["url"]),
                "失败原因": "",
                "生成时间": now_ms(),
            },
        )
        add_log("success", f"人工视频入库完成: {stored['url']}", "workflow", job_id)
        return {"task_record_id": task_record_id, "video_url": stored["url"]}

    def sync_publish_items(self, record_ids: list[str] | None = None, limit: int = 100, job_id: str | None = None) -> dict[str, Any]:
        if not self.settings.publish_table_id:
            raise RuntimeError("缺少 FEISHU_PUBLISH_TABLE_ID")

        existing_by_video: dict[str, str] = {}
        for record in self.feishu.list_records(self.settings.publish_table_id):
            fields = record.get("fields", {})
            video_url = str(field_value(fields, "视频链接", "") or "").strip()
            if video_url.startswith("http"):
                existing_by_video[video_url] = str(record.get("record_id") or "")

        rows = self.list_tasks(limit=max(limit, 200))
        if record_ids:
            allowed = set(record_ids)
            rows = [row for row in rows if row.get("record_id") in allowed]
        rows = [row for row in rows if str(row.get("生成视频链接") or "").startswith("http")][:limit]

        created = 0
        updated = 0
        skipped = 0
        for row in rows:
            video_url = str(row.get("生成视频链接") or "").strip()
            task_name = str(row.get("生成任务名称") or "").strip()
            product_title = task_name.rsplit(" - 视频任务 ", 1)[0] if " - 视频任务 " in task_name else task_name
            source_ids = row.get("关联素材记录IDs") if isinstance(row.get("关联素材记录IDs"), list) else []
            fields = {
                "标题": str(row.get("生成标题") or product_title).strip(),
                "文案": str(row.get("生成文案") or "").strip(),
                "标签": str(row.get("关键词") or "").strip(),
                "商品标题": product_title,
                "视频链接": video_url,
                "生成时间": now_ms(),
                "发布状态": "待发布",
            }
            if source_ids:
                fields["关联复刻素材"] = source_ids[:1]
            try:
                existing_id = existing_by_video.get(video_url)
                if existing_id:
                    try:
                        self.feishu.update_record(self.settings.publish_table_id, existing_id, fields)
                    except Exception:
                        fields.pop("关联复刻素材", None)
                        self.feishu.update_record(self.settings.publish_table_id, existing_id, fields)
                    updated += 1
                else:
                    try:
                        created_record = self.feishu.create_record(self.settings.publish_table_id, fields)
                    except Exception:
                        fields.pop("关联复刻素材", None)
                        created_record = self.feishu.create_record(self.settings.publish_table_id, fields)
                    existing_by_video[video_url] = str(created_record.get("record_id") or "")
                    created += 1
            except Exception as exc:
                skipped += 1
                add_log("error", f"发布表同步失败({row.get('record_id')}): {exc}", "workflow", job_id)

        add_log("success", f"发布表同步完成: 新增 {created} / 更新 {updated} / 跳过 {skipped}", "workflow", job_id)
        return {"created": created, "updated": updated, "skipped": skipped}
