import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from flask import Flask, g, has_request_context, jsonify, request, send_from_directory
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
from api.feishu import field_value
from api.image_gen import STYLE_LABELS
from api.jobs import coordinated_resources, enqueue_job, get_job, job_health, list_jobs
from api.logging_bus import recent_logs
from api.persistence import get_persistence
from api.workflow import MATERIAL_PROMPT_FIELD, PromptConflictError, ViralRemixWorkflow


app = Flask(__name__, static_folder="public", static_url_path="")
CORS(app)
workflow = ViralRemixWorkflow(settings, ledger=get_persistence(settings))
cache_lock = Lock()
list_cache = {}


def get_workflow() -> ViralRemixWorkflow:
    return workflow


def cached_list(key: str, loader, ttl_seconds: int = 20):
    force = str(request.args.get("force") or "").lower() in ("1", "true", "yes")
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
        list_cache.clear()


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


@app.route("/api/jobs")
def job_list():
    return jsonify({"success": True, "data": list_jobs()})


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


@app.route("/api/prompts/<record_id>", methods=["PATCH"])
def update_prompt(record_id):
    body = dict(request.get_json(silent=True) or {})
    revision = str(body.pop("revision", "") or "").strip()
    if not revision:
        return jsonify({"success": False, "message": "Prompt 版本缺失，请刷新后重新编辑"}), 428
    workflow = get_workflow()
    audit_write("prompt.update", f"prompt:{record_id}", "started")
    try:
        with coordinated_resources([f"prompt:{record_id}"], settings=settings):
            data = workflow.update_prompt_config(record_id, body, expected_revision=revision)
    except PromptConflictError as exc:
        audit_write("prompt.update", f"prompt:{record_id}", "conflict", str(exc))
        return jsonify({"success": False, "message": str(exc)}), 409
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


@app.route("/api/doubao-items/page")
def list_doubao_items_page():
    workflow = get_workflow()
    page_size = query_int("page_size", 100, 100)
    page_token = str(request.args.get("page_token") or "").strip()
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    data = cached_list(
        f"doubao-page:{page_size}:{page_token}:{offset}",
        lambda: workflow.list_doubao_items_page(
            page_size=page_size,
            page_token=page_token,
            start_index=offset + 1,
        ),
        ttl_seconds=120,
    )
    return jsonify({
        "success": True,
        "data": data["items"],
        "has_more": data["has_more"],
        "page_token": data["page_token"],
    })


@app.route("/api/doubao-items/<record_id>/generate-prompt", methods=["POST"])
def generate_doubao_prompt(record_id):
    body = request.get_json(silent=True) or {}
    try:
        country = get_country(body.get("country_code"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    job_id = enqueue_workflow_action(
        f"doubao-generate-prompt:{record_id}:{country.code}",
        "doubao.generate_prompt",
        {"record_id": record_id, "country_code": country.code},
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
    try:
        record_ids, country = validated_doubao_batch_request()
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    job_id = enqueue_workflow_action(
        f"doubao-batch-sync-part-time:{country.code}",
        "doubao.batch_sync_part_time",
        {"record_ids": record_ids, "country_code": country.code},
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
            "message": "所选素材在兼职表中还没有可同步的视频：需要任务状态为已完成、视频地址已填写，且审核未标记为无效或未去水印。",
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
