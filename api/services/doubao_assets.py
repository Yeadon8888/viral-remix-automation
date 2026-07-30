from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from ..feishu import field_value, first_attachment
from ..logging_bus import add_log


FACE_STATUS_FIELD = "人脸检测状态"
FACE_DETAIL_FIELD = "人脸检测说明"
FACE_TIME_FIELD = "人脸检测时间"
FACE_IMAGE_FIELD = "人脸检测图片链接"
FACE_MODEL_FIELD = "人脸检测模型"

FACE_CLEAR = "无人脸"
FACE_PRESENT = "含人脸"
FACE_UNCERTAIN = "疑似人脸"
FACE_PENDING = "待检测"
FACE_EXTERNAL = "待转存"
FACE_RUNNING = "检测中"
FACE_FAILED = "检测失败"
FACE_REMOVED = "已移除"
FLAGGED_FACE_STATES = {FACE_PRESENT, FACE_UNCERTAIN}
LOCKED_SYNC_STATES = {"已同步", "同步冲突"}


def now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def url_cell(url: str) -> dict[str, str]:
    return {"text": url, "link": url}


def product_id_from_fields(fields: dict[str, Any]) -> str:
    for name in ("商品ID", "商品id", "商品Id", "商品 ID"):
        value = str(field_value(fields, name, "") or "").strip()
        if value:
            return value
    return ""


def _image_mime(data: bytes, declared_mime: str) -> str:
    declared = str(declared_mime or "").split(";")[0].strip().lower()
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        return "image/avif"
    if declared.startswith("image/"):
        return declared
    raise RuntimeError("对象存储链接返回的内容不是可识别的图片")


class DoubaoAssetService:
    """Owns the image-storage and face-safety invariants for Doubao materials."""

    def __init__(
        self,
        settings,
        feishu,
        storage,
        gemini,
        download_url: Callable[[str], tuple[bytes, str]],
    ):
        self.settings = settings
        self.feishu = feishu
        self.storage = storage
        self.gemini = gemini
        self.download_url = download_url

    def describe(self, fields: dict[str, Any]) -> dict[str, Any]:
        stored_url = str(field_value(fields, "商品图存储链接", "") or "").strip()
        source_url = str(field_value(fields, "商品图链接", "") or "").strip()
        has_attachment = bool(first_attachment(fields, "商品图"))
        has_source = has_attachment or source_url.startswith("http") or stored_url.startswith("http")
        managed = self.storage.is_managed_url(stored_url)
        raw_status = str(field_value(fields, FACE_STATUS_FIELD, "") or "").strip()
        checked_url = str(field_value(fields, FACE_IMAGE_FIELD, "") or "").strip()

        if raw_status == FACE_REMOVED:
            safety_status = FACE_REMOVED
        elif not has_source:
            safety_status = "缺少商品图"
        elif not managed:
            safety_status = FACE_EXTERNAL
        elif checked_url != stored_url or raw_status not in {
            FACE_CLEAR,
            FACE_PRESENT,
            FACE_UNCERTAIN,
            FACE_RUNNING,
            FACE_FAILED,
        }:
            safety_status = FACE_PENDING
        else:
            safety_status = raw_status

        face_passed = bool(managed and checked_url == stored_url and safety_status == FACE_CLEAR)
        product_id = product_id_from_fields(fields)
        prompt = str(field_value(fields, "最终视频生成提示词", "") or "").strip()
        sync_state = str(field_value(fields, "兼职同步状态", "") or "").strip()
        target_record_id = str(field_value(fields, "兼职同步记录ID", "") or "").strip()
        if sync_state == "已同步":
            downstream_lock_reason = "已同步到兼职表，禁止回退修改图片、人脸检测或提示词"
        elif sync_state == "同步冲突":
            downstream_lock_reason = "兼职任务已接单或完成，现有任务受保护，禁止修改上游内容"
        elif target_record_id:
            downstream_lock_reason = "已关联兼职任务，禁止回退修改图片或人脸检测"
        else:
            downstream_lock_reason = ""
        face_lock_reason = downstream_lock_reason or (
            "已生成提示词，禁止回退重新检测人脸；如需更换图片，请新建素材记录"
            if prompt
            else ""
        )
        publish_prerequisite_issues: list[str] = []
        if not face_passed:
            publish_prerequisite_issues.append(f"商品图人脸检查未通过（{safety_status}）")
        if not product_id:
            publish_prerequisite_issues.append("缺少商品 ID")
        if not prompt:
            publish_prerequisite_issues.append("缺少最终视频生成提示词")
        publish_prerequisites_ready = not publish_prerequisite_issues

        if sync_state == "已同步":
            next_action = "wait_video"
            blocker = downstream_lock_reason
        elif sync_state == "同步冲突":
            next_action = "review_sync_conflict"
            blocker = downstream_lock_reason
        elif safety_status in {"缺少商品图", FACE_REMOVED}:
            next_action = "add_image"
            blocker = "请先补充商品图"
        elif safety_status == FACE_EXTERNAL:
            next_action = "store_image"
            blocker = "外部链接必须先转存到自有对象存储"
        elif safety_status == FACE_RUNNING:
            next_action = "wait_face_scan"
            blocker = "人脸检测正在后台执行"
        elif safety_status in FLAGGED_FACE_STATES:
            next_action = "remove_image"
            blocker = "商品图包含或疑似包含人脸，已阻断后续处理"
        elif safety_status in {FACE_PENDING, FACE_FAILED}:
            next_action = "detect_face"
            blocker = "请先完成人脸检测"
        elif not product_id:
            next_action = "fix_product_id"
            blocker = "缺少商品 ID，不能同步到兼职表"
        elif not prompt:
            next_action = "generate_prompt"
            blocker = "缺少最终视频生成提示词"
        elif sync_state != "已同步":
            next_action = "sync_part_time"
            blocker = ""
        else:
            next_action = "wait_video"
            blocker = ""

        stage_labels = {
            "add_image": "待补商品图",
            "store_image": "待转存对象存储",
            "wait_face_scan": "人脸检测中",
            "remove_image": "人脸审核拦截",
            "detect_face": "待人脸检测",
            "fix_product_id": "缺少商品 ID",
            "generate_prompt": "待生成提示词",
            "sync_part_time": "待同步兼职表",
            "review_sync_conflict": "冲突保护锁定",
            "wait_video": "已同步锁定" if sync_state == "已同步" else "等待成片",
        }
        can_detect_face = (
            managed
            and safety_status not in {FACE_RUNNING, FACE_REMOVED}
            and not face_lock_reason
        )
        can_generate_prompt = face_passed and not downstream_lock_reason
        can_sync_part_time = publish_prerequisites_ready and sync_state not in LOCKED_SYNC_STATES

        return {
            "商品ID": product_id,
            "人脸检测状态": safety_status,
            "人脸检测说明": str(field_value(fields, FACE_DETAIL_FIELD, "") or "").strip(),
            "人脸检测时间": field_value(fields, FACE_TIME_FIELD, ""),
            "人脸检测图片链接": checked_url,
            "人脸检测模型": str(field_value(fields, FACE_MODEL_FIELD, "") or "").strip(),
            "可执行图片转存": has_source and not managed and not downstream_lock_reason,
            "人脸检测通过": face_passed,
            "人脸可检测": managed and safety_status not in {FACE_RUNNING, FACE_REMOVED},
            "可执行人脸检测": can_detect_face,
            "人脸需检测": managed and safety_status in {FACE_PENDING, FACE_FAILED},
            "可删除含人脸记录": safety_status in FLAGGED_FACE_STATES and checked_url == stored_url,
            "可生成提示词": face_passed,
            "可执行生成提示词": can_generate_prompt,
            "发布前置条件通过": publish_prerequisites_ready,
            "发布前置条件问题": publish_prerequisite_issues,
            "可同步兼职": publish_prerequisites_ready,
            "可执行同步兼职": can_sync_part_time,
            "素材下一步": next_action,
            "素材阻塞原因": blocker,
            "流程阶段": stage_labels.get(next_action, "待处理"),
            "流程已锁定": bool(face_lock_reason),
            "流程锁定原因": face_lock_reason,
        }

    def require_upstream_mutable(self, fields: dict[str, Any]) -> None:
        state = self.describe(fields)
        reason = str(state.get("流程锁定原因") or "").strip()
        if reason and (
            str(field_value(fields, "兼职同步状态", "") or "").strip() in LOCKED_SYNC_STATES
            or str(field_value(fields, "兼职同步记录ID", "") or "").strip()
        ):
            raise RuntimeError(reason)

    def require_face_detection_ready(self, fields: dict[str, Any]) -> str:
        state = self.describe(fields)
        if not state["可执行人脸检测"]:
            reason = str(state.get("流程锁定原因") or "").strip()
            if reason:
                raise RuntimeError(reason)
        return self.require_managed_image(fields)

    def require_prompt_generation_ready(self, fields: dict[str, Any]) -> str:
        state = self.describe(fields)
        if not state["可执行生成提示词"]:
            reason = str(state.get("流程锁定原因") or "").strip()
            if reason:
                raise RuntimeError(reason)
        return self.require_safe_image(fields)

    def require_managed_image(self, fields: dict[str, Any]) -> str:
        stored_url = str(field_value(fields, "商品图存储链接", "") or "").strip()
        if not self.storage.is_managed_url(stored_url):
            raise RuntimeError("商品图尚未转存到自有对象存储，请先执行步骤 1“转存对象存储”")
        return stored_url

    def require_safe_image(self, fields: dict[str, Any]) -> str:
        stored_url = self.require_managed_image(fields)
        state = self.describe(fields)
        if not state["人脸检测通过"]:
            status = state["人脸检测状态"]
            if status in FLAGGED_FACE_STATES:
                raise RuntimeError(f"商品图状态为“{status}”，已阻断提示词生成和下游同步")
            raise RuntimeError("商品图尚未通过人脸检测，请先执行步骤 2“检测人脸”")
        return stored_url

    def require_part_time_ready(self, fields: dict[str, Any]) -> str:
        stored_url = self.require_safe_image(fields)
        state = self.describe(fields)
        if not state["商品ID"]:
            raise RuntimeError("缺少商品 ID，已阻断兼职同步")
        if not str(field_value(fields, "最终视频生成提示词", "") or "").strip():
            raise RuntimeError("缺少最终视频生成提示词，请先生成提示词")
        return stored_url

    def download_managed_image(self, fields: dict[str, Any]) -> tuple[bytes, str, str]:
        stored_url = self.require_managed_image(fields)
        data, mime = self.download_url(stored_url)
        max_bytes = max(1, int(getattr(self.settings, "face_image_max_bytes", 20 * 1024 * 1024)))
        if not data:
            raise RuntimeError("对象存储中的商品图为空")
        if len(data) > max_bytes:
            raise RuntimeError(f"商品图超过人脸检测大小限制：{max_bytes // 1024 // 1024}MB")
        return data, _image_mime(data, mime), stored_url

    def detect_face(
        self,
        record_id: str,
        *,
        force: bool = False,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.doubao_table_id, record_id)
        fields = record.get("fields", {})
        state = self.describe(fields)
        if not force and state["人脸检测状态"] in {FACE_CLEAR, FACE_PRESENT, FACE_UNCERTAIN}:
            return {
                "record_id": record_id,
                "status": state["人脸检测状态"],
                "skipped": True,
            }

        try:
            stored_url = self.require_managed_image(fields)
        except Exception as exc:
            self.feishu.update_record(
                self.settings.doubao_table_id,
                record_id,
                {
                    FACE_STATUS_FIELD: FACE_EXTERNAL,
                    FACE_DETAIL_FIELD: str(exc),
                    FACE_TIME_FIELD: now_ms(),
                },
            )
            raise

        self.require_face_detection_ready(fields)
        model = str(getattr(self.settings, "gemini_model", "") or "")
        self.feishu.update_record(
            self.settings.doubao_table_id,
            record_id,
            {
                FACE_STATUS_FIELD: FACE_RUNNING,
                FACE_DETAIL_FIELD: "",
                FACE_IMAGE_FIELD: url_cell(stored_url),
                FACE_MODEL_FIELD: model,
            },
        )
        add_log("info", f"开始检测豆包商品图人脸: {record_id}", "workflow", job_id)
        try:
            data, mime, stored_url = self.download_managed_image(fields)
            result = self.gemini.detect_human_faces(image_bytes=data, image_mime=mime)
            status = {
                "clear": FACE_CLEAR,
                "face": FACE_PRESENT,
                "uncertain": FACE_UNCERTAIN,
            }.get(str(result.get("status") or "").strip().lower())
            if not status:
                raise RuntimeError("人脸检测返回了不支持的状态")
            reason = str(result.get("reason") or "").strip()[:500]
            self.feishu.update_record(
                self.settings.doubao_table_id,
                record_id,
                {
                    FACE_STATUS_FIELD: status,
                    FACE_DETAIL_FIELD: reason,
                    FACE_TIME_FIELD: now_ms(),
                    FACE_IMAGE_FIELD: url_cell(stored_url),
                    FACE_MODEL_FIELD: model,
                },
            )
            add_log(
                "success" if status == FACE_CLEAR else "error",
                f"豆包商品图人脸检测完成: {record_id} / {status}",
                "workflow",
                job_id,
            )
            return {
                "record_id": record_id,
                "status": status,
                "face_count": int(result.get("face_count") or 0),
                "confidence": float(result.get("confidence") or 0),
                "reason": reason,
            }
        except Exception as exc:
            self.feishu.update_record(
                self.settings.doubao_table_id,
                record_id,
                {
                    FACE_STATUS_FIELD: FACE_FAILED,
                    FACE_DETAIL_FIELD: str(exc)[:500],
                    FACE_TIME_FIELD: now_ms(),
                    FACE_IMAGE_FIELD: url_cell(stored_url),
                    FACE_MODEL_FIELD: model,
                },
            )
            raise

    def delete_flagged_record(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.doubao_table_id, record_id)
        fields = record.get("fields", {})
        state = self.describe(fields)
        if not state["可删除含人脸记录"]:
            raise RuntimeError("只有当前商品图被标记为“含人脸”或“疑似人脸”时才允许删除记录")
        if str(field_value(fields, "兼职同步状态", "") or "").strip() == "已同步":
            raise RuntimeError("该素材已同步到兼职表，为避免留下仍可接单的违规任务，暂不允许直接删除")

        checked_url = state["人脸检测图片链接"]
        self.feishu.delete_record(self.settings.doubao_table_id, record_id)
        add_log("success", f"已删除含人脸豆包素材记录: {record_id}", "workflow", job_id)
        return {
            "record_id": record_id,
            "status": "已删除",
            "deleted": True,
            "removed_url": checked_url,
        }

    def source_image(self, fields: dict[str, Any]) -> tuple[bytes, str, str]:
        stored_url = str(field_value(fields, "商品图存储链接", "") or "").strip()
        if stored_url.startswith("http") and not self.storage.is_managed_url(stored_url):
            data, mime = self.download_url(stored_url)
            return data, _image_mime(data, mime), stored_url.rsplit("/", 1)[-1] or "doubao-external-image"

        attachment = first_attachment(fields, "商品图")
        if attachment:
            file_token = attachment.get("file_token")
            if not file_token:
                raise RuntimeError("商品图附件缺少 file_token，无法下载")
            with TemporaryDirectory() as tmpdir:
                filename = str(attachment.get("name") or "doubao-product.png")
                path, mime = self.feishu.download_media(str(file_token), Path(tmpdir) / filename)
                data = path.read_bytes()
                return data, _image_mime(data, mime), path.name

        image_url = str(field_value(fields, "商品图链接", "") or "").strip()
        if image_url.startswith("http"):
            data, mime = self.download_url(image_url)
            return data, _image_mime(data, mime), image_url.rsplit("/", 1)[-1] or "doubao-product-url"

        raise RuntimeError("缺少商品图：请上传商品图附件，或填写商品图链接")

    def store_image(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.doubao_table_id, record_id)
        fields = record.get("fields", {})
        product_name = str(field_value(fields, "商品名称", record_id) or record_id).strip()
        stored_url = str(field_value(fields, "商品图存储链接", "") or "").strip()
        attachment = first_attachment(fields, "商品图")
        source_url = str(field_value(fields, "商品图链接", "") or "").strip()
        has_new_source = bool(attachment or source_url.startswith("http"))
        if self.storage.is_managed_url(stored_url) and not has_new_source:
            add_log("info", f"豆包商品图已在对象存储，跳过重复入库: {product_name}", "workflow", job_id)
            return {"record_id": record_id, "stored_image_url": stored_url, "skipped": True}

        self.require_upstream_mutable(fields)
        add_log("info", f"开始豆包商品图入库: {product_name}", "workflow", job_id)
        data, mime, filename = self.source_image(fields)
        stored = self.storage.upload_bytes(data, filename or "doubao-product-image", mime)
        result_url = str(stored.get("url") or "").strip()
        if not self.storage.is_managed_url(result_url):
            raise RuntimeError("对象存储上传完成但未返回可识别的自有存储链接")
        self.feishu.update_record(
            self.settings.doubao_table_id,
            record_id,
            {
                "商品图存储链接": url_cell(result_url),
                FACE_STATUS_FIELD: FACE_PENDING,
                FACE_DETAIL_FIELD: "",
                FACE_TIME_FIELD: None,
                FACE_IMAGE_FIELD: None,
                FACE_MODEL_FIELD: "",
                "最终视频生成提示词": "",
                "标题": "",
                "文案": "",
                "标签": "",
                "目标国家": "",
                "目标语言": "",
                "Prompt配置版本": "",
                "兼职同步记录ID": "",
                "兼职同步状态": "未同步",
                "兼职同步时间": None,
                "兼职同步错误": "",
            },
        )
        add_log("success", f"豆包商品图入库完成: {result_url}", "workflow", job_id)
        return {"record_id": record_id, "stored_image_url": result_url}
