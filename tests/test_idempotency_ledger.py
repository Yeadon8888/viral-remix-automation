import threading
import unittest


class IdempotencyLedgerTests(unittest.TestCase):
    def ledger_class(self):
        try:
            from api.persistence import InMemoryIdempotencyLedger
        except (ImportError, ModuleNotFoundError) as exc:
            self.fail(f"缺少幂等台账: {exc}")
        return InMemoryIdempotencyLedger

    def test_only_one_owner_can_reserve_the_same_business_key(self):
        Ledger = self.ledger_class()
        ledger = Ledger()
        barrier = threading.Barrier(2)
        results = []

        def reserve(owner):
            barrier.wait()
            results.append(ledger.reserve("part-time:source-1:MX:西班牙语", owner))

        threads = [threading.Thread(target=reserve, args=(owner,)) for owner in ("job-a", "job-b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)

        self.assertEqual(sum(1 for item in results if item.acquired), 1)
        self.assertEqual(sum(1 for item in results if not item.acquired), 1)

    def test_completed_reservation_returns_existing_target(self):
        Ledger = self.ledger_class()
        ledger = Ledger()
        first = ledger.reserve("part-time:source-1:MX:西班牙语", "job-a")
        self.assertTrue(first.acquired)
        ledger.complete("part-time:source-1:MX:西班牙语", "job-a", "target-1")

        second = ledger.reserve("part-time:source-1:MX:西班牙语", "job-b")

        self.assertFalse(second.acquired)
        self.assertEqual(second.target_record_id, "target-1")

    def test_postgres_adapter_is_available_for_production(self):
        try:
            from api.persistence import PostgresIdempotencyLedger
        except ImportError as exc:
            self.fail(f"缺少 PostgreSQL 幂等台账适配器: {exc}")

        self.assertTrue(callable(PostgresIdempotencyLedger))


if __name__ == "__main__":
    unittest.main()
