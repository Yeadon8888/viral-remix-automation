import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RuntimeConfigurationTests(unittest.TestCase):
    def test_gunicorn_keeps_health_and_enqueue_requests_responsive_during_slow_reads(self):
        for relative_path in ("Dockerfile", "Procfile"):
            command = (ROOT / relative_path).read_text(encoding="utf-8")
            with self.subTest(file=relative_path):
                self.assertIn("--worker-class gthread", command)
                self.assertIn("--threads 4", command)
                self.assertIn("--workers 1", command)

    def test_railway_deployment_waits_for_health_and_drains_old_worker(self):
        deploy = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))["deploy"]

        self.assertEqual(deploy["healthcheckPath"], "/healthz")
        self.assertGreaterEqual(deploy["drainingSeconds"], 120)
        self.assertGreaterEqual(deploy["overlapSeconds"], 10)
        self.assertIn("--graceful-timeout 120", (ROOT / "Procfile").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
