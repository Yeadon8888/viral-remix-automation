import unittest


class FakeLedger:
    def __init__(self):
        self.events = []

    def audit(self, operator, action, resource, status, detail=""):
        self.events.append((operator, action, resource, status, detail))


class JobAuditTests(unittest.TestCase):
    def test_successful_job_records_completion(self):
        from api.jobs import audited_executor

        ledger = FakeLedger()
        result = audited_executor(
            lambda action, payload, job_id, operator: {"ok": True},
            ledger,
            "doubao.generate_prompt",
            {"record_id": "rec-1"},
            "job-1",
            "alice",
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(ledger.events[-1][3], "done")
        self.assertEqual(ledger.events[-1][2], "job-1")

    def test_failed_job_records_error_and_reraises(self):
        from api.jobs import audited_executor

        ledger = FakeLedger()

        def fail(*args):
            raise ValueError("boom")

        with self.assertRaisesRegex(ValueError, "boom"):
            audited_executor(fail, ledger, "task.generate_video", {}, "job-2", "bob")

        self.assertEqual(ledger.events[-1][3], "failed")
        self.assertIn("boom", ledger.events[-1][4])


if __name__ == "__main__":
    unittest.main()
