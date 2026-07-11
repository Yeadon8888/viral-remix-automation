import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module


class PersistentJobRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_write_routes_enqueue_serializable_actions(self):
        cases = [
            ("/api/doubao-items/rec-1/generate-prompt", {"country_code": "MX"}, "doubao.generate_prompt"),
            ("/api/doubao-items/rec-1/sync-part-time", {"country_code": "MX"}, "doubao.sync_part_time"),
            ("/api/doubao-items/rec-1/store-image", {}, "doubao.store_image"),
            ("/api/doubao-items/batch-store-images", {"record_ids": ["rec-1"], "limit": 50}, "doubao.batch_store_images"),
            ("/api/doubao-items/batch-generate-prompts", {"record_ids": ["rec-1"], "country_code": "MX"}, "doubao.batch_generate_prompts"),
            ("/api/doubao-items/batch-sync-part-time", {"record_ids": ["rec-1"], "country_code": "MX"}, "doubao.batch_sync_part_time"),
            ("/api/materials/mat-1/prepare", {}, "material.prepare"),
            ("/api/materials/mat-1/analyze-video", {}, "material.analyze_video"),
            ("/api/materials/mat-1/store-image", {}, "material.store_image"),
            ("/api/materials/mat-1/generate-images", {}, "material.generate_images"),
            ("/api/materials/mat-1/create-tasks", {}, "material.create_tasks"),
            ("/api/materials/mat-1/retry-failed-step", {}, "material.retry_failed_step"),
            ("/api/materials/batch-step", {"record_ids": ["mat-1"], "step": "create-tasks", "limit": 50}, "material.batch_step"),
            ("/api/materials/batch-prepare", {"record_ids": ["mat-1"], "limit": 5}, "material.batch_prepare"),
            ("/api/tasks/task-1/generate-video", {}, "task.generate_video"),
            ("/api/tasks/task-1/sync-reference-image", {}, "task.sync_reference_image"),
            ("/api/tasks/task-1/store-manual-video", {}, "task.store_manual_video"),
            ("/api/tasks/batch-generate-video", {"record_ids": ["task-1"], "limit": 3}, "task.batch_generate_video"),
            ("/api/tasks/batch-sync-reference-images", {"record_ids": ["task-1"], "limit": 100}, "task.batch_sync_reference_images"),
            ("/api/tasks/batch-store-manual-videos", {"record_ids": ["task-1"], "limit": 100}, "task.batch_store_manual_videos"),
            ("/api/tasks/sync-publish", {"record_ids": ["task-1"], "limit": 100}, "publish.sync"),
        ]
        settings = SimpleNamespace(admin_token="", admin_users_json="")

        for path, body, expected_action in cases:
            captured = {}

            def enqueue(name, action, payload):
                captured.update({"name": name, "action": action, "payload": payload})
                return "job-1"

            with (
                self.subTest(path=path),
                patch.object(app_module, "settings", settings),
                patch.object(app_module, "enqueue_workflow_action", enqueue),
            ):
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                self.assertEqual(captured.get("action"), expected_action)
                self.assertEqual(response.get_json()["job_id"], "job-1")


if __name__ == "__main__":
    unittest.main()
