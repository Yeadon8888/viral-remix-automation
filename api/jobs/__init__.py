from threading import Lock

from api.logging_bus import add_log
from api.persistence import get_persistence

from .actions import execute_action, normalize_payload, resource_keys_for
from .coordinator import MemoryJobBackend, RedisJobBackend
from .runner import run_job


_backend = None
_backend_lock = Lock()


def audited_executor(executor, ledger, action: str, payload: dict, job_id: str, operator: str):
    try:
        result = executor(action, payload, job_id, operator)
    except Exception as exc:
        try:
            ledger.audit(operator, action, job_id, "failed", str(exc))
        except Exception as audit_exc:
            add_log("error", f"任务 {job_id} 失败审计写入失败: {audit_exc}", "audit")
        raise
    try:
        ledger.audit(operator, action, job_id, "done")
    except Exception as audit_exc:
        add_log("error", f"任务 {job_id} 完成审计写入失败: {audit_exc}", "audit")
    return result


def get_backend(settings=None):
    global _backend
    if _backend is not None:
        return _backend
    if settings is None:
        from api.config import settings as default_settings

        settings = default_settings
    with _backend_lock:
        if _backend is not None:
            return _backend
        ledger = get_persistence(settings)
        executor = lambda action, payload, job_id, operator: audited_executor(
            execute_action, ledger, action, payload, job_id, operator
        )
        if getattr(settings, "redis_url", ""):
            import redis

            client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
            client.ping()
            _backend = RedisJobBackend(
                client,
                executor,
                max_workers=getattr(settings, "job_worker_concurrency", 2),
                lock_ttl_seconds=getattr(settings, "job_lock_ttl_seconds", 300),
            )
        elif getattr(settings, "coordination_required", False):
            raise RuntimeError("生产环境缺少 REDIS_URL，已拒绝启用不安全的内存任务队列")
        else:
            _backend = MemoryJobBackend(
                executor,
                max_workers=getattr(settings, "job_worker_concurrency", 2),
            )
        return _backend


def enqueue_job(name: str, action: str, payload: dict, operator: str = "system", settings=None) -> str:
    if settings is None:
        from api.config import settings as default_settings

        settings = default_settings
    normalized = normalize_payload(action, payload)
    resources = resource_keys_for(action, normalized)
    ledger = get_persistence(settings)
    ledger.audit(operator, action, ",".join(resources), "queued", name)
    return get_backend(settings).enqueue(
        name,
        action,
        normalized,
        resources,
        operator,
    )


def get_job(job_id: str):
    return get_backend().get(job_id)


def list_jobs(limit: int = 100):
    return get_backend().list(limit=limit)


def job_health():
    return get_backend().health()


def coordinated_resources(resource_keys: list[str], settings=None):
    return get_backend(settings).lock_resources(resource_keys)


def reset_backend_for_tests() -> None:
    global _backend
    with _backend_lock:
        if _backend is not None and hasattr(_backend, "shutdown"):
            _backend.shutdown()
        _backend = None


__all__ = [
    "enqueue_job",
    "audited_executor",
    "coordinated_resources",
    "get_backend",
    "get_job",
    "job_health",
    "list_jobs",
    "reset_backend_for_tests",
    "run_job",
]
