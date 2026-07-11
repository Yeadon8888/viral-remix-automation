import hashlib
import json
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Lock, RLock, Thread
from typing import Any, Callable
from uuid import uuid4


JobExecutor = Callable[[str, dict[str, Any], str, str], Any]


def canonical_payload_hash(name: str, action: str, payload: dict[str, Any]) -> str:
    body = json.dumps(
        {"name": name, "action": action, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class MemoryJobBackend:
    """Bounded local backend used by tests and local development only."""

    def __init__(self, executor: JobExecutor, max_workers: int = 2, max_jobs: int = 100):
        self.executor = executor
        self.max_jobs = max_jobs
        self._pool = ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="automation-job")
        self._lock = RLock()
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._dedupe: dict[str, str] = {}
        self._resource_locks: dict[str, Lock] = {}

    def _prune(self) -> None:
        while len(self._jobs) > self.max_jobs:
            for job_id, job in list(self._jobs.items()):
                if job["status"] not in {"queued", "running"}:
                    self._jobs.pop(job_id, None)
                    break
            else:
                break

    def enqueue(
        self,
        name: str,
        action: str,
        payload: dict[str, Any],
        resource_keys: list[str],
        operator: str,
    ) -> str:
        dedupe_key = canonical_payload_hash(name, action, payload)
        with self._lock:
            existing_id = self._dedupe.get(dedupe_key)
            existing = self._jobs.get(existing_id or "")
            if existing and existing["status"] in {"queued", "running"}:
                return str(existing["id"])
            job_id = uuid4().hex[:16]
            self._jobs[job_id] = {
                "id": job_id,
                "name": name,
                "action": action,
                "status": "queued",
                "operator": operator,
                "resource_keys": sorted(set(resource_keys)),
                "result": None,
                "error": None,
                "created_at": int(time.time() * 1000),
                "started_at": None,
                "finished_at": None,
                "dedupe_key": dedupe_key,
            }
            self._dedupe[dedupe_key] = job_id
            self._prune()
        self._pool.submit(self._run, job_id, action, dict(payload), operator)
        return job_id

    def _run(self, job_id: str, action: str, payload: dict[str, Any], operator: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = int(time.time() * 1000)
            locks = [self._resource_locks.setdefault(key, Lock()) for key in job["resource_keys"]]
        for lock in locks:
            lock.acquire()
        try:
            result = self.executor(action, payload, job_id, operator)
            with self._lock:
                self._jobs[job_id].update({"status": "done", "result": result})
        except Exception as exc:
            with self._lock:
                self._jobs[job_id].update({"status": "failed", "error": str(exc)})
        finally:
            for lock in reversed(locks):
                lock.release()
            with self._lock:
                job = self._jobs[job_id]
                job["finished_at"] = int(time.time() * 1000)
                if self._dedupe.get(job["dedupe_key"]) == job_id:
                    self._dedupe.pop(job["dedupe_key"], None)
                self._prune()

    @contextmanager
    def lock_resources(self, resource_keys: list[str]):
        with self._lock:
            locks = [self._resource_locks.setdefault(key, Lock()) for key in sorted(set(resource_keys))]
        for lock in locks:
            lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in list(self._jobs.values())[-max(1, limit):]]

    def health(self) -> dict[str, Any]:
        with self._lock:
            queued = sum(1 for item in self._jobs.values() if item["status"] == "queued")
            running = sum(1 for item in self._jobs.values() if item["status"] == "running")
        return {"backend": "memory", "persistent": False, "queued": queued, "running": running}

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


class _RedisLease:
    def __init__(self, redis_client, key: str, ttl_seconds: int, wait_seconds: int):
        self.redis = redis_client
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.wait_seconds = wait_seconds
        self.token = uuid4().hex

    def acquire(self) -> bool:
        deadline = time.time() + self.wait_seconds
        while time.time() < deadline:
            if self.redis.set(self.key, self.token, nx=True, ex=self.ttl_seconds):
                return True
            time.sleep(0.05)
        return False

    def release(self) -> None:
        with self.redis.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(self.key)
                    if str(pipe.get(self.key) or "") != self.token:
                        pipe.unwatch()
                        return
                    pipe.multi()
                    pipe.delete(self.key)
                    pipe.execute()
                    return
                except Exception as exc:
                    if exc.__class__.__name__ != "WatchError":
                        raise

    def refresh(self) -> bool:
        with self.redis.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(self.key)
                    if str(pipe.get(self.key) or "") != self.token:
                        pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.expire(self.key, self.ttl_seconds)
                    pipe.execute()
                    return True
                except Exception as exc:
                    if exc.__class__.__name__ != "WatchError":
                        raise


class RedisJobBackend:
    """Redis-backed persistent queue with cross-process record locks."""

    def __init__(
        self,
        redis_client,
        executor: JobExecutor,
        max_workers: int = 2,
        namespace: str = "viral-remix",
        lock_ttl_seconds: int = 3600,
        lock_wait_seconds: int = 30,
        start_workers: bool = True,
    ):
        self.redis = redis_client
        self.executor = executor
        self.max_workers = max(1, max_workers)
        self.namespace = namespace.strip(":")
        self.lock_ttl_seconds = max(30, lock_ttl_seconds)
        self.lock_wait_seconds = max(1, lock_wait_seconds)
        self._stop = Event()
        self._threads: list[Thread] = []
        if start_workers:
            self._recover_stale_jobs()
            self.start_workers()

    def _key(self, suffix: str) -> str:
        return f"{self.namespace}:{suffix}"

    @property
    def queue_key(self) -> str:
        return self._key("jobs:queue")

    @property
    def processing_key(self) -> str:
        return self._key("jobs:processing")

    @property
    def history_key(self) -> str:
        return self._key("jobs:history")

    def _job_key(self, job_id: str) -> str:
        return self._key(f"job:{job_id}")

    def _dedupe_key(self, digest: str) -> str:
        return self._key(f"dedupe:{digest}")

    def _recover_stale_jobs(self) -> int:
        recovery_key = self._key("jobs:recovery-lock")
        recovery_token = uuid4().hex
        if not self.redis.set(recovery_key, recovery_token, nx=True, ex=30):
            return 0
        recovered = 0
        try:
            now_ms = int(time.time() * 1000)
            for raw_job_id in self.redis.lrange(self.processing_key, 0, -1):
                job_id = str(raw_job_id)
                job = self.get(job_id)
                if not job:
                    self.redis.lrem(self.processing_key, 0, job_id)
                    continue
                lease_until = int(job.get("lease_until") or 0)
                queued_without_lease = job["status"] == "queued" and (
                    now_ms - int(job.get("created_at") or now_ms) >= 30_000
                )
                if not queued_without_lease and not (
                    job["status"] == "running" and lease_until and lease_until <= now_ms
                ):
                    continue
                with self.redis.pipeline() as pipe:
                    pipe.hset(self._job_key(job_id), mapping={
                        "status": "queued",
                        "started_at": "",
                        "lease_until": "",
                    })
                    pipe.lrem(self.processing_key, 0, job_id)
                    pipe.lpush(self.queue_key, job_id)
                    pipe.execute()
                recovered += 1
            return recovered
        finally:
            if str(self.redis.get(recovery_key) or "") == recovery_token:
                self.redis.delete(recovery_key)

    def start_workers(self) -> None:
        if self._threads:
            return
        for index in range(self.max_workers):
            thread = Thread(target=self._worker_loop, name=f"redis-job-{index + 1}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def enqueue(
        self,
        name: str,
        action: str,
        payload: dict[str, Any],
        resource_keys: list[str],
        operator: str,
    ) -> str:
        digest = canonical_payload_hash(name, action, payload)
        dedupe_key = self._dedupe_key(digest)
        existing_id = self.redis.get(dedupe_key)
        if existing_id:
            existing = self.get(str(existing_id))
            if not existing or existing["status"] in {"queued", "running"}:
                return str(existing_id)
            self.redis.delete(dedupe_key)

        job_id = uuid4().hex[:16]
        if not self.redis.set(dedupe_key, job_id, nx=True, ex=86400):
            return str(self.redis.get(dedupe_key) or job_id)

        created_at = int(time.time() * 1000)
        self.redis.hset(self._job_key(job_id), mapping={
            "id": job_id,
            "name": name,
            "action": action,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            "resource_keys": json.dumps(sorted(set(resource_keys)), ensure_ascii=False),
            "operator": operator,
            "status": "queued",
            "result": "",
            "error": "",
            "created_at": str(created_at),
            "started_at": "",
            "finished_at": "",
            "dedupe_key": digest,
            "lease_until": "",
        })
        self.redis.expire(self._job_key(job_id), 7 * 86400)
        self.redis.zadd(self.history_key, {job_id: created_at})
        self.redis.lpush(self.queue_key, job_id)
        return job_id

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            job_id = self.redis.brpoplpush(self.queue_key, self.processing_key, timeout=1)
            if not job_id:
                continue
            job_id = str(job_id)
            try:
                self._execute_job(job_id)
            finally:
                self.redis.lrem(self.processing_key, 0, job_id)

    def _execute_job(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job or job["status"] != "queued":
            return
        locks = [
            _RedisLease(
                self.redis,
                self._key(f"lock:{resource}"),
                self.lock_ttl_seconds,
                self.lock_wait_seconds,
            )
            for resource in job["resource_keys"]
        ]
        acquired = []
        requeued = False
        renew_stop = Event()
        heartbeat = None
        try:
            for lock in locks:
                if not lock.acquire():
                    self.redis.lpush(self.queue_key, job_id)
                    requeued = True
                    return
                acquired.append(lock)
            started_at = int(time.time() * 1000)
            self.redis.hset(self._job_key(job_id), mapping={
                "status": "running",
                "started_at": str(started_at),
                "lease_until": str(started_at + self.lock_ttl_seconds * 1000),
            })
            heartbeat = Thread(
                target=self._renew_job_lease,
                args=(job_id, acquired, renew_stop),
                name=f"redis-lease-{job_id}",
                daemon=True,
            )
            heartbeat.start()
            result = self.executor(job["action"], job["payload"], job_id, job["operator"])
            self.redis.hset(self._job_key(job_id), mapping={
                "status": "done",
                "result": json.dumps(result, ensure_ascii=False, default=str),
                "finished_at": str(int(time.time() * 1000)),
                "lease_until": "",
            })
        except Exception as exc:
            self.redis.hset(self._job_key(job_id), mapping={
                "status": "failed",
                "error": str(exc),
                "finished_at": str(int(time.time() * 1000)),
                "lease_until": "",
            })
        finally:
            renew_stop.set()
            if heartbeat:
                heartbeat.join(timeout=2)
            for lock in reversed(acquired):
                try:
                    lock.release()
                except Exception:
                    pass
            digest = str(job.get("dedupe_key") or "")
            if not requeued and digest and str(self.redis.get(self._dedupe_key(digest)) or "") == job_id:
                self.redis.delete(self._dedupe_key(digest))

    def _renew_job_lease(self, job_id: str, locks: list[_RedisLease], stop: Event) -> None:
        interval = max(1.0, self.lock_ttl_seconds / 3)
        while not stop.wait(interval):
            try:
                if not all(lock.refresh() for lock in locks):
                    return
                self.redis.hset(
                    self._job_key(job_id),
                    "lease_until",
                    str(int(time.time() * 1000) + self.lock_ttl_seconds * 1000),
                )
            except Exception:
                return

    @contextmanager
    def lock_resources(self, resource_keys: list[str]):
        locks = [
            _RedisLease(
                self.redis,
                self._key(f"lock:{resource}"),
                self.lock_ttl_seconds,
                self.lock_wait_seconds,
            )
            for resource in sorted(set(resource_keys))
        ]
        acquired = []
        try:
            for lock in locks:
                if not lock.acquire():
                    raise TimeoutError("等待记录锁超时，请稍后重试")
                acquired.append(lock)
            yield
        finally:
            for lock in reversed(acquired):
                try:
                    lock.release()
                except Exception:
                    pass

    def get(self, job_id: str) -> dict[str, Any] | None:
        raw = self.redis.hgetall(self._job_key(job_id))
        if not raw:
            return None
        def number(name: str):
            value = raw.get(name) or ""
            return int(value) if value else None
        def json_value(name: str, default):
            value = raw.get(name) or ""
            return json.loads(value) if value else default
        return {
            "id": raw.get("id", job_id),
            "name": raw.get("name", ""),
            "action": raw.get("action", ""),
            "payload": json_value("payload", {}),
            "resource_keys": json_value("resource_keys", []),
            "operator": raw.get("operator", ""),
            "status": raw.get("status", "queued"),
            "result": json_value("result", None),
            "error": raw.get("error") or None,
            "created_at": number("created_at"),
            "started_at": number("started_at"),
            "finished_at": number("finished_at"),
            "dedupe_key": raw.get("dedupe_key", ""),
            "lease_until": number("lease_until"),
        }

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        ids = self.redis.zrange(self.history_key, -max(1, limit), -1)
        return [job for job_id in ids if (job := self.get(str(job_id)))]

    def health(self) -> dict[str, Any]:
        self.redis.ping()
        jobs = self.list(limit=200)
        return {
            "backend": "redis",
            "persistent": True,
            "queued": sum(1 for item in jobs if item["status"] == "queued"),
            "running": sum(1 for item in jobs if item["status"] == "running"),
        }

    def shutdown(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads.clear()
