import os
import time
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4

from flask import Flask, g, has_request_context, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import ServiceUnavailable

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

from api.config import settings
from api.countries import get_country, list_countries
from api.auth import Operator, resolve_operator
from api.feishu import FeishuError, field_value
from api.image_gen import STYLE_LABELS
from api.jobs import cancel_job, coordinated_resources, enqueue_job, get_job, job_health, list_jobs
from api.logging_bus import recent_logs
from api.persistence import get_persistence
from api.services.batch_queue_export import BatchQueueExportError
from api.workflow import MATERIAL_PROMPT_FIELD, PromptConflictError, ViralRemixWorkflow


app = Flask(__name__, static_folder="public", static_url_path="")
CORS(app)
workflow = ViralRemixWorkflow(settings, ledger=get_persistence(settings))
cache_lock = Lock()
list_cache = {}


def force_refresh_requested() -> bool:
    return str(request.args.get("force") or "").lower() in ("1", "true", "yes")


def get_workflow() -> ViralRemixWorkflow:
    return workflow


def cached_list(key: str, loader, ttl_seconds: int = 20):
    force = force_refresh_requested()
    now = time.time()
    with cache_lock:
        cached = list_cache.get(key)
        if cached and not force and now - cached["time"] < ttl_seconds:
            return cached["data"]
    data = loader()
    with cache_lock:
        list_cache[key] = {"time": time.time(), "data": data}
    return data


def clear_list_cache() -> None:
    with cache_lock:
        row_index = list_cache.get("doubao-row-index")
        list_cache.clear()
        if row_index:
            list_cache["doubao-row-index"] = row_index


def current_operator() -> Operator:
    if has_request_context() and getattr(g, "operator", None):
        return g.operator
    return Operator("system", "admin")


def enqueue_workflow_action(name: str, action: str, payload: dict) -> str:
    clear_list_cache()
    try:
        return enqueue_job(name, action, payload, operator=current_operator().id, settings=settings)
    except Exception as exc:
        raise ServiceUnavailable(str(exc)) from exc


def audit_write(action: str, resource: str, status: str, detail: str = "") -> None:
    try:
        get_persistence(settings).audit(current_operator().id, action, resource, status, detail)
    except Exception as exc:
        raise ServiceUnavailable(f"审计服务不可用: {exc}") from exc


@app.errorhandler(ServiceUnavailable)
def service_unavailable(exc):
    return jsonify({"success": False, "message": str(exc.description)}), 503


@app.errorhandler(FeishuError)
def feishu_service_error(exc):
    message = str(exc)
    if "data not ready" in message.lower():
        message = "飞书数据暂未准备好，后台已自动重试，请稍后刷新"
    else:
        message = f"飞书服务请求失败：{message}"
    return jsonify({"success": False, "message": message}), 503


def request_limit(default: int = 500, maximum: int = 1000) -> int:
    try:
        value = int(request.args.get("limit", default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def query_flag(name: str) -> bool:
    return str(request.args.get(name) or "").lower() in ("1", "true", "yes")


def query_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def query_days(default: int = 2) -> int:
    try:
        value = int(request.args.get("days", default))
    except (TypeError, ValueError):
        value = default
    return value if value in {0, 1, 2, 7} else default


def compact_material_rows(rows: list[dict]) -> list[dict]:
    heavy_fields = (
        "视频生成Prompt",
        "生成文案",
        "关键词",
        "视频拆解提示词模板",
        "视频拆解实际提示词",
        "商品组图提示词模板",
        "商品组图实际提示词",
    )
    compact_rows = []
    for row in rows:
        item = dict(row)
        item["has_publish_detail"] = bool(
            str(row.get("生成标题") or "").strip()
            or str(row.get("生成文案") or "").strip()
            or str(row.get("关键词") or "").strip()
            or str(row.get("视频生成Prompt") or "").strip()
        )
        item["has_video_template_detail"] = bool(str(row.get("视频拆解提示词模板") or "").strip())
        item["has_video_actual_detail"] = bool(str(row.get("视频拆解实际提示词") or "").strip())
        item["has_image_template_detail"] = bool(str(row.get("商品组图提示词模板") or "").strip())
        item["has_image_actual_detail"] = bool(str(row.get("商品组图实际提示词") or "").strip())
        for field in heavy_fields:
            item.pop(field, None)
        compact_rows.append(item)
    return compact_rows


def publish_readiness_error_message(readiness: dict) -> str:
    return (
        "没有符合条件的视频："
        f"图片或人脸未通过 {int(readiness.get('blocked_face_sources') or 0)} 条，"
        f"缺少商品 ID {int(readiness.get('missing_product_id_sources') or 0)} 条，"
        f"缺少最终提示词 {int(readiness.get('missing_prompt_sources') or 0)} 条，"
        f"没有已完成有效视频 {int(readiness.get('missing_video_sources') or 0)} 条。"
        "以上分类可能重叠。"
    )


def compact_job_for_list(job: dict, failure_limit: int = 20) -> dict:
    compact = {
        key: job.get(key)
        for key in (
            "id",
            "name",
            "action",
            "operator",
            "status",
            "cancel_requested",
            "error",
            "created_at",
            "started_at",
            "finished_at",
        )
    }
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    record_ids = payload.get("record_ids") if isinstance(payload.get("record_ids"), list) else []
    target_labels = {
        "doubao.batch_store_images": "豆包素材图片",
        "doubao.batch_detect_faces": "豆包素材人脸检测",
        "doubao.batch_delete_face_records": "含人脸豆包素材",
        "doubao.batch_generate_prompts": "豆包素材提示词",
        "doubao.batch_sync_part_time": "豆包素材兼职同步",
        "doubao.batch_workflow": "豆包素材全流程",
        "publish.sync_doubao": "豆包成片发布同步",
        "publish.sync_part_time": "兼职成片发布同步",
    }
    target_label = str(payload.get("selection_label") or "").strip()[:120]
    if record_ids:
        compact["target"] = {
            "label": target_label or target_labels.get(str(job.get("action") or ""), "批量记录"),
            "record_count": len(record_ids),
        }
        country_code = str(payload.get("country_code") or "").strip()
        if country_code:
            compact["target"]["country_code"] = country_code

    result = job.get("result")
    if not isinstance(result, dict):
        compact["result"] = result
        compact["outcome"] = "failed" if job.get("status") == "failed" else str(job.get("status") or "")
        return compact

    summary = {
        key: value
        for key, value in result.items()
        if key != "results" and not isinstance(value, (dict, list))
    }
    result_items = result.get("results", [])
    failures = [
        {
            key: item.get(key)
            for key in ("record_id", "row_number", "source_table", "error", "status", "stage", "message")
            if item.get(key) not in (None, "")
        }
        for item in result_items
        if isinstance(item, dict)
        and (item.get("error") or item.get("status") == "failed")
    ]
    conflicts = [
        {
            key: item.get(key)
            for key in ("record_id", "row_number", "source_table", "status", "stage", "message")
            if item.get(key) not in (None, "")
        }
        for item in result_items
        if isinstance(item, dict) and item.get("status") == "conflict"
    ]
    blocked = [
        {
            key: item.get(key)
            for key in ("record_id", "row_number", "source_table", "status", "stage", "message")
            if item.get(key) not in (None, "")
        }
        for item in result_items
        if isinstance(item, dict) and item.get("status") == "blocked"
    ]
    if isinstance(result_items, list):
        reported_failed = int(summary.get("failed") or 0)
        unclassified_failed = max(0, reported_failed - len(failures) - len(conflicts))
        summary["failed"] = len(failures) + unclassified_failed
        summary["conflicts"] = len(conflicts)
    if failures:
        summary["failures"] = failures[:max(1, failure_limit)]
        summary["failure_items_total"] = len(failures)
    if conflicts:
        summary["conflict_items"] = conflicts[:max(1, failure_limit)]
        summary["conflict_items_total"] = len(conflicts)
    if blocked:
        summary["blocked_items"] = blocked[:max(1, failure_limit)]
        summary["blocked_items_total"] = len(blocked)
    compact["result"] = summary
    failed = int(summary.get("failed") or 0)
    conflicts_count = int(summary.get("conflicts") or 0)
    processed = int(summary.get("processed") or 0)
    if job.get("status") == "failed" or (failed and processed and failed >= processed):
        compact["outcome"] = "failed"
    elif failed:
        compact["outcome"] = "partial_failed"
    elif conflicts_count:
        compact["outcome"] = "protected"
    else:
        compact["outcome"] = str(job.get("status") or "")
    return compact


def require_admin():
    admin_token = getattr(settings, "admin_token", "")
    users_json = getattr(settings, "admin_users_json", "")
    if not admin_token and not users_json:
        g.operator = Operator("local-admin", "admin")
        return None
    token = request.headers.get("X-Admin-Token") or request.args.get("token")
    operator = resolve_operator(token or "", legacy_admin_token=admin_token, users_json=users_json)
    if not operator:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    if request.method not in {"GET", "HEAD", "OPTIONS"} and operator.role == "viewer":
        return jsonify({"success": False, "message": "Forbidden"}), 403
    g.operator = operator
    return None


@app.before_request
def check_auth():
    if request.path.startswith("/api/"):
        auth_error = require_admin()
        if auth_error:
            return auth_error
    return None


@app.route("/")
def index():
    return send_from_directory("public", "index.html")


@app.route("/healthz")
def healthz():
    try:
        coordination = job_health()
        persistence = get_persistence(settings).health()
    except Exception as exc:
        return jsonify({"status": "unavailable", "message": str(exc)}), 503
    expected_workers = int(coordination.get("expected_workers") or 0)
    alive_workers = int(coordination.get("alive_workers") or 0)
    if expected_workers and alive_workers < expected_workers:
        return jsonify({
            "status": "unavailable",
            "message": f"后台任务 Worker 异常：存活 {alive_workers}/{expected_workers}",
            "coordination": coordination,
            "persistence": persistence,
        }), 503
    return jsonify({"status": "ok", "coordination": coordination, "persistence": persistence})


@app.route("/api/health")
def health():
    try:
        coordination = job_health()
        persistence = get_persistence(settings).health()
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    return jsonify({
        "success": True,
        "missing": settings.missing_required(),
        "coordination": coordination,
        "persistence": persistence,
    })


@app.route("/api/config")
def config_status():
    return jsonify({
        "success": True,
        "missing": settings.missing_required(),
        "source_table_id": settings.source_table_id,
        "task_table_id": settings.task_table_id,
        "prompt_table_id": settings.prompt_table_id,
        "doubao_table_id": settings.doubao_table_id,
        "part_time_table_id": settings.part_time_table_id,
        "publish_table_id": settings.publish_table_id,
        "gemini_model": settings.gemini_model,
        "cpa_model": settings.cpa_video_model,
        "image_styles": settings.image_scene_styles,
        "image_style_labels": STYLE_LABELS,
        "newtoken_timeout_seconds": settings.newtoken_timeout_seconds,
        "storage_mode": "upload_gateway" if settings.has_upload_gateway else "direct_r2",
    })


@app.route("/api/countries")
def countries():
    return jsonify({"success": True, "data": list_countries()})


@app.route("/api/worker/latest")
def worker_latest():
    default_release_base = "https://github.com/Yeadon8888/dola-local-worker-releases/releases/download/v1.2.3"
    version = os.getenv("WORKER_UPDATE_VERSION", "1.2.3").strip() or "1.2.3"
    release_base = os.getenv("WORKER_RELEASE_BASE_URL", default_release_base).strip().rstrip("/")
    mac_url = os.getenv("WORKER_MAC_URL", "").strip()
    windows_url = os.getenv("WORKER_WINDOWS_URL", "").strip()
    if release_base:
        mac_url = mac_url or f"{release_base}/Dola.Worker-mac.zip"
        windows_url = windows_url or f"{release_base}/DolaLocalWorker-windows.zip"
    return jsonify({
        "success": True,
        "data": {
            "version": version,
            "min_supported_version": os.getenv("WORKER_MIN_SUPPORTED_VERSION", "1.2.0").strip() or "1.2.0",
            "published_at": os.getenv("WORKER_UPDATE_PUBLISHED_AT", "2026-06-30T11:22:18Z").strip(),
            "notes": os.getenv(
                "WORKER_UPDATE_NOTES",
                "优化右下角运行状态面板，稳定批量创建环境，并支持云端触发本地 Worker 更新。",
            ).strip(),
            "mac": {
                "url": mac_url,
                "sha256": os.getenv(
                    "WORKER_MAC_SHA256",
                    "c74c8315435dba9b50d2e6b09c8c384cb785892f83c78a2e06f0e89dd6069d9d",
                ).strip().lower(),
            },
            "windows": {
                "url": windows_url,
                "sha256": os.getenv(
                    "WORKER_WINDOWS_SHA256",
                    "6daf61dbd53e2245f57a38087e909ec4b972b5ade7fd22ac38bdc3425c57d39e",
                ).strip().lower(),
            },
        },
    })


@app.route("/api/logs")
def logs():
    return jsonify({"success": True, "data": recent_logs(query_int("limit", 120, 300))})


@app.route("/api/jobs/<job_id>")
def job_status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"success": False, "message": "job not found"}), 404
    return jsonify({"success": True, "data": job})


RETRYABLE_BATCH_ACTIONS = {
    "doubao.batch_store_images",
    "doubao.batch_detect_faces",
    "doubao.batch_generate_prompts",
    "doubao.batch_sync_part_time",
    "doubao.batch_workflow",
    "publish.sync_doubao",
    "publish.sync_part_time",
}


CANCELLABLE_BATCH_ACTIONS = {
    *RETRYABLE_BATCH_ACTIONS,
    "doubao.batch_delete_face_records",
    "material.batch_step",
    "material.batch_prepare",
    "task.batch_generate_video",
    "task.batch_sync_reference_images",
    "task.batch_store_manual_videos",
}


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def cancel_active_job(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"success": False, "message": "job not found"}), 404
    if str(job.get("action") or "") not in CANCELLABLE_BATCH_ACTIONS:
        return jsonify({"success": False, "message": "该任务类型不支持停止"}), 400
    if job.get("status") not in {"queued", "running"}:
        return jsonify({"success": False, "message": "任务已经结束，无需停止"}), 409
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    batch_group_id = str(payload.get("batch_group_id") or "").strip()
    targets = [job]
    if batch_group_id:
        targets.extend(
            candidate
            for candidate in list_jobs(limit=100)
            if str((candidate.get("payload") or {}).get("batch_group_id") or "").strip() == batch_group_id
        )
    active_targets = {
        str(candidate.get("id") or ""): candidate
        for candidate in targets
        if candidate.get("status") in {"queued", "running"}
        and str(candidate.get("action") or "") in CANCELLABLE_BATCH_ACTIONS
        and str(candidate.get("id") or "")
    }
    updated_jobs = [cancel_job(target_id) or candidate for target_id, candidate in active_targets.items()]
    updated = next((item for item in updated_jobs if item.get("id") == job_id), job)
    immediate = updated.get("status") == "cancelled"
    return jsonify({
        "success": True,
        "job_id": job_id,
        "job_ids": list(active_targets),
        "cancelled_job_count": len(active_targets),
        "status": updated.get("status"),
        "cancel_requested": True,
        "message": (
            f"本批次 {len(active_targets)} 个任务已停止"
            if immediate and len(active_targets) > 1
            else "任务已停止"
            if immediate
            else f"已请求停止本批次 {len(active_targets)} 个任务，正在完成当前处理中的记录"
            if len(active_targets) > 1
            else "已请求停止，正在完成当前处理中的记录"
        ),
    })


@app.route("/api/jobs/<job_id>/retry-failures", methods=["POST"])
def retry_job_failures(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"success": False, "message": "job not found"}), 404
    action = str(job.get("action") or "")
    if action not in RETRYABLE_BATCH_ACTIONS:
        return jsonify({"success": False, "message": "该任务类型不支持失败重试"}), 400
    if job.get("status") in {"queued", "running"}:
        return jsonify({"success": False, "message": "任务仍在处理中，请完成后再重试"}), 409

    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    failed_ids = [
        str(item.get("record_id") or "").strip()
        for item in result.get("results", [])
        if isinstance(item, dict)
        and (item.get("error") or item.get("status") == "failed")
        and str(item.get("record_id") or "").strip()
    ]
    if not failed_ids and job.get("status") == "failed":
        payload_ids = (job.get("payload") or {}).get("record_ids")
        failed_ids = payload_ids if isinstance(payload_ids, list) else []
    failed_ids = list(dict.fromkeys(failed_ids))[:200]
    if not failed_ids:
        return jsonify({"success": False, "message": "该任务没有可重试的失败记录"}), 400

    payload = dict(job.get("payload") or {})
    payload["record_ids"] = failed_ids
    payload["retry_of"] = job_id
    label = str(payload.get("selection_label") or "").strip()
    payload["selection_label"] = f"{label} · 失败重试" if label else "失败记录重试"
    retry_id = enqueue_workflow_action(
        f"{str(job.get('name') or action)}-retry",
        action,
        payload,
    )
    return jsonify({"success": True, "job_id": retry_id, "retry_count": len(failed_ids)})


@app.route("/api/jobs")
def job_list():
    limit = query_int("limit", 30, 100)
    return jsonify({
        "success": True,
        "data": [compact_job_for_list(job) for job in list_jobs(limit=limit)],
    })


@app.route("/api/materials")
def list_materials():
    workflow = get_workflow()
    limit = request_limit()
    data = cached_list(f"materials:{limit}", lambda: workflow.list_materials(limit=limit), ttl_seconds=120)
    if not query_flag("details"):
        data = compact_material_rows(data)
    return jsonify({"success": True, "data": data})


@app.route("/api/materials/<record_id>/details")
def material_details(record_id):
    field = str(request.args.get("field") or "").strip()
    workflow = get_workflow()
    record = workflow.feishu.get_record(settings.source_table_id, record_id)
    fields = record.get("fields", {})
    title = str(field_value(fields, "生成标题", "") or "").strip()
    caption = str(field_value(fields, "生成文案", "") or "").strip()
    keywords = str(field_value(fields, "关键词", "") or "").strip()
    values = {
        "publish": "\n\n".join([
            item
            for item in (title, caption, keywords, str(field_value(fields, MATERIAL_PROMPT_FIELD, "") or "").strip())
            if item
        ]),
        "video_template": str(field_value(fields, "视频拆解提示词模板", "") or "").strip(),
        "video_actual": str(field_value(fields, "视频拆解实际提示词", "") or "").strip(),
        "image_template": str(field_value(fields, "商品组图提示词模板", "") or "").strip(),
        "image_actual": str(field_value(fields, "商品组图实际提示词", "") or "").strip(),
    }
    if field not in values:
        return jsonify({"success": False, "message": "unsupported detail field"}), 400
    return jsonify({"success": True, "data": {"text": values[field]}})


@app.route("/api/prompts")
def list_prompts():
    workflow = get_workflow()
    data = cached_list("prompts", workflow.list_prompt_configs, ttl_seconds=120)
    return jsonify({"success": True, "data": data})


@app.route("/api/doubao-prompt-options")
def list_doubao_prompt_options():
    try:
        country = get_country(request.args.get("country_code"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    rows = get_workflow().list_prompt_configs()
    data = [
        row for row in rows
        if str(row.get("模板Key") or "") == "doubao_seedance_video_prompt"
        and str(row.get("是否启用") or "") == "启用"
        and str(row.get("目标地区") or "") in {country.prompt_region, "通用"}
        and str(row.get("目标语言") or "") in {"", country.language_name}
    ]
    return jsonify({"success": True, "data": data})


@app.route("/api/prompts", methods=["POST"])
def create_prompt():
    body = dict(request.get_json(silent=True) or {})
    current_workflow = get_workflow()
    audit_write("prompt.create", "prompt-config:global", "started")
    try:
        with coordinated_resources(["prompt-config:global"], settings=settings):
            data = current_workflow.create_prompt_config(body)
    except ValueError as exc:
        audit_write("prompt.create", "prompt-config:global", "failed", str(exc))
        return jsonify({"success": False, "message": str(exc)}), 400
    except TimeoutError as exc:
        raise ServiceUnavailable(str(exc)) from exc
    except Exception as exc:
        audit_write("prompt.create", "prompt-config:global", "failed", str(exc))
        raise
    audit_write("prompt.create", f"prompt:{data.get('record_id', '')}", "done")
    clear_list_cache()
    return jsonify({"success": True, "data": data}), 201


@app.route("/api/prompts/<record_id>", methods=["PATCH"])
def update_prompt(record_id):
    body = dict(request.get_json(silent=True) or {})
    revision = str(body.pop("revision", "") or "").strip()
    if not revision:
        return jsonify({"success": False, "message": "Prompt 版本缺失，请刷新后重新编辑"}), 428
    workflow = get_workflow()
    audit_write("prompt.update", f"prompt:{record_id}", "started")
    try:
        with coordinated_resources(["prompt-config:global"], settings=settings):
            data = workflow.update_prompt_config(record_id, body, expected_revision=revision)
    except PromptConflictError as exc:
        audit_write("prompt.update", f"prompt:{record_id}", "conflict", str(exc))
        return jsonify({"success": False, "message": str(exc)}), 409
    except ValueError as exc:
        audit_write("prompt.update", f"prompt:{record_id}", "failed", str(exc))
        return jsonify({"success": False, "message": str(exc)}), 400
    except TimeoutError as exc:
        raise ServiceUnavailable(str(exc)) from exc
    except Exception as exc:
        audit_write("prompt.update", f"prompt:{record_id}", "failed", str(exc))
        raise
    audit_write("prompt.update", f"prompt:{record_id}", "done")
    clear_list_cache()
    return jsonify({"success": True, "data": data})


@app.route("/api/materials/<record_id>/templates", methods=["POST"])
def update_material_templates(record_id):
    body = request.get_json(silent=True) or {}
    styles = body.get("styles") or body.get("templates") or []
    if not isinstance(styles, list):
        return jsonify({"success": False, "message": "styles must be an array"}), 400
    workflow = get_workflow()
    audit_write("material.update_templates", f"material:{record_id}", "started")
    try:
        with coordinated_resources([f"material:{record_id}"], settings=settings):
            data = workflow.update_material_templates(record_id, styles)
    except TimeoutError as exc:
        raise ServiceUnavailable(str(exc)) from exc
    except Exception as exc:
        audit_write("material.update_templates", f"material:{record_id}", "failed", str(exc))
        raise
    audit_write("material.update_templates", f"material:{record_id}", "done")
    clear_list_cache()
    return jsonify({"success": True, "data": data})


@app.route("/api/tasks")
def list_tasks():
    workflow = get_workflow()
    limit = request_limit()
    data = cached_list(f"tasks:{limit}", lambda: workflow.list_tasks(limit=limit), ttl_seconds=10)
    return jsonify({"success": True, "data": data})


@app.route("/api/doubao-items")
def list_doubao_items():
    workflow = get_workflow()
    limit = request_limit()
    data = cached_list(f"doubao:{limit}", lambda: workflow.list_doubao_items(limit=limit), ttl_seconds=120)
    return jsonify({"success": True, "data": data})


@app.route("/api/part-time-items")
def list_part_time_items():
    workflow = get_workflow()
    days = query_days()
    data = cached_list(
        f"part-time-publish:{days}",
        lambda: workflow.list_part_time_publish_items(limit=None, days=days),
        ttl_seconds=60,
    )
    return jsonify({"success": True, "data": data})


@app.route("/api/part-time-items/batch-sync-publish", methods=["POST"])
def batch_sync_part_time_publish():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    if not isinstance(record_ids, list):
        return jsonify({"success": False, "message": "record_ids is required"}), 400
    ids = list(dict.fromkeys(str(item).strip() for item in record_ids if str(item).strip()))
    if not ids:
        return jsonify({"success": False, "message": "record_ids is required"}), 400
    batch_size = 200
    selection_limit = 1000
    if len(ids) > selection_limit:
        return jsonify({
            "success": False,
            "message": f"单次最多选择 {selection_limit} 条，请缩小行号范围后重试",
            "selected_count": len(ids),
            "selection_limit": selection_limit,
        }), 400

    raw_row_numbers = body.get("row_numbers")
    row_numbers: dict[str, int] = {}
    if isinstance(raw_row_numbers, dict):
        for record_id in ids:
            try:
                row_number = int(raw_row_numbers.get(record_id))
            except (TypeError, ValueError):
                continue
            if row_number > 0:
                row_numbers[record_id] = row_number

    selection_label = str(body.get("selection_label") or "").strip()[:120]
    chunks = [ids[index:index + batch_size] for index in range(0, len(ids), batch_size)]
    batch_group_id = uuid4().hex[:16] if len(chunks) > 1 else ""
    job_ids = []
    for index, chunk in enumerate(chunks, start=1):
        payload = {
            "record_ids": chunk,
            "limit": batch_size,
        }
        chunk_row_numbers = {
            record_id: row_numbers[record_id]
            for record_id in chunk
            if record_id in row_numbers
        }
        if chunk_row_numbers:
            payload["row_numbers"] = chunk_row_numbers
        if batch_group_id:
            payload["batch_group_id"] = batch_group_id
        if selection_label:
            if len(chunks) == 1:
                payload["selection_label"] = selection_label
            else:
                suffix = f"第 {index}/{len(chunks)} 批（{len(chunk)} 条）"
                prefix = selection_label[:max(0, 120 - len(suffix) - 3)]
                payload["selection_label"] = f"{prefix} · {suffix}"
        job_ids.append(enqueue_workflow_action(
            "part-time-batch-sync-publish",
            "publish.sync_part_time",
            payload,
        ))

    return jsonify({
        "success": True,
        "job_id": job_ids[0],
        "job_ids": job_ids,
        "batch_count": len(job_ids),
        "batch_group_id": batch_group_id,
        "selected_count": len(ids),
        "submitted_count": len(ids),
    })


@app.route("/api/doubao-items/export-batch-queue", methods=["POST"])
def export_doubao_batch_queue():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    if not isinstance(record_ids, list):
        return jsonify({"success": False, "message": "record_ids is required"}), 400
    ids = list(dict.fromkeys(str(item).strip() for item in record_ids if str(item).strip()))[:200]
    if not ids:
        return jsonify({"success": False, "message": "请先选择要导出的豆包素材"}), 400
    try:
        package = get_workflow().export_doubao_batch_queue(ids)
    except BatchQueueExportError as exc:
        return jsonify({"success": False, "message": str(exc)}), 422
    response = send_file(
        BytesIO(package.content),
        mimetype="application/zip",
        as_attachment=True,
        download_name=package.filename,
        max_age=0,
    )
    response.headers["X-Exported-Items"] = str(package.item_count)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/doubao-items/page")
def list_doubao_items_page():
    workflow = get_workflow()
    page_size = query_int("page_size", 100, 100)
    days = query_days()
    page_token = str(request.args.get("page_token") or "").strip()
    order = str(request.args.get("order") or "latest").strip().lower()
    if order not in {"latest", "oldest"}:
        return jsonify({"success": False, "message": "order must be latest or oldest"}), 400
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    data = cached_list(
        f"doubao-page:{order}:{days}:{page_size}:{page_token}:{offset}",
        lambda: workflow.list_doubao_items_page(
            page_size=page_size,
            page_token=page_token,
            start_index=offset + 1,
            order=order,
            days=days,
        ),
        ttl_seconds=120,
    )
    return jsonify({
        "success": True,
        "data": data["items"],
        "has_more": data["has_more"],
        "page_token": data["page_token"],
        "total": data.get("total", 0),
    })


@app.route("/api/doubao-items/range")
def list_doubao_items_range():
    start_row = query_int("start_row", 1, 100000)
    end_row = query_int("end_row", start_row, 100000)
    start = min(start_row, end_row)
    end = max(start_row, end_row)
    if end - start + 1 > 1000:
        return jsonify({
            "success": False,
            "message": "单次最多按行号读取 1000 条，请缩小范围后重试",
        }), 400
    data = cached_list(
        f"doubao-range:{start}:{end}",
        lambda: get_workflow().list_doubao_items_range(
            start,
            end,
            force_refresh=force_refresh_requested(),
        ),
        ttl_seconds=120,
    )
    return jsonify({
        "success": True,
        "data": data["items"],
        "total": data.get("total", 0),
    })


@app.route("/api/doubao-items/row-index")
def list_doubao_row_index():
    data = cached_list(
        "doubao-row-index",
        lambda: get_workflow().list_doubao_row_index(
            force_refresh=force_refresh_requested(),
        ),
        ttl_seconds=300,
    )
    return jsonify({"success": True, "data": data, "total": len(data)})


@app.route("/api/doubao-items/<record_id>/generate-prompt", methods=["POST"])
def generate_doubao_prompt(record_id):
    body = request.get_json(silent=True) or {}
    try:
        country = get_country(body.get("country_code"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    prompt_snapshot = None
    config_id = str(body.get("prompt_config_id") or "").strip()
    if config_id:
        try:
            prompt_snapshot = get_workflow().prompt_configs.resolve_doubao_snapshot(
                record_id=config_id,
                region=country.prompt_region,
                language=country.language_name,
            )
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400
    payload = {"record_id": record_id, "country_code": country.code}
    if prompt_snapshot:
        payload["prompt_snapshot"] = prompt_snapshot
    job_id = enqueue_workflow_action(
        f"doubao-generate-prompt:{record_id}:{country.code}",
        "doubao.generate_prompt",
        payload,
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/doubao-items/<record_id>/generate-white-background", methods=["POST"])
def generate_doubao_white_background(record_id):
    job_id = enqueue_workflow_action(
        f"doubao-generate-white-background:{record_id}",
        "doubao.generate_white_background",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/doubao-items/<record_id>/sync-part-time", methods=["POST"])
def sync_doubao_part_time(record_id):
    body = request.get_json(silent=True) or {}
    try:
        country = get_country(body.get("country_code"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    job_id = enqueue_workflow_action(
        f"doubao-sync-part-time:{record_id}:{country.code}",
        "doubao.sync_part_time",
        {"record_id": record_id, "country_code": country.code},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/doubao-items/<record_id>/store-image", methods=["POST"])
def store_doubao_image(record_id):
    job_id = enqueue_workflow_action(
        f"doubao-store-image:{record_id}",
        "doubao.store_image",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/dola-local/tasks")
def list_dola_local_tasks():
    workflow = get_workflow()
    limit = request_limit(default=200, maximum=1000)
    include_done = query_flag("include_done")
    data = cached_list(
        f"dola-local:{limit}:{include_done}",
        lambda: workflow.list_dola_local_tasks(limit=limit, include_done=include_done),
        ttl_seconds=20,
    )
    return jsonify({"success": True, "data": data})


@app.route("/api/dola-local/tasks/<record_id>")
def get_dola_local_task(record_id):
    workflow = get_workflow()
    data = workflow.get_dola_local_task(record_id)
    return jsonify({"success": True, "data": data})


@app.route("/api/dola-local/tasks/<record_id>/status", methods=["POST"])
def update_dola_local_status(record_id):
    body = request.get_json(silent=True) or {}
    workflow = get_workflow()
    audit_write("dola.update_status", f"task:{record_id}", "started")
    try:
        with coordinated_resources([f"task:{record_id}"], settings=settings):
            data = workflow.update_dola_local_status(
                record_id,
                str(body.get("status") or ""),
                str(body.get("error") or ""),
            )
    except TimeoutError as exc:
        raise ServiceUnavailable(str(exc)) from exc
    except Exception as exc:
        audit_write("dola.update_status", f"task:{record_id}", "failed", str(exc))
        raise
    audit_write("dola.update_status", f"task:{record_id}", "done")
    clear_list_cache()
    return jsonify({"success": True, "data": data})


@app.route("/api/dola-local/tasks/<record_id>/upload-video", methods=["POST"])
def upload_dola_local_video(record_id):
    file = request.files.get("file")
    if not file:
        return jsonify({"success": False, "message": "file is required"}), 400
    workflow = get_workflow()
    audit_write("dola.upload_video", f"task:{record_id}", "started")
    with TemporaryDirectory() as tmpdir:
        filename = file.filename or f"{record_id}.mp4"
        target = Path(tmpdir) / filename
        file.save(target)
        try:
            with coordinated_resources([f"task:{record_id}"], settings=settings):
                data = workflow.store_dola_local_video_file(
                    record_id,
                    target,
                    file.mimetype or "video/mp4",
                )
        except TimeoutError as exc:
            raise ServiceUnavailable(str(exc)) from exc
        except Exception as exc:
            audit_write("dola.upload_video", f"task:{record_id}", "failed", str(exc))
            raise
    audit_write("dola.upload_video", f"task:{record_id}", "done")
    clear_list_cache()
    return jsonify({"success": True, "data": data})


@app.route("/api/doubao-items/batch-store-images", methods=["POST"])
def batch_store_doubao_images():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    limit = int(body.get("limit", 50))
    job_id = enqueue_workflow_action(
        "doubao-batch-store-images",
        "doubao.batch_store_images",
        {"record_ids": record_ids or [], "limit": limit},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/doubao-items/batch-operation", methods=["POST"])
def batch_doubao_operation():
    body = request.get_json(silent=True) or {}
    operation = str(body.get("operation") or "").strip()
    record_ids = body.get("record_ids")
    if not isinstance(record_ids, list):
        return jsonify({"success": False, "message": "record_ids is required"}), 400
    ids = list(dict.fromkeys(str(item).strip() for item in record_ids if str(item).strip()))
    if not ids:
        return jsonify({"success": False, "message": "record_ids is required"}), 400
    raw_row_numbers = body.get("row_numbers")
    row_numbers: dict[str, int] = {}
    if isinstance(raw_row_numbers, dict):
        for record_id in ids:
            try:
                row_number = int(raw_row_numbers.get(record_id))
            except (TypeError, ValueError):
                continue
            if row_number > 0:
                row_numbers[record_id] = row_number
    batch_size = 200
    selection_limit = 1000
    if len(ids) > selection_limit:
        return jsonify({
            "success": False,
            "message": f"单次最多选择 {selection_limit} 条，请缩小行号范围后重试",
            "selected_count": len(ids),
            "selection_limit": selection_limit,
        }), 400

    action_map = {
        "store_images": ("doubao.batch_store_images", "doubao-batch-store-images", "图片转存"),
        "detect_faces": ("doubao.batch_detect_faces", "doubao-batch-detect-faces", "人脸检测"),
        "delete_face_records": ("doubao.batch_delete_face_records", "doubao-batch-delete-face-records", "删除含人脸记录"),
        "remove_face_images": ("doubao.batch_delete_face_records", "doubao-batch-delete-face-records", "删除含人脸记录"),
        "generate_prompts": ("doubao.batch_generate_prompts", "doubao-batch-generate-prompts", "提示词生成"),
        "sync_part_time": ("doubao.batch_sync_part_time", "doubao-batch-sync-part-time", "兼职表同步"),
        "process_workflow": ("doubao.batch_workflow", "doubao-batch-workflow", "按流程批量处理"),
        "sync_publish": ("publish.sync_doubao", "doubao-batch-sync-publish", "发布表同步"),
    }
    selected = action_map.get(operation)
    if not selected:
        return jsonify({"success": False, "message": f"不支持的批量操作：{operation}"}), 400

    selection_label = str(body.get("selection_label") or "").strip()[:120]
    country = None
    if operation in {"generate_prompts", "sync_part_time", "process_workflow"}:
        try:
            country = get_country(body.get("country_code"))
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400
    readiness = None
    if operation == "sync_publish":
        readiness = get_workflow().doubao_publish_readiness(ids)

    action, job_name, label = selected
    chunks = [ids[index:index + batch_size] for index in range(0, len(ids), batch_size)]
    batch_group_id = uuid4().hex[:16] if len(chunks) > 1 else ""
    job_ids = []
    for index, chunk in enumerate(chunks, start=1):
        payload = {"record_ids": chunk}
        chunk_row_numbers = {
            record_id: row_numbers[record_id]
            for record_id in chunk
            if record_id in row_numbers
        }
        if chunk_row_numbers:
            payload["row_numbers"] = chunk_row_numbers
        if batch_group_id:
            payload["batch_group_id"] = batch_group_id
        if selection_label:
            if len(chunks) == 1:
                payload["selection_label"] = selection_label
            else:
                suffix = f"第 {index}/{len(chunks)} 批（{len(chunk)} 条）"
                prefix = selection_label[:max(0, 120 - len(suffix) - 3)]
                payload["selection_label"] = f"{prefix} · {suffix}"
        if country:
            payload["country_code"] = country.code
        if operation in {"generate_prompts", "process_workflow"} and body.get("prompt_config_id"):
            try:
                payload["prompt_snapshot"] = get_workflow().prompt_configs.resolve_doubao_snapshot(
                    record_id=str(body.get("prompt_config_id") or "").strip(),
                    region=country.prompt_region,
                    language=country.language_name,
                )
            except ValueError as exc:
                return jsonify({"success": False, "message": str(exc)}), 400
        if operation in {"sync_part_time", "process_workflow"}:
            payload["force_update"] = body.get("force_update") is True
        if operation == "process_workflow":
            payload["detect_faces"] = body.get("detect_faces") is True
            payload["repair_faces"] = body.get("repair_faces") is True and payload["detect_faces"]
        if operation == "sync_publish":
            payload["limit"] = min(max(int(body.get("limit", 200)), 1), 200)
        job_ids.append(enqueue_workflow_action(job_name, action, payload))

    response = {
        "success": True,
        "job_id": job_ids[0],
        "job_ids": job_ids,
        "batch_count": len(job_ids),
        "batch_group_id": batch_group_id or None,
        "batch_size": batch_size,
        "selected_count": len(ids),
        "submitted_count": sum(len(chunk) for chunk in chunks),
        "operation": operation,
        "label": label,
    }
    if readiness is not None:
        response["readiness"] = readiness
    result = jsonify(response)
    if operation in {"delete_face_records", "remove_face_images"}:
        response["deprecated"] = True
        response["deprecation_message"] = "含人脸记录删除操作已废弃，请使用白底图修复"
        result = jsonify(response)
        result.headers["Deprecation"] = "true"
        result.headers["Warning"] = '299 - "Deprecated face-record deletion; use white-background repair"'
    return result


def validated_doubao_batch_request():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    if not isinstance(record_ids, list) or not record_ids:
        raise ValueError("record_ids is required")
    ids = list(dict.fromkeys(str(item).strip() for item in record_ids if str(item).strip()))
    if not ids:
        raise ValueError("record_ids is required")
    country = get_country(body.get("country_code"))
    return ids[:200], country


@app.route("/api/doubao-items/batch-generate-prompts", methods=["POST"])
def batch_generate_doubao_prompts():
    try:
        record_ids, country = validated_doubao_batch_request()
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    job_id = enqueue_workflow_action(
        f"doubao-batch-prompts:{country.code}",
        "doubao.batch_generate_prompts",
        {"record_ids": record_ids, "country_code": country.code},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/doubao-items/batch-sync-part-time", methods=["POST"])
def batch_sync_doubao_part_time():
    body = request.get_json(silent=True) or {}
    try:
        record_ids, country = validated_doubao_batch_request()
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    job_id = enqueue_workflow_action(
        f"doubao-batch-sync-part-time:{country.code}",
        "doubao.batch_sync_part_time",
        {
            "record_ids": record_ids,
            "country_code": country.code,
            "force_update": body.get("force_update") is True,
        },
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/doubao-items/batch-sync-publish", methods=["POST"])
def batch_sync_doubao_publish():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    if not isinstance(record_ids, list):
        return jsonify({"success": False, "message": "record_ids is required"}), 400
    ids = list(dict.fromkeys(str(item).strip() for item in record_ids if str(item).strip()))[:200]
    if not ids:
        return jsonify({"success": False, "message": "record_ids is required"}), 400
    readiness = get_workflow().doubao_publish_readiness(ids)
    if not readiness["eligible_videos"]:
        return jsonify({
            "success": False,
            "message": publish_readiness_error_message(readiness),
            "readiness": readiness,
        }), 409
    job_id = enqueue_workflow_action(
        "doubao-batch-sync-publish",
        "publish.sync_doubao",
        {"record_ids": ids, "limit": min(max(int(body.get("limit", 200)), 1), 200)},
    )
    return jsonify({"success": True, "job_id": job_id, "readiness": readiness})


@app.route("/api/materials/<record_id>/prepare", methods=["POST"])
def prepare_material(record_id):
    job_id = enqueue_workflow_action(
        f"prepare-material:{record_id}",
        "material.prepare",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/<record_id>/analyze-video", methods=["POST"])
def analyze_material_video(record_id):
    job_id = enqueue_workflow_action(
        f"analyze-video:{record_id}",
        "material.analyze_video",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/<record_id>/store-image", methods=["POST"])
def store_material_image(record_id):
    job_id = enqueue_workflow_action(
        f"store-image:{record_id}",
        "material.store_image",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/<record_id>/generate-images", methods=["POST"])
def generate_material_images(record_id):
    job_id = enqueue_workflow_action(
        f"generate-images:{record_id}",
        "material.generate_images",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/<record_id>/create-tasks", methods=["POST"])
def create_material_tasks(record_id):
    job_id = enqueue_workflow_action(
        f"create-tasks:{record_id}",
        "material.create_tasks",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/<record_id>/retry-failed-step", methods=["POST"])
def retry_failed_material_step(record_id):
    job_id = enqueue_workflow_action(
        f"retry-failed-step:{record_id}",
        "material.retry_failed_step",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/batch-step", methods=["POST"])
def batch_material_step():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids") or []
    step = str(body.get("step") or "").strip()
    limit = int(body.get("limit", 50))
    if step not in {"analyze-video", "store-image", "generate-images", "create-tasks"}:
        return jsonify({"success": False, "message": "unsupported material step"}), 400
    if not isinstance(record_ids, list) or not record_ids:
        return jsonify({"success": False, "message": "record_ids is required"}), 400

    job_id = enqueue_workflow_action(
        f"batch-{step}",
        "material.batch_step",
        {"record_ids": record_ids[:limit], "step": step, "limit": limit},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/batch-prepare", methods=["POST"])
def batch_prepare():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    limit = int(body.get("limit", 5))
    job_id = enqueue_workflow_action(
        "batch-prepare-materials",
        "material.batch_prepare",
        {"record_ids": record_ids or [], "limit": limit},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/tasks/<record_id>/generate-video", methods=["POST"])
def generate_task_video(record_id):
    job_id = enqueue_workflow_action(
        f"generate-video:{record_id}",
        "task.generate_video",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/tasks/<record_id>/sync-reference-image", methods=["POST"])
def sync_task_reference_image(record_id):
    job_id = enqueue_workflow_action(
        f"sync-reference-image:{record_id}",
        "task.sync_reference_image",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/tasks/<record_id>/store-manual-video", methods=["POST"])
def store_manual_task_video(record_id):
    job_id = enqueue_workflow_action(
        f"store-manual-video:{record_id}",
        "task.store_manual_video",
        {"record_id": record_id},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/tasks/batch-generate-video", methods=["POST"])
def batch_generate_video():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    limit = int(body.get("limit", 3))
    job_id = enqueue_workflow_action(
        "batch-generate-videos",
        "task.batch_generate_video",
        {"record_ids": record_ids or [], "limit": limit},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/tasks/batch-sync-reference-images", methods=["POST"])
def batch_sync_reference_images():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    limit = int(body.get("limit", 100))
    job_id = enqueue_workflow_action(
        "batch-sync-reference-images",
        "task.batch_sync_reference_images",
        {"record_ids": record_ids or [], "limit": limit},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/tasks/batch-store-manual-videos", methods=["POST"])
def batch_store_manual_videos():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    limit = int(body.get("limit", 100))
    job_id = enqueue_workflow_action(
        "batch-store-manual-videos",
        "task.batch_store_manual_videos",
        {"record_ids": record_ids or [], "limit": limit},
    )
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/tasks/sync-publish", methods=["POST"])
def sync_publish_tasks():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    limit = int(body.get("limit", 100))
    job_id = enqueue_workflow_action(
        "sync-publish-tasks",
        "publish.sync",
        {"record_ids": record_ids or [], "limit": limit},
    )
    return jsonify({"success": True, "job_id": job_id})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "38888")), debug=True)
