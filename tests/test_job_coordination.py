import threading
import time
import unittest


class JobCoordinationTests(unittest.TestCase):
    def backend_class(self):
        try:
            from api.jobs.coordinator import MemoryJobBackend
        except (ImportError, ModuleNotFoundError) as exc:
            self.fail(f"缺少持久任务协调器接口: {exc}")
        return MemoryJobBackend

    def wait_for(self, backend, job_id, timeout=2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = backend.get(job_id)
            if job and job["status"] not in {"queued", "running"}:
                return job
            time.sleep(0.01)
        self.fail(f"任务未在 {timeout}s 内完成: {job_id}")

    def test_exact_duplicate_request_reuses_running_job(self):
        MemoryJobBackend = self.backend_class()
        started = threading.Event()
        release = threading.Event()

        def execute(action, payload, job_id, operator):
            started.set()
            release.wait(1)
            return payload

        backend = MemoryJobBackend(execute, max_workers=1)
        try:
            first = backend.enqueue(
                "doubao-batch-prompts:MX",
                "doubao.batch_generate_prompts",
                {"record_ids": ["rec-1"], "country_code": "MX"},
                ["doubao:rec-1"],
                "alice",
            )
            self.assertTrue(started.wait(1))
            second = backend.enqueue(
                "doubao-batch-prompts:MX",
                "doubao.batch_generate_prompts",
                {"record_ids": ["rec-1"], "country_code": "MX"},
                ["doubao:rec-1"],
                "alice",
            )

            self.assertEqual(first, second)
        finally:
            release.set()
            self.wait_for(backend, first)
            backend.shutdown()

    def test_different_batch_payloads_do_not_drop_second_selection(self):
        MemoryJobBackend = self.backend_class()
        executed = []
        lock = threading.Lock()

        def execute(action, payload, job_id, operator):
            with lock:
                executed.append(tuple(payload["record_ids"]))
            return payload

        backend = MemoryJobBackend(execute, max_workers=2)
        try:
            first = backend.enqueue(
                "doubao-batch-prompts:MX",
                "doubao.batch_generate_prompts",
                {"record_ids": ["rec-1"], "country_code": "MX"},
                ["doubao:rec-1"],
                "alice",
            )
            second = backend.enqueue(
                "doubao-batch-prompts:MX",
                "doubao.batch_generate_prompts",
                {"record_ids": ["rec-2"], "country_code": "MX"},
                ["doubao:rec-2"],
                "bob",
            )

            self.assertNotEqual(first, second)
            self.wait_for(backend, first)
            self.wait_for(backend, second)
            self.assertCountEqual(executed, [("rec-1",), ("rec-2",)])
        finally:
            backend.shutdown()

    def test_same_record_is_serialized_across_different_actions(self):
        MemoryJobBackend = self.backend_class()
        active = 0
        max_active = 0
        lock = threading.Lock()

        def execute(action, payload, job_id, operator):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return action

        backend = MemoryJobBackend(execute, max_workers=2)
        try:
            my_job = backend.enqueue(
                "doubao-generate-prompt:rec-1:MY",
                "doubao.generate_prompt",
                {"record_id": "rec-1", "country_code": "MY"},
                ["doubao:rec-1"],
                "alice",
            )
            mx_job = backend.enqueue(
                "doubao-generate-prompt:rec-1:MX",
                "doubao.generate_prompt",
                {"record_id": "rec-1", "country_code": "MX"},
                ["doubao:rec-1"],
                "bob",
            )
            self.wait_for(backend, my_job)
            self.wait_for(backend, mx_job)

            self.assertEqual(max_active, 1)
        finally:
            backend.shutdown()

    def test_worker_concurrency_is_bounded(self):
        MemoryJobBackend = self.backend_class()
        active = 0
        max_active = 0
        lock = threading.Lock()

        def execute(action, payload, job_id, operator):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return payload

        backend = MemoryJobBackend(execute, max_workers=2)
        try:
            jobs = [
                backend.enqueue(
                    f"job:{index}",
                    "test.execute",
                    {"index": index},
                    [f"record:{index}"],
                    "alice",
                )
                for index in range(6)
            ]
            for job_id in jobs:
                self.wait_for(backend, job_id)

            self.assertLessEqual(max_active, 2)
            self.assertEqual(len(backend.list(limit=20)), 6)
        finally:
            backend.shutdown()

    def test_synchronous_writes_can_use_the_same_resource_lock(self):
        MemoryJobBackend = self.backend_class()
        backend = MemoryJobBackend(lambda *args: None, max_workers=1)
        active = 0
        max_active = 0
        state_lock = threading.Lock()

        def write():
            nonlocal active, max_active
            with backend.lock_resources(["prompt:rec-1"]):
                with state_lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.05)
                with state_lock:
                    active -= 1

        threads = [threading.Thread(target=write) for _ in range(2)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(max_active, 1)
        finally:
            backend.shutdown()

    def test_queued_job_can_be_cancelled_before_execution(self):
        MemoryJobBackend = self.backend_class()
        first_started = threading.Event()
        release_first = threading.Event()
        executed = []

        def execute(action, payload, job_id, operator):
            executed.append(payload["record_id"])
            if payload["record_id"] == "rec-running":
                first_started.set()
                release_first.wait(1)
            return payload

        backend = MemoryJobBackend(execute, max_workers=1)
        try:
            running_id = backend.enqueue(
                "running",
                "test.execute",
                {"record_id": "rec-running"},
                ["record:running"],
                "alice",
            )
            self.assertTrue(first_started.wait(1))
            queued_id = backend.enqueue(
                "queued",
                "test.execute",
                {"record_id": "rec-queued"},
                ["record:queued"],
                "alice",
            )

            cancelled = backend.cancel(queued_id)
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertTrue(cancelled["cancel_requested"])
            release_first.set()
            self.wait_for(backend, running_id)
            self.assertEqual(self.wait_for(backend, queued_id)["status"], "cancelled")
            self.assertEqual(executed, ["rec-running"])
        finally:
            release_first.set()
            backend.shutdown()

    def test_running_job_finishes_as_cancelled_after_cooperative_stop(self):
        MemoryJobBackend = self.backend_class()
        started = threading.Event()
        backend = None

        def execute(action, payload, job_id, operator):
            started.set()
            deadline = time.time() + 1
            while time.time() < deadline and not backend.is_cancel_requested(job_id):
                time.sleep(0.01)
            return {"processed": 1}

        backend = MemoryJobBackend(execute, max_workers=1)
        try:
            job_id = backend.enqueue(
                "running",
                "test.execute",
                {"record_id": "rec-running"},
                ["record:running"],
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


if __name__ == "__main__":
    unittest.main()
