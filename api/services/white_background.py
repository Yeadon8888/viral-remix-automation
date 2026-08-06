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

    def generate(
        self,
        record_id: str,
        job_id: str | None = None,
        *,
        checkpoint: dict[str, Any] | None = None,
        save_checkpoint=None,
        should_stop=None,
    ) -> dict[str, Any]:
        progress = dict(checkpoint or {})

        def save(**patch):
            progress.update(patch)
            if save_checkpoint:
                save_checkpoint(dict(progress))

        record = self.feishu.get_record(self.settings.doubao_table_id, record_id)
        fields = record.get("fields", {})
        current_url = str(field_value(fields, "商品图存储链接", "") or "").strip()
        checkpoint_original_url = str(progress.get("original_url") or "").strip()
        committed_url = str(progress.get("committed_url") or "").strip()
        model = str(getattr(self.settings, "tokensfactory_image_model", "nano-banana-2") or "nano-banana-2")
        if committed_url:
            if current_url != committed_url:
                raise RuntimeError("白底图恢复点与当前商品图不一致")
            return self._finish_recheck(
                record_id,
                committed_url,
                str(progress.get("task_id") or "").strip(),
                model,
                job_id,
                save,
            )
        original_url = self.require_eligible(fields)
        if checkpoint_original_url and checkpoint_original_url != current_url:
            raise RuntimeError("商品图已变更，不能恢复旧的白底图任务")
        self._update_status(record_id, "生成中", model=model)
        add_log("info", f"开始生成白底商品图: {record_id}", "workflow", job_id)
        try:
            task_id = str(progress.get("task_id") or "").strip()
            if not task_id:
                task_id = self.generator.create_task(original_url, WHITE_BACKGROUND_PROMPT)
                save(stage="生成中", task_id=task_id, original_url=original_url)
            result_url = str(progress.get("result_url") or "").strip()
            if not result_url:
                result_url = self.generator.wait_for_result(task_id, should_stop=should_stop)
                save(stage="生成完成", result_url=result_url)
            if not result_url.startswith(("http://", "https://")):
                raise RuntimeError("白底图服务未返回可用的图片链接")
            max_bytes = max(1, int(getattr(self.settings, "face_image_max_bytes", 20 * 1024 * 1024)))
            managed_url = str(progress.get("managed_url") or "").strip()
            if not managed_url:
                downloader = getattr(self.generator, "download_image", None)
                data, declared_mime = (
                    downloader(result_url, max_bytes)
                    if downloader
                    else self.download_url(result_url)
                )
                if not data:
                    raise RuntimeError("白底图下载结果为空")
                if len(data) > max_bytes:
                    raise RuntimeError(f"白底图超过大小限制：{max_bytes // 1024 // 1024}MB")
                # Generated provider output must have a recognized magic number;
                # a claimed image/* Content-Type alone is not sufficient.
                mime = _image_mime(data, "")
                self._update_status(record_id, "上传中", model=model)
                suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime, Path(result_url).suffix or ".img")
                uploaded = self.storage.upload_bytes(data, f"white-background{suffix}", mime)
                managed_url = str(uploaded.get("url") or "").strip()
                if not self.storage.is_managed_url(managed_url):
                    raise RuntimeError("R2 上传成功但未返回可识别的自有存储链接")
                save(stage="上传完成", managed_url=managed_url)

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
            save(stage="待复检", committed_url=managed_url)
            return self._finish_recheck(record_id, managed_url, task_id, model, job_id, save)
        except Exception as exc:
            self._update_status(record_id, "失败", model=model, error=str(exc))
            raise

    def _finish_recheck(self, record_id, managed_url, task_id, model, job_id, save):
        face_result = self.assets.detect_face(record_id, force=True, job_id=job_id)
        if face_result.get("status") != "无人脸":
            message = f"白底图复检仍为{face_result.get('status') or '未通过'}，已继续阻断"
            self._update_status(record_id, "待处理", model=model, error=message)
            save(stage="待处理", status="blocked", error=message)
            return {"record_id": record_id, "status": "blocked", "error": message}
        self._update_status(record_id, "完成", model=model)
        save(stage="完成", status="done")
        add_log("success", f"白底商品图替换并复检完成: {record_id}", "workflow", job_id)
        return {
            "record_id": record_id,
            "status": "完成",
            "stored_image_url": managed_url,
            "external_task_id": task_id,
        }

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
