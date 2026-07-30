import threading
import time
import unittest


class RedisJobBackendTests(unittest.TestCase):
    def dependencies(self):
        try:
            import fakeredis
            from api.jobs.coordinator import RedisJobBackend
        except (ImportError, ModuleNotFoundError) as exc:
            self.fail(f"缺少 Redis 持久任务后端或测试依赖: {exc}")
        return fakeredis, RedisJobBackend

    def wait_for(self, backend, job_id, timeout=3):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = backend.get(job_id)
            if job and job["status"] not in {"queued", "running"}:
                return job
            time.sleep(0.02)
        self.fail(f"Redis 任务未完成: {job_id}")

    def test_queued_job_survives_backend_restart(self):
        fakeredis, RedisJobBackend = self.dependencies()
        server = fakeredis.FakeServer()
        redis_one = fakeredis.FakeRedis(server=server, decode_responses=True)
        redis_two = fakeredis.FakeRedis(server=server, decode_responses=True)
        executed = []

        producer = RedisJobBackend(redis_one, lambda *args: None, start_workers=False, namespace="test-persist")
        job_id = producer.enqueue(
            "doubao-generate-prompt:rec-1:MX",
            "doubao.generate_prompt",
            {"record_id": "rec-1", "country_code": "MX"},
            ["doubao:rec-1"],
            "alice",
        )
        producer.shutdown()

        consumer = RedisJobBackend(
            redis_two,
            lambda action, payload, current_job_id, operator: executed.append(payload["record_id"]),
            max_workers=1,
            namespace="test-persist",
        )
        try:
            job = self.wait_for(consumer, job_id)
            self.assertEqual(job["status"], "done")
            self.assertEqual(executed, ["rec-1"])
        finally:
            consumer.shutdown()

    def test_item_checkpoints_survive_backend_restart(self):
        fakeredis, RedisJobBackend = self.dependencies()
        server = fakeredis.FakeServer()
        first = RedisJobBackend(
            fakeredis.FakeRedis(server=server, decode_responses=True),
            lambda *args: None,
            start_workers=False,
            namespace="test-checkpoints",
        )
        second = RedisJobBackend(
            fakeredis.FakeRedis(server=server, decode_responses=True),
            lambda *args: None,
            start_workers=False,
            namespace="test-checkpoints",
        )
        try:
            job_id = first.enqueue(
                "batch",
                "doubao.batch_workflow",
                {"record_ids": ["rec-done", "rec-failed"]},
                ["doubao:rec-done", "doubao:rec-failed"],
                "alice",
            )
            first.save_item_result(job_id, "rec-done", {"record_id": "rec-done", "status": "created"})
            first.save_item_result(
                job_id,
                "rec-failed",
                {"record_id": "rec-failed", "status": "failed", "error": "HTTP 502"},
            )

            self.assertEqual(
                second.item_results(job_id),
                {
                    "rec-done": {"record_id": "rec-done", "status": "created"},
                    "rec-failed": {"record_id": "rec-failed", "status": "failed", "error": "HTTP 502"},
                },
            )
        finally:
            first.shutdown()
            second.shutdown()

    def test_queued_job_can_be_cancelled_without_execution(self):
        fakeredis, RedisJobBackend = self.dependencies()
        executed = []
        backend = RedisJobBackend(
            fakeredis.FakeRedis(decode_responses=True),
            lambda action, payload, job_id, operator: executed.append(job_id),
            start_workers=False,
            namespace="test-cancel-queued",
        )
        try:
            job_id = backend.enqueue(
                "queued",
                "doubao.batch_store_images",
                {"record_ids": ["rec-1"]},
                ["doubao:rec-1"],
                "alice",
            )
            stopped = backend.cancel(job_id)
            self.assertEqual(stopped["status"], "cancelled")
            self.assertTrue(stopped["cancel_requested"])
            self.assertEqual(backend.redis.llen(backend.queue_key), 0)
            backend.start_workers()
            time.sleep(0.1)
            self.assertEqual(executed, [])
        finally:
            backend.shutdown()

    def test_running_job_honors_cooperative_cancel_request(self):
        fakeredis, RedisJobBackend = self.dependencies()
        started = threading.Event()
        backend = None

        def execute(action, payload, job_id, operator):
            started.set()
            deadline = time.time() + 1
            while time.time() < deadline and not backend.is_cancel_requested(job_id):
                time.sleep(0.01)
            return {"processed": 1}

        backend = RedisJobBackend(
            fakeredis.FakeRedis(decode_responses=True),
            execute,
            max_workers=1,
            namespace="test-cancel-running",
        )
        try:
            job_id = backend.enqueue(
                "running",
                "doubao.batch_store_images",
                {"record_ids": ["rec-1"]},
                ["doubao:rec-1"],
                "alice",
            )
            self.assertTrue(started.wait(1))
            requested = backend.cancel(job_id)
            self.assertEqual(requested["status"], "running")
            self.assertTrue(requested["cancel_requested"])
            stopped = self.wait_for(backend, job_id)
            self.assertEqual(stopped["status"], "cancelled")
            self.assertEqual(stopped["result"], {"processed": 1})
        finally:
            backend.shutdown()

    def test_worker_survives_transient_redis_read_timeout_and_executes_job_once(self):
        fakeredis, RedisJobBackend = self.dependencies()
        from redis.exceptions import TimeoutError as RedisTimeoutError

        redis_client = fakeredis.FakeRedis(decode_responses=True)
        original_brpoplpush = redis_client.brpoplpush
        timeout_injected = threading.Event()
        executed = []

        def flaky_brpoplpush(*args, **kwargs):
            if not timeout_injected.is_set():
                timeout_injected.set()
                raise RedisTimeoutError("Timeout reading from socket")
            return original_brpoplpush(*args, **kwargs)

        redis_client.brpoplpush = flaky_brpoplpush
        backend = RedisJobBackend(
            redis_client,
            lambda action, payload, current_job_id, operator: executed.append(current_job_id),
            max_workers=1,
            namespace="test-read-timeout",
        )
        try:
            self.assertTrue(timeout_injected.wait(1))
            job_id = backend.enqueue(
                "doubao-batch-store-images",
                "doubao.batch_store_images",
                {"record_ids": ["rec-timeout"], "limit": 50},
                ["doubao:rec-timeout"],
                "alice",
            )

            job = self.wait_for(backend, job_id, timeout=1)
            self.assertEqual(job["status"], "done")
            self.assertEqual(executed, [job_id])
            health = backend.health()
            self.assertEqual(health["worker_error_count"], 1)
            self.assertIn("Timeout reading from socket", health["last_worker_error"])
            self.assertEqual(health["alive_workers"], 1)
        finally:
            backend.shutdown()

    def test_health_reports_expected_and_alive_queue_workers(self):
        fakeredis, RedisJobBackend = self.dependencies()
        backend = RedisJobBackend(
            fakeredis.FakeRedis(decode_responses=True),
            lambda *args: None,
            max_workers=2,
            namespace="test-worker-health",
        )
        try:
            health = backend.health()
            self.assertEqual(health["expected_workers"], 2)
            self.assertEqual(health["alive_workers"], 2)
            self.assertTrue(health["recovery_worker_alive"])
        finally:
            backend.shutdown()

    def test_worker_continues_after_transient_processing_cleanup_timeout(self):
        fakeredis, RedisJobBackend = self.dependencies()
        from redis.exceptions import TimeoutError as RedisTimeoutError

        redis_client = fakeredis.FakeRedis(decode_responses=True)
        original_lrem = redis_client.lrem
        cleanup_timeout_injected = threading.Event()
        executed = []

        def flaky_lrem(name, count, value):
            if str(name).endswith("jobs:processing") and not cleanup_timeout_injected.is_set():
                cleanup_timeout_injected.set()
                raise RedisTimeoutError("Timeout cleaning processing queue")
            return original_lrem(name, count, value)

        redis_client.lrem = flaky_lrem
        backend = RedisJobBackend(
            redis_client,
            lambda action, payload, current_job_id, operator: executed.append(payload["record_id"]),
            max_workers=1,
            namespace="test-cleanup-timeout",
        )
        try:
            first = backend.enqueue(
                "job:first",
                "test.execute",
                {"record_id": "rec-first"},
                ["record:rec-first"],
                "alice",
            )
            self.assertEqual(self.wait_for(backend, first)["status"], "done")
            self.assertTrue(cleanup_timeout_injected.wait(1))

            second = backend.enqueue(
                "job:second",
                "test.execute",
                {"record_id": "rec-second"},
                ["record:rec-second"],
                "alice",
            )
            self.assertEqual(self.wait_for(backend, second, timeout=1)["status"], "done")
            self.assertEqual(executed, ["rec-first", "rec-second"])
        finally:
            backend.shutdown()

    def test_two_backends_share_distributed_record_lock(self):
        fakeredis, RedisJobBackend = self.dependencies()
        server = fakeredis.FakeServer()
        active = 0
        max_active = 0
        state_lock = threading.Lock()

        def execute(action, payload, job_id, operator):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.08)
            with state_lock:
                active -= 1
            return payload

        first = RedisJobBackend(
            fakeredis.FakeRedis(server=server, decode_responses=True),
            execute,
            max_workers=1,
            namespace="test-lock",
        )
        second = RedisJobBackend(
            fakeredis.FakeRedis(server=server, decode_responses=True),
            execute,
            max_workers=1,
            namespace="test-lock",
        )
        try:
            my_job = first.enqueue(
                "doubao-generate-prompt:rec-1:MY",
                "doubao.generate_prompt",
                {"record_id": "rec-1", "country_code": "MY"},
                ["doubao:rec-1"],
                "alice",
            )
            mx_job = second.enqueue(
                "doubao-generate-prompt:rec-1:MX",
                "doubao.generate_prompt",
                {"record_id": "rec-1", "country_code": "MX"},
                ["doubao:rec-1"],
                "bob",
            )
            self.wait_for(first, my_job)
            self.wait_for(second, mx_job)
            self.assertEqual(max_active, 1)
        finally:
            first.shutdown()
            second.shutdown()

    def test_expired_processing_job_is_requeued_after_worker_restart(self):
        fakeredis, RedisJobBackend = self.dependencies()
        server = fakeredis.FakeServer()
        redis_one = fakeredis.FakeRedis(server=server, decode_responses=True)
        executed = []
        producer = RedisJobBackend(redis_one, lambda *args: None, start_workers=False, namespace="test-recover")
        job_id = producer.enqueue(
            "doubao-generate-prompt:rec-2:ID",
            "doubao.generate_prompt",
            {"record_id": "rec-2", "country_code": "ID"},
            ["doubao:rec-2"],
            "alice",
        )
        redis_one.rpoplpush(producer.queue_key, producer.processing_key)
        redis_one.hset(producer._job_key(job_id), mapping={
            "status": "running",
            "lease_until": "1",
        })

        consumer = RedisJobBackend(
            fakeredis.FakeRedis(server=server, decode_responses=True),
            lambda action, payload, current_job_id, operator: executed.append(payload["record_id"]),
            max_workers=1,
            namespace="test-recover",
        )
        try:
            job = self.wait_for(consumer, job_id)
            self.assertEqual(job["status"], "done")
            self.assertEqual(executed, ["rec-2"])
        finally:
            consumer.shutdown()

    def test_expired_running_job_missing_from_processing_is_requeued(self):
        fakeredis, RedisJobBackend = self.dependencies()
        server = fakeredis.FakeServer()
        redis_one = fakeredis.FakeRedis(server=server, decode_responses=True)
        executed = []
        producer = RedisJobBackend(
            redis_one,
            lambda *args: None,
            start_workers=False,
            namespace="test-orphan-recover",
        )
        job_id = producer.enqueue(
            "doubao-batch-store-images",
            "doubao.batch_store_images",
            {"record_ids": ["rec-orphan"], "limit": 50},
            ["doubao:rec-orphan"],
            "alice",
        )
        redis_one.lrem(producer.queue_key, 0, job_id)
        redis_one.hset(producer._job_key(job_id), mapping={
            "status": "running",
            "started_at": "1",
            "lease_until": "1",
        })

        consumer = RedisJobBackend(
            fakeredis.FakeRedis(server=server, decode_responses=True),
            lambda action, payload, current_job_id, operator: executed.extend(payload["record_ids"]),
            max_workers=1,
            namespace="test-orphan-recover",
        )
        try:
            job = self.wait_for(consumer, job_id)
            self.assertEqual(job["status"], "done")
            self.assertEqual(executed, ["rec-orphan"])
        finally:
            consumer.shutdown()

    def test_expired_processing_job_is_recovered_without_another_restart(self):
        fakeredis, RedisJobBackend = self.dependencies()
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        executed = []
        backend = RedisJobBackend(
            redis_client,
            lambda action, payload, current_job_id, operator: executed.append(payload["record_id"]),
            max_workers=1,
            namespace="test-periodic-recover",
            recovery_interval_seconds=0.05,
            start_workers=False,
        )
        job_id = backend.enqueue(
            "doubao-generate-prompt:rec-periodic:MY",
            "doubao.generate_prompt",
            {"record_id": "rec-periodic", "country_code": "MY"},
            ["doubao:rec-periodic"],
            "alice",
        )
        redis_client.rpoplpush(backend.queue_key, backend.processing_key)
        redis_client.hset(backend._job_key(job_id), mapping={
            "status": "running",
            "lease_until": str(int(time.time() * 1000) + 60_000),
        })
        backend.start_workers()
        redis_client.hset(backend._job_key(job_id), "lease_until", "1")

        try:
            job = self.wait_for(backend, job_id)
            self.assertEqual(job["status"], "done")
            self.assertEqual(executed, ["rec-periodic"])
        finally:
            backend.shutdown()

    def test_enqueue_race_reuses_dedupe_owner_before_job_hash_exists(self):
        fakeredis, RedisJobBackend = self.dependencies()
        server = fakeredis.FakeServer()
        redis_one = fakeredis.FakeRedis(server=server, decode_responses=True)
        redis_two = fakeredis.FakeRedis(server=server, decode_responses=True)
        reached_hset = threading.Event()
        release_hset = threading.Event()
        original_hset = redis_one.hset

        def delayed_hset(name, *args, **kwargs):
            if ":job:" in str(name):
                reached_hset.set()
                release_hset.wait(2)
            return original_hset(name, *args, **kwargs)

        redis_one.hset = delayed_hset
        first = RedisJobBackend(redis_one, lambda *args: None, start_workers=False, namespace="test-race")
        second = RedisJobBackend(redis_two, lambda *args: None, start_workers=False, namespace="test-race")
        result = {}

        def enqueue_first():
            result["first"] = first.enqueue(
                "doubao-generate-prompt:rec-3:MX",
                "doubao.generate_prompt",
                {"record_id": "rec-3", "country_code": "MX"},
                ["doubao:rec-3"],
                "alice",
            )

        thread = threading.Thread(target=enqueue_first)
        thread.start()
        self.assertTrue(reached_hset.wait(1))
        result["second"] = second.enqueue(
            "doubao-generate-prompt:rec-3:MX",
            "doubao.generate_prompt",
            {"record_id": "rec-3", "country_code": "MX"},
            ["doubao:rec-3"],
            "bob",
        )
        release_hset.set()
        thread.join(2)
        self.assertEqual(result["first"], result["second"])

    def test_lock_contention_keeps_dedupe_while_job_is_requeued(self):
        fakeredis, RedisJobBackend = self.dependencies()
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        backend = RedisJobBackend(
            redis_client,
            lambda action, payload, job_id, operator: payload,
            max_workers=1,
            namespace="test-requeue-dedupe",
            lock_wait_seconds=1,
        )
        try:
            with backend.lock_resources(["doubao:rec-4"]):
                first = backend.enqueue(
                    "doubao-generate-prompt:rec-4:MX",
                    "doubao.generate_prompt",
                    {"record_id": "rec-4", "country_code": "MX"},
                    ["doubao:rec-4"],
                    "alice",
                )
                time.sleep(1.2)
                second = backend.enqueue(
                    "doubao-generate-prompt:rec-4:MX",
                    "doubao.generate_prompt",
                    {"record_id": "rec-4", "country_code": "MX"},
                    ["doubao:rec-4"],
                    "bob",
                )
                self.assertEqual(first, second)
            self.assertEqual(self.wait_for(backend, first)["status"], "done")
        finally:
            backend.shutdown()

    def test_redis_lease_can_be_refreshed_for_long_jobs(self):
        import fakeredis
        from api.jobs.coordinator import _RedisLease

        redis_client = fakeredis.FakeRedis(decode_responses=True)
        lease = _RedisLease(redis_client, "test:lease", ttl_seconds=2, wait_seconds=1)
        self.assertTrue(lease.acquire())
        time.sleep(1.1)
        self.assertTrue(lease.refresh())
        time.sleep(1.1)
        self.assertEqual(redis_client.get("test:lease"), lease.token)
        lease.release()


if __name__ == "__main__":
    unittest.main()
