import os
import time
import unittest
from uuid import uuid4


class ProductionBackendIntegrationTests(unittest.TestCase):
    def test_postgres_ledger_round_trip(self):
        database_url = os.getenv("INTEGRATION_DATABASE_URL", "")
        if not database_url:
            self.skipTest("INTEGRATION_DATABASE_URL is not configured")

        from api.persistence import PostgresIdempotencyLedger

        ledger = PostgresIdempotencyLedger(database_url)
        suffix = uuid4().hex
        scope = f"integration:{suffix}"
        resource = f"integration:{suffix}"
        try:
            reservation = ledger.reserve(scope, "integration-test")
            self.assertTrue(reservation.acquired)
            ledger.complete(scope, "integration-test", "target-test")
            self.assertEqual(ledger.reserve(scope, "second-owner").target_record_id, "target-test")
            ledger.audit("integration-test", "integration.verify", resource, "done")
            self.assertTrue(ledger.health()["persistent"])
        finally:
            with ledger._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM automation_idempotency WHERE scope = %s", (scope,))
                    cursor.execute("DELETE FROM automation_audit WHERE resource = %s", (resource,))

    def test_redis_queue_round_trip(self):
        redis_url = os.getenv("INTEGRATION_REDIS_URL", "")
        if not redis_url:
            self.skipTest("INTEGRATION_REDIS_URL is not configured")

        import redis
        from api.jobs.coordinator import RedisJobBackend

        namespace = f"integration-{uuid4().hex}"
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        backend = RedisJobBackend(client, lambda action, payload, job_id, operator: payload, namespace=namespace)
        observer = RedisJobBackend(
            client,
            lambda *args: None,
            namespace=namespace,
            start_workers=False,
        )
        try:
            job_id = backend.enqueue(
                "integration-job",
                "integration.verify",
                {"record_id": "integration-record"},
                ["integration:record"],
                "integration-test",
            )
            deadline = time.time() + 10
            while time.time() < deadline:
                job = backend.get(job_id)
                if job and job["status"] == "done":
                    break
                time.sleep(0.05)
            else:
                self.fail("Redis integration job did not complete")
            checkpoint = {
                "record_id": "integration-record",
                "status": "failed",
                "error": "integration failure must remain manual",
            }
            backend.save_item_result(job_id, "integration-record", checkpoint)
            self.assertEqual(
                observer.item_results(job_id),
                {"integration-record": checkpoint},
            )
            self.assertTrue(backend.health()["persistent"])
        finally:
            backend.shutdown()
            observer.shutdown()
            keys = list(client.scan_iter(f"{namespace}:*"))
            if keys:
                client.delete(*keys)


if __name__ == "__main__":
    unittest.main()
