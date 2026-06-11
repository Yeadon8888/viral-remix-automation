import os
import traceback
from threading import Lock, Thread
from uuid import uuid4

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

from api.config import settings
from api.image_gen import STYLE_LABELS
from api.logging_bus import add_log, recent_logs
from api.workflow import ViralRemixWorkflow


app = Flask(__name__, static_folder="public", static_url_path="")
CORS(app)

jobs: dict[str, dict] = {}
job_lock = Lock()


def require_admin():
    if not settings.admin_token:
        return None
    token = request.headers.get("X-Admin-Token") or request.args.get("token")
    if token != settings.admin_token:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    return None


def run_job(name: str, fn, *args, **kwargs) -> str:
    with job_lock:
        for existing in reversed(list(jobs.values())):
            if existing.get("name") == name and existing.get("status") == "running":
                add_log("info", f"任务已在运行，复用: {name}", "job", existing["id"])
                return existing["id"]
        job_id = uuid4().hex[:12]
        jobs[job_id] = {"id": job_id, "name": name, "status": "running", "result": None, "error": None}

    def target():
        try:
            add_log("info", f"任务开始: {name}", "job", job_id)
            result = fn(*args, job_id=job_id, **kwargs)
            with job_lock:
                jobs[job_id].update({"status": "done", "result": result})
            add_log("success", f"任务完成: {name}", "job", job_id)
        except Exception as exc:
            with job_lock:
                jobs[job_id].update({"status": "failed", "error": str(exc)})
            traceback.print_exc()
            add_log("error", f"任务失败: {name}: {exc}", "job", job_id)

    Thread(target=target, daemon=True).start()
    return job_id


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


@app.route("/api/health")
def health():
    return jsonify({"success": True, "missing": settings.missing_required()})


@app.route("/api/config")
def config_status():
    return jsonify({
        "success": True,
        "missing": settings.missing_required(),
        "source_table_id": settings.source_table_id,
        "task_table_id": settings.task_table_id,
        "prompt_table_id": settings.prompt_table_id,
        "doubao_table_id": settings.doubao_table_id,
        "gemini_model": settings.gemini_model,
        "cpa_model": settings.cpa_video_model,
        "image_styles": settings.image_scene_styles,
        "image_style_labels": STYLE_LABELS,
        "newtoken_timeout_seconds": settings.newtoken_timeout_seconds,
        "storage_mode": "upload_gateway" if settings.has_upload_gateway else "direct_r2",
    })


@app.route("/api/logs")
def logs():
    return jsonify({"success": True, "data": recent_logs(int(request.args.get("limit", 150)))})


@app.route("/api/jobs/<job_id>")
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"success": False, "message": "job not found"}), 404
    return jsonify({"success": True, "data": job})


@app.route("/api/jobs")
def job_list():
    return jsonify({"success": True, "data": list(jobs.values())[-50:]})


@app.route("/api/materials")
def list_materials():
    workflow = ViralRemixWorkflow(settings)
    data = workflow.list_materials(limit=int(request.args.get("limit", 100)))
    return jsonify({"success": True, "data": data})


@app.route("/api/prompts")
def list_prompts():
    workflow = ViralRemixWorkflow(settings)
    return jsonify({"success": True, "data": workflow.list_prompt_configs()})


@app.route("/api/prompts/<record_id>", methods=["PATCH"])
def update_prompt(record_id):
    body = request.get_json(silent=True) or {}
    workflow = ViralRemixWorkflow(settings)
    data = workflow.update_prompt_config(record_id, body)
    return jsonify({"success": True, "data": data})


@app.route("/api/materials/<record_id>/templates", methods=["POST"])
def update_material_templates(record_id):
    body = request.get_json(silent=True) or {}
    styles = body.get("styles") or body.get("templates") or []
    if not isinstance(styles, list):
        return jsonify({"success": False, "message": "styles must be an array"}), 400
    workflow = ViralRemixWorkflow(settings)
    data = workflow.update_material_templates(record_id, styles)
    return jsonify({"success": True, "data": data})


@app.route("/api/tasks")
def list_tasks():
    workflow = ViralRemixWorkflow(settings)
    data = workflow.list_tasks(limit=int(request.args.get("limit", 100)))
    return jsonify({"success": True, "data": data})


@app.route("/api/doubao-items")
def list_doubao_items():
    workflow = ViralRemixWorkflow(settings)
    data = workflow.list_doubao_items(limit=int(request.args.get("limit", 100)))
    return jsonify({"success": True, "data": data})


@app.route("/api/doubao-items/<record_id>/generate-prompt", methods=["POST"])
def generate_doubao_prompt(record_id):
    workflow = ViralRemixWorkflow(settings)
    job_id = run_job(f"doubao-generate-prompt:{record_id}", workflow.generate_doubao_prompt, record_id)
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/<record_id>/prepare", methods=["POST"])
def prepare_material(record_id):
    workflow = ViralRemixWorkflow(settings)
    job_id = run_job(f"prepare-material:{record_id}", workflow.prepare_material, record_id)
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/<record_id>/analyze-video", methods=["POST"])
def analyze_material_video(record_id):
    workflow = ViralRemixWorkflow(settings)
    job_id = run_job(f"analyze-video:{record_id}", workflow.analyze_viral_video, record_id)
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/<record_id>/store-image", methods=["POST"])
def store_material_image(record_id):
    workflow = ViralRemixWorkflow(settings)
    job_id = run_job(f"store-image:{record_id}", workflow.store_product_image, record_id)
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/<record_id>/generate-images", methods=["POST"])
def generate_material_images(record_id):
    workflow = ViralRemixWorkflow(settings)
    job_id = run_job(f"generate-images:{record_id}", workflow.generate_product_images, record_id)
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/<record_id>/create-tasks", methods=["POST"])
def create_material_tasks(record_id):
    workflow = ViralRemixWorkflow(settings)
    job_id = run_job(f"create-tasks:{record_id}", workflow.create_video_tasks, record_id)
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/materials/batch-prepare", methods=["POST"])
def batch_prepare():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    limit = int(body.get("limit", 5))
    workflow = ViralRemixWorkflow(settings)

    def runner(job_id=None):
        ids = record_ids
        if not ids:
            rows = workflow.list_materials(limit=100)
            ids = [
                row["record_id"]
                for row in rows
                if row.get("record_id") and row.get("爆款视频链接") and row.get("自动化状态") != "已创建任务"
            ][:limit]
        results = []
        for rid in ids[:limit]:
            try:
                results.append(workflow.prepare_material(rid, job_id=job_id))
            except Exception as exc:
                results.append({"record_id": rid, "error": str(exc)})
        return {"processed": len(results), "results": results}

    job_id = run_job("batch-prepare-materials", runner)
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/tasks/<record_id>/generate-video", methods=["POST"])
def generate_task_video(record_id):
    workflow = ViralRemixWorkflow(settings)
    job_id = run_job(f"generate-video:{record_id}", workflow.generate_task_video, record_id)
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/tasks/batch-generate-video", methods=["POST"])
def batch_generate_video():
    body = request.get_json(silent=True) or {}
    record_ids = body.get("record_ids")
    limit = int(body.get("limit", 3))
    workflow = ViralRemixWorkflow(settings)

    def runner(job_id=None):
        ids = record_ids
        if not ids:
            rows = workflow.list_tasks(limit=200)
            ids = [
                row["record_id"]
                for row in rows
                if row.get("record_id") and row.get("生成状态") in ("待生成视频", "内容已生成", "")
            ][:limit]
        results = []
        for rid in ids[:limit]:
            try:
                results.append(workflow.generate_task_video(rid, job_id=job_id))
            except Exception as exc:
                results.append({"record_id": rid, "error": str(exc)})
        return {"processed": len(results), "results": results}

    job_id = run_job("batch-generate-videos", runner)
    return jsonify({"success": True, "job_id": job_id})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "38888")), debug=True)
