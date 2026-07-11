import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from api.workflow import PromptConflictError


class PromptApiConflictTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.settings = SimpleNamespace(admin_token="", admin_users_json="")

    def test_missing_revision_requires_refreshable_precondition(self):
        class FakeWorkflow:
            def update_prompt_config(self, *args, **kwargs):
                raise AssertionError("缺少 revision 时不应写入")

        with (
            patch.object(app_module, "settings", self.settings),
            patch.object(app_module, "workflow", FakeWorkflow()),
        ):
            response = self.client.patch("/api/prompts/prompt-1", json={"模板内容": "new"})

        self.assertEqual(response.status_code, 428)
        self.assertIn("刷新", response.get_json()["message"])

    def test_stale_revision_returns_json_conflict(self):
        class FakeWorkflow:
            def update_prompt_config(self, record_id, body, expected_revision=""):
                raise PromptConflictError("Prompt 已被其他人修改，请刷新后重新编辑")

        with (
            patch.object(app_module, "settings", self.settings),
            patch.object(app_module, "workflow", FakeWorkflow()),
        ):
            response = self.client.patch(
                "/api/prompts/prompt-1",
                json={"模板内容": "new", "revision": "old"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("其他人修改", response.get_json()["message"])

    def test_current_revision_is_forwarded_to_workflow(self):
        captured = {}

        class FakeWorkflow:
            def update_prompt_config(self, record_id, body, expected_revision=""):
                captured.update({"record_id": record_id, "body": body, "revision": expected_revision})
                return {"record_id": record_id}

        with (
            patch.object(app_module, "settings", self.settings),
            patch.object(app_module, "workflow", FakeWorkflow()),
        ):
            response = self.client.patch(
                "/api/prompts/prompt-1",
                json={"模板内容": "new", "revision": "current"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["revision"], "current")
        self.assertNotIn("revision", captured["body"])


if __name__ == "__main__":
    unittest.main()
