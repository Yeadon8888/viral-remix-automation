from pathlib import Path
from typing import Any, Callable

from ..feishu import field_value
from ..logging_bus import add_log
from .doubao_assets import (
    FACE_DETAIL_FIELD,
    FACE_IMAGE_FIELD,
    FACE_MODEL_FIELD,
    FACE_PENDING,
    FACE_STATUS_FIELD,
    FACE_TIME_FIELD,
    FLAGGED_FACE_STATES,
    _image_mime,
    now_ms,
    url_cell,
)


WHITE_STATUS_FIELD = "白底图处理状态"
WHITE_TIME_FIELD = "白底图处理时间"
WHITE_MODEL_FIELD = "白底图处理模型"
WHITE_ERROR_FIELD = "白底图处理错误"

WHITE_BACKGROUND_PROMPT = """
仅提取并保留参考图中的商品主体，放置在纯白背景上。
严格保持商品的颜色、材质、形状、结构、比例、Logo、包装和配件与参考图一致。
商品必须完整、居中，边缘自然清晰，光影真实，适合作为后续图生视频的商品参考图。
""".strip()


class WhiteBackgroundService:
    def __init__(
        self,
        settings,
        feishu,
        storage,
        assets,
        generator,
        download_url: Callable[[str], tuple[bytes, str]],
    ):
        self.settings = settings
        self.feishu = feishu
        self.storage = storage
        self.assets = assets
        self.generator = generator
        self.download_url = download_url

    def describe_eligibility(self, fields: dict[str, Any]) -> dict[str, Any]:
        asset_state = self.assets.describe(fields)
        stored_url = str(field_value(fields, "商品图存储链接", "") or "").strip()
        checked_url = str(field_value(fields, FACE_IMAGE_FIELD, "") or "").strip()
        face_status = str(asset_state.get("人脸检测状态") or "").strip()
        lock_reason = str(asset_state.get("流程锁定原因") or "").strip()
        reason = ""
        if face_status not in FLAGGED_FACE_STATES:
            reason = "只有当前商品图为“含人脸”或“疑似人脸”时才可生成白底图"
        elif not self.storage.is_managed_url(stored_url):
            reason = "商品图尚未转存到自有对象存储"
        elif checked_url != stored_url:
            reason = "人脸检测结果已过期，请先重新检测当前商品图"
        elif lock_reason:
            reason = lock_reason
        return {
            "可生成白底图": not reason,
            "白底图阻断原因": reason,
            WHITE_STATUS_FIELD: str(field_value(fields, WHITE_STATUS_FIELD, "") or "").strip(),
            WHITE_TIME_FIELD: field_value(fields, WHITE_TIME_FIELD, ""),
            WHITE_MODEL_FIELD: str(field_value(fields, WHITE_MODEL_FIELD, "") or "").strip(),
            WHITE_ERROR_FIELD: str(field_value(fields, WHITE_ERROR_FIELD, "") or "").strip(),
        }

    def require_eligible(self, fields: dict[str, Any]) -> str:
        state = self.describe_eligibility(fields)
        if not state["可生成白底图"]:
            raise RuntimeError(state["白底图阻断原因"])
        return str(field_value(fields, "商品图存储链接", "") or "").strip()

    def generate(self, record_id: str, job_id: str | None = None) -> dict[str, Any]:
        record = self.feishu.get_record(self.settings.doubao_table_id, record_id)
        fields = record.get("fields", {})
        original_url = self.require_eligible(fields)
        model = str(getattr(self.settings, "tokensfactory_image_model", "nano-banana-2") or "nano-banana-2")
        self._update_status(record_id, "生成中", model=model)
        add_log("info", f"开始生成白底商品图: {record_id}", "workflow", job_id)
        try:
            generated = self.generator.generate_white_background(
                original_url,
                WHITE_BACKGROUND_PROMPT,
            )
            result_url = str(generated.get("image_url") or "").strip()
            if not result_url.startswith(("http://", "https://")):
                raise RuntimeError("白底图服务未返回可用的图片链接")
            data, declared_mime = self.download_url(result_url)
            max_bytes = max(1, int(getattr(self.settings, "face_image_max_bytes", 20 * 1024 * 1024)))
            if not data:
                raise RuntimeError("白底图下载结果为空")
            if len(data) > max_bytes:
                raise RuntimeError(f"白底图超过大小限制：{max_bytes // 1024 // 1024}MB")
            mime = _image_mime(data, declared_mime)
            self._update_status(record_id, "上传中", model=model)
            suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime, Path(result_url).suffix or ".img")
            uploaded = self.storage.upload_bytes(data, f"white-background{suffix}", mime)
            managed_url = str(uploaded.get("url") or "").strip()
            if not self.storage.is_managed_url(managed_url):
                raise RuntimeError("R2 上传成功但未返回可识别的自有存储链接")

            latest = self.feishu.get_record(self.settings.doubao_table_id, record_id).get("fields", {})
            current_url = str(field_value(latest, "商品图存储链接", "") or "").strip()
            if current_url != original_url:
                raise RuntimeError("商品图已变更，白底图结果未覆盖当前记录")
            self.require_eligible(latest)
            self.feishu.update_record(
                self.settings.doubao_table_id,
                record_id,
                {
                    "商品图存储链接": url_cell(managed_url),
                    FACE_STATUS_FIELD: FACE_PENDING,
                    FACE_DETAIL_FIELD: "",
                    FACE_TIME_FIELD: None,
                    FACE_IMAGE_FIELD: None,
                    FACE_MODEL_FIELD: "",
                    WHITE_STATUS_FIELD: "待复检",
                    WHITE_TIME_FIELD: now_ms(),
                    WHITE_MODEL_FIELD: model,
                    WHITE_ERROR_FIELD: "",
                },
            )
            face_result = self.assets.detect_face(record_id, force=True, job_id=job_id)
            if face_result.get("status") != "无人脸":
                message = f"白底图复检仍为{face_result.get('status') or '未通过'}，已继续阻断"
                self._update_status(record_id, "失败", model=model, error=message)
                return {"record_id": record_id, "status": "失败", "error": message}
            self._update_status(record_id, "完成", model=model)
            add_log("success", f"白底商品图替换并复检完成: {record_id}", "workflow", job_id)
            return {
                "record_id": record_id,
                "status": "完成",
                "stored_image_url": managed_url,
                "external_task_id": str(generated.get("task_id") or ""),
            }
        except Exception as exc:
            self._update_status(record_id, "失败", model=model, error=str(exc))
            raise

    def _update_status(
        self,
        record_id: str,
        status: str,
        *,
        model: str,
        error: str = "",
    ) -> None:
        self.feishu.update_record(
            self.settings.doubao_table_id,
            record_id,
            {
                WHITE_STATUS_FIELD: status,
                WHITE_TIME_FIELD: now_ms(),
                WHITE_MODEL_FIELD: model,
                WHITE_ERROR_FIELD: str(error or "")[:500],
            },
        )
