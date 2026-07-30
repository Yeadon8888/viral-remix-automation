import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_healthz_is_public_even_when_admin_token_is_configured(self):
        configured = SimpleNamespace(admin_token="secret", admin_users_json="")
        with patch.object(app_module, "settings", configured):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_api_health_reports_coordination_and_persistence(self):
        configured = SimpleNamespace(
            admin_token="",
            admin_users_json="",
            missing_required=lambda: [],
        )
        persistence = SimpleNamespace(health=lambda: {"backend": "postgres", "persistent": True})
        with (
            patch.object(app_module, "settings", configured),
            patch.object(app_module, "job_health", return_value={"backend": "redis", "persistent": True}),
            patch.object(app_module, "get_persistence", return_value=persistence),
        ):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["coordination"]["backend"], "redis")
        self.assertEqual(data["persistence"]["backend"], "postgres")

    def test_healthz_is_unavailable_when_queue_workers_are_dead(self):
        configured = SimpleNamespace(admin_token="", admin_users_json="")
        persistence = SimpleNamespace(health=lambda: {"backend": "postgres", "persistent": True})
        coordination = {
            "backend": "redis",
            "persistent": True,
            "queued": 3,
            "running": 0,
            "expected_workers": 2,
            "alive_workers": 0,
        }
        with (
            patch.object(app_module, "settings", configured),
            patch.object(app_module, "job_health", return_value=coordination),
            patch.object(app_module, "get_persistence", return_value=persistence),
        ):
            response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
