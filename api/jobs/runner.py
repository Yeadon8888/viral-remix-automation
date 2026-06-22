import traceback
from threading import Lock, Thread
from typing import Any, Callable
from uuid import uuid4

from api.logging_bus import add_log

MAX_JOBS = 10

jobs: dict[str, dict[str, Any]] = {}
job_lock = Lock()


def prune_jobs() -> None:
    while len(jobs) > MAX_JOBS:
        for job_id, job in list(jobs.items()):
            if job.get("status") != "running":
                jobs.pop(job_id, None)
                break
        else:
            break


def get_job(job_id: str) -> dict[str, Any] | None:
    with job_lock:
        return jobs.get(job_id)


def list_jobs() -> list[dict[str, Any]]:
    with job_lock:
        return list(jobs.values())[-MAX_JOBS:]


def run_job(name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    with job_lock:
        for existing in reversed(list(jobs.values())):
            if existing.get("name") == name and existing.get("status") == "running":
                add_log("info", f"任务已在运行，复用: {name}", "job", existing["id"])
                return str(existing["id"])
        job_id = uuid4().hex[:12]
        jobs[job_id] = {"id": job_id, "name": name, "status": "running", "result": None, "error": None}
        prune_jobs()

    def target() -> None:
        try:
            add_log("info", f"任务开始: {name}", "job", job_id)
            result = fn(*args, job_id=job_id, **kwargs)
            with job_lock:
                jobs[job_id].update({"status": "done", "result": result})
                prune_jobs()
            add_log("success", f"任务完成: {name}", "job", job_id)
        except Exception as exc:
            with job_lock:
                jobs[job_id].update({"status": "failed", "error": str(exc)})
                prune_jobs()
            traceback.print_exc()
            add_log("error", f"任务失败: {name}: {exc}", "job", job_id)

    Thread(target=target, daemon=True).start()
    return job_id
