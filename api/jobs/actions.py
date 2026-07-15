from typing import Any


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
    if "record_id" in normalized:
        normalized["record_id"] = str(normalized.get("record_id") or "").strip()
    if "country_code" in normalized:
        normalized["country_code"] = str(normalized.get("country_code") or "MY").strip().upper()
    if "step" in normalized:
        normalized["step"] = str(normalized.get("step") or "").strip()
    if "limit" in normalized:
        normalized["limit"] = max(1, int(normalized.get("limit") or 1))
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


def _batch(records: list[str], handler) -> dict[str, Any]:
    results = []
    for record_id in records:
        try:
            results.append(handler(record_id))
        except Exception as exc:
            results.append({"record_id": record_id, "error": str(exc)})
    return {
        "processed": len(results),
        "failed": sum(1 for item in results if item.get("error") or item.get("status") == "failed"),
        "results": results,
    }


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

    single_actions = {
        "doubao.generate_prompt": lambda: work.generate_doubao_prompt(record_id, country_code, job_id=job_id),
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
                and (row.get("商品图数量") or str(row.get("商品图链接") or "").startswith("http"))
                and not str(row.get("商品图存储链接") or "").startswith("http")
            ][:limit]
        return _batch(ids, lambda rid: work.store_doubao_image(rid, job_id=job_id))

    if action == "doubao.batch_generate_prompts":
        return _batch(
            record_ids,
            lambda rid: work.generate_doubao_prompt(rid, country_code, job_id=job_id),
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
            ),
        )
        for status in ("created", "updated", "conflict"):
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
        return work.sync_doubao_publish_items(record_ids, data.get("limit", 200), job_id=job_id)

    raise ValueError(f"不支持的任务动作：{action}")
