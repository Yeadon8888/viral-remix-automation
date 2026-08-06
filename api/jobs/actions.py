from concurrent.futures import ThreadPoolExecutor
from typing import Any

from api.logging_bus import add_log


_worker_workflow = None


def _record_ids(payload: dict[str, Any]) -> list[str]:
    values = payload.get("record_ids")
    if not isinstance(values, list):
        return []
    return sorted({str(item).strip() for item in values if str(item).strip()})


def normalize_payload(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if "record_ids" in normalized:
        normalized["record_ids"] = _record_ids(normalized)
    if "row_numbers" in normalized:
        allowed_ids = set(normalized.get("record_ids") or [])
        raw_row_numbers = normalized.get("row_numbers")
        row_numbers: dict[str, int] = {}
        if isinstance(raw_row_numbers, dict):
            for record_id, raw_value in raw_row_numbers.items():
                key = str(record_id).strip()
                try:
                    value = int(raw_value)
                except (TypeError, ValueError):
                    continue
                if key in allowed_ids and value > 0:
                    row_numbers[key] = value
        normalized["row_numbers"] = row_numbers
    if "record_id" in normalized:
        normalized["record_id"] = str(normalized.get("record_id") or "").strip()
    if "country_code" in normalized:
        normalized["country_code"] = str(normalized.get("country_code") or "MY").strip().upper()
    if "step" in normalized:
        normalized["step"] = str(normalized.get("step") or "").strip()
    if "limit" in normalized:
        normalized["limit"] = max(1, int(normalized.get("limit") or 1))
    if "force_update" in normalized:
        normalized["force_update"] = normalized.get("force_update") is True
    if "detect_faces" in normalized:
        normalized["detect_faces"] = normalized.get("detect_faces") is True
    if "repair_faces" in normalized:
        normalized["repair_faces"] = normalized.get("repair_faces") is True and normalized.get("detect_faces") is True
    return normalized


def resource_keys_for(action: str, payload: dict[str, Any]) -> list[str]:
    normalized = normalize_payload(action, payload)
    ids = normalized.get("record_ids") or []
    record_id = normalized.get("record_id")
    if record_id:
        ids = [record_id]
    if action.startswith("doubao."):
        return [f"doubao:{item}" for item in ids] or ["doubao:global"]
    if action.startswith("material."):
        return [f"material:{item}" for item in ids] or ["material:global"]
    if action.startswith("task."):
        return [f"task:{item}" for item in ids] or ["task:global"]
    if action.startswith("publish."):
        return ["publish:global"]
    return [f"action:{action}"]


def _workflow():
    global _worker_workflow
    if _worker_workflow is None:
        from api.config import settings
        from api.persistence import get_persistence
        from api.workflow import ViralRemixWorkflow

        _worker_workflow = ViralRemixWorkflow(settings, ledger=get_persistence(settings))
    return _worker_workflow


def _batch(
    records: list[str],
    handler,
    *,
    max_workers: int = 1,
    job_id: str | None = None,
    label: str = "",
    row_numbers: dict[str, int] | None = None,
    resume_paid_checkpoints: bool = False,
) -> dict[str, Any]:
    total = len(records)
    workers = min(total, max(1, int(max_workers))) if total else 1
    row_numbers = row_numbers or {}
    checkpointed: dict[str, dict[str, Any]] = {}
    if job_id:
        from api.jobs import job_item_results

        checkpointed = job_item_results(job_id)
    if label:
        add_log("info", f"{label}批量任务开始: {total} 条 / 并发 {workers}", "batch", job_id)

    def run(item: tuple[int, str]) -> dict[str, Any]:
        index, record_id = item
        row_number = row_numbers.get(record_id)
        location = f"豆包素材表第 {row_number} 行（{record_id}）" if row_number else record_id

        def with_row_context(result: dict[str, Any]) -> dict[str, Any]:
            output = dict(result)
            if row_number:
                output.setdefault("row_number", row_number)
                output.setdefault("source_table", "豆包素材表")
            return output

        has_resumable_paid_task = resume_paid_checkpoints and checkpointed.get(record_id, {}).get("task_id")
        if record_id in checkpointed and not has_resumable_paid_task:
            if label:
                add_log("info", f"[{label} {index + 1}/{total}] 从检查点恢复: {location}", "batch", job_id)
            return with_row_context(checkpointed[record_id])
        if job_id:
            from api.jobs import job_cancel_requested, save_job_item_result

            if job_cancel_requested(job_id):
                return with_row_context({
                    "record_id": record_id,
                    "status": "cancelled",
                    "message": "用户已停止任务，当前记录未处理",
                })
        if label:
            add_log("info", f"[{label} {index + 1}/{total}] 开始: {location}", "batch", job_id)
        try:
            result = with_row_context(handler(record_id))
        except Exception as exc:
            if label:
                add_log("error", f"[{label} {index + 1}/{total}] 失败: {location} / {exc}", "batch", job_id)
            result = with_row_context({"record_id": record_id, "error": str(exc)})
        if job_id:
            try:
                save_job_item_result(job_id, record_id, result)
            except Exception as exc:
                add_log("warning", f"任务检查点写入失败: {record_id} / {exc}", "batch", job_id)
        return result

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="batch-item") as pool:
        results = list(pool.map(run, enumerate(records)))

    cancelled = sum(1 for item in results if item.get("status") == "cancelled")
    result = {
        "selected": len(results),
        "processed": len(results) - cancelled,
        "cancelled": cancelled,
        "failed": sum(
            1
            for item in results
            if item.get("error") or item.get("status") in {"failed", "conflict"}
        ),
        "results": results,
    }
    if label:
        if cancelled:
            add_log(
                "warning",
                f"{label}批量任务已停止: 已处理 {result['processed']} 条 / 未处理 {cancelled} 条",
                "batch",
                job_id,
            )
        else:
            add_log(
                "success" if result["failed"] == 0 else "error",
                f"{label}批量任务完成: {result['processed']} 条 / 失败 {result['failed']} 条",
                "batch",
                job_id,
            )
    return result


def _concurrency(workflow, setting_name: str, default: int) -> int:
    settings = getattr(workflow, "settings", None)
    return max(1, int(getattr(settings, setting_name, default)))


def execute_action(
    action: str,
    payload: dict[str, Any],
    job_id: str,
    operator: str,
    workflow=None,
) -> Any:
    data = normalize_payload(action, payload)
    work = workflow or _workflow()
    record_id = data.get("record_id", "")
    record_ids = data.get("record_ids") or []
    country_code = data.get("country_code", "MY")
    row_numbers = data.get("row_numbers") if isinstance(data.get("row_numbers"), dict) else {}

    if action == "doubao.generate_white_background":
        from api.jobs import job_cancel_requested, job_item_results, save_job_item_result

        checkpoint = job_item_results(job_id).get(record_id, {})
        return work.generate_doubao_white_background(
            record_id,
            job_id=job_id,
            checkpoint=checkpoint,
            save_checkpoint=lambda progress: save_job_item_result(job_id, record_id, progress),
            should_stop=lambda: job_cancel_requested(job_id),
        )

    single_actions = {
        "doubao.detect_face": lambda: work.detect_doubao_face(record_id, job_id=job_id),
        "doubao.generate_prompt": lambda: work.generate_doubao_prompt(record_id, country_code, job_id=job_id),
        "doubao.delete_face_record": lambda: work.delete_doubao_face_record(record_id, job_id=job_id),
        "doubao.remove_face_image": lambda: work.delete_doubao_face_record(record_id, job_id=job_id),
        "doubao.sync_part_time": lambda: work.sync_doubao_to_part_time(record_id, country_code, job_id=job_id),
        "doubao.store_image": lambda: work.store_doubao_image(record_id, job_id=job_id),
        "material.prepare": lambda: work.prepare_material(record_id, job_id=job_id),
        "material.analyze_video": lambda: work.analyze_viral_video(record_id, job_id=job_id),
        "material.store_image": lambda: work.store_product_image(record_id, job_id=job_id),
        "material.generate_images": lambda: work.generate_product_images(record_id, job_id=job_id),
        "material.create_tasks": lambda: work.create_video_tasks(record_id, job_id=job_id),
        "material.retry_failed_step": lambda: work.retry_failed_material_step(record_id, job_id=job_id),
        "task.generate_video": lambda: work.generate_task_video(record_id, job_id=job_id),
        "task.sync_reference_image": lambda: work.sync_task_reference_image(record_id, job_id=job_id),
        "task.store_manual_video": lambda: work.store_manual_task_video(record_id, job_id=job_id),
    }
    handler = single_actions.get(action)
    if handler:
        return handler()

    if action == "doubao.batch_store_images":
        ids = record_ids
        if not ids:
            limit = data.get("limit", 50)
            rows = work.list_doubao_items(limit=200)
            ids = [
                row["record_id"]
                for row in rows
                if row.get("record_id")
                and row.get("商品图可转存")
            ][:limit]
        return _batch(
            ids,
            lambda rid: work.store_doubao_image(rid, job_id=job_id),
            max_workers=_concurrency(work, "batch_image_concurrency", 4),
            job_id=job_id,
            label="图片转存",
            row_numbers=row_numbers,
        )

    if action == "doubao.batch_generate_prompts":
        return _batch(
            record_ids,
            lambda rid: work.generate_doubao_prompt(rid, country_code, job_id=job_id),
            max_workers=_concurrency(work, "batch_prompt_concurrency", 2),
            job_id=job_id,
            label="提示词生成",
            row_numbers=row_numbers,
        )

    if action == "doubao.batch_detect_faces":
        return _batch(
            record_ids,
            lambda rid: work.detect_doubao_face(rid, job_id=job_id),
            max_workers=_concurrency(work, "batch_face_concurrency", 2),
            job_id=job_id,
            label="人脸检测",
            row_numbers=row_numbers,
        )

    if action in {"doubao.batch_delete_face_records", "doubao.batch_remove_face_images"}:
        return _batch(
            record_ids,
            lambda rid: work.delete_doubao_face_record(rid, job_id=job_id),
            max_workers=_concurrency(work, "batch_image_concurrency", 4),
            job_id=job_id,
            label="含人脸记录删除",
            row_numbers=row_numbers,
        )

    if action == "doubao.batch_sync_part_time":
        target_records = work.feishu.list_records(work.settings.part_time_table_id)
        result = _batch(
            record_ids,
            lambda rid: work.sync_doubao_to_part_time(
                rid,
                country_code,
                job_id=job_id,
                target_records=target_records,
                force_update=bool(data.get("force_update")),
            ),
            max_workers=_concurrency(work, "batch_sync_concurrency", 2),
            job_id=job_id,
            label="兼职表同步",
            row_numbers=row_numbers,
        )
        for status in ("created", "updated", "conflict", "skipped"):
            result[status] = sum(1 for item in result["results"] if item.get("status") == status)
        return result

    if action == "doubao.batch_workflow":
        from api.jobs import job_cancel_requested, job_item_results, save_job_item_result

        target_records = work.feishu.list_records(work.settings.part_time_table_id)
        repair_checkpoints = job_item_results(job_id)
        face_options = {}
        if "detect_faces" in data:
            face_options = {
                "detect_faces": data.get("detect_faces") is not False,
                "repair_faces": data.get("repair_faces") is True and data.get("detect_faces") is not False,
            }
        result = _batch(
            record_ids,
            lambda rid: work.process_doubao_workflow_record(
                rid,
                country_code,
                job_id=job_id,
                target_records=target_records,
                force_update=bool(data.get("force_update")),
                **face_options,
                **(
                    {
                        "repair_checkpoint": repair_checkpoints.get(rid, {}),
                        "save_repair_checkpoint": lambda progress, record_id=rid: save_job_item_result(job_id, record_id, progress),
                        "should_stop": lambda: job_cancel_requested(job_id),
                    }
                    if face_options.get("repair_faces")
                    else {}
                ),
            ),
            max_workers=_concurrency(work, "batch_sync_concurrency", 2),
            job_id=job_id,
            label="按流程处理",
            row_numbers=row_numbers,
            resume_paid_checkpoints=face_options.get("repair_faces") is True,
        )
        for status in ("created", "updated", "conflict", "skipped", "blocked"):
            result[status] = sum(1 for item in result["results"] if item.get("status") == status)
        return result

    if action == "material.batch_step":
        step = data.get("step", "")
        handlers = {
            "analyze-video": work.analyze_viral_video,
            "store-image": work.store_product_image,
            "generate-images": work.generate_product_images,
            "create-tasks": work.create_video_tasks,
        }
        selected = handlers.get(step)
        if not selected:
            raise ValueError(f"不支持的素材批量步骤：{step}")
        return _batch(record_ids, lambda rid: selected(rid, job_id=job_id))

    if action == "material.batch_prepare":
        ids = record_ids
        if not ids:
            limit = data.get("limit", 5)
            rows = work.list_materials(limit=500)
            ids = [
                row["record_id"]
                for row in rows
                if row.get("record_id") and row.get("爆款视频链接") and row.get("自动化状态") != "已创建任务"
            ][:limit]
        return _batch(ids, lambda rid: work.prepare_material(rid, job_id=job_id))

    if action == "task.batch_generate_video":
        ids = record_ids
        if not ids:
            limit = data.get("limit", 3)
            rows = work.list_tasks(limit=200)
            ids = [
                row["record_id"]
                for row in rows
                if row.get("record_id") and row.get("生成状态") in ("待生成视频", "内容已生成", "")
            ][:limit]
        return _batch(ids, lambda rid: work.generate_task_video(rid, job_id=job_id))

    if action == "task.batch_sync_reference_images":
        ids = record_ids
        if not ids:
            limit = data.get("limit", 100)
            rows = work.list_tasks(limit=max(limit, 200))
            ids = [
                row["record_id"]
                for row in rows
                if row.get("record_id")
                and str(row.get("首帧/参考图链接") or "").startswith("http")
                and not int(row.get("首帧附件数") or 0)
            ][:limit]
        return _batch(ids, lambda rid: work.sync_task_reference_image(rid, job_id=job_id))

    if action == "task.batch_store_manual_videos":
        ids = record_ids
        if not ids:
            limit = data.get("limit", 100)
            rows = work.list_tasks(limit=max(limit, 200))
            ids = [
                row["record_id"]
                for row in rows
                if row.get("record_id")
                and (int(row.get("生成视频附件数") or 0) or str(row.get("生成视频链接") or "").startswith("http"))
            ][:limit]
        return _batch(ids, lambda rid: work.store_manual_task_video(rid, job_id=job_id))

    if action == "publish.sync":
        return work.sync_publish_items(record_ids or None, data.get("limit", 100), job_id=job_id)

    if action == "publish.sync_doubao":
        result = work.sync_doubao_publish_items(record_ids, data.get("limit", 200), job_id=job_id)
        for item in result.get("results", []):
            row_number = row_numbers.get(str(item.get("record_id") or ""))
            if row_number:
                item.setdefault("row_number", row_number)
                item.setdefault("source_table", "豆包素材表")
        return result

    if action == "publish.sync_part_time":
        result = work.sync_part_time_publish_items(record_ids, data.get("limit", 200), job_id=job_id)
        for item in result.get("results", []):
            row_number = row_numbers.get(str(item.get("record_id") or ""))
            if row_number:
                item.setdefault("row_number", row_number)
                item.setdefault("source_table", "兼职表")
        return result

    raise ValueError(f"不支持的任务动作：{action}")
