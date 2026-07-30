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
            ("/api/doubao-items/batch-sync-publish", {"record_ids": ["rec-1"], "limit": 200}, "publish.sync_doubao"),
            ("/api/part-time-items/batch-sync-publish", {"record_ids": ["part-1"], "limit": 200}, "publish.sync_part_time"),
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

        class ReadyWorkflow:
            def doubao_publish_readiness(self, record_ids):
                return {
                    "selected_sources": len(record_ids),
                    "ready_sources": len(record_ids),
                    "eligible_videos": len(record_ids),
                    "pending_sources": 0,
                }

        for path, body, expected_action in cases:
            captured = {}

            def enqueue(name, action, payload):
                captured.update({"name": name, "action": action, "payload": payload})
                return "job-1"

            with (
                self.subTest(path=path),
                patch.object(app_module, "settings", settings),
                patch.object(app_module, "enqueue_workflow_action", enqueue),
                patch.object(app_module, "get_workflow", return_value=ReadyWorkflow()),
            ):
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                self.assertEqual(captured.get("action"), expected_action)
                self.assertEqual(response.get_json()["job_id"], "job-1")

    def test_part_time_items_route_lists_external_plugin_results(self):
        settings = SimpleNamespace(admin_token="", admin_users_json="")
        calls = []

        class ReadyWorkflow:
            def list_part_time_publish_items(self, limit=None, days=0):
                calls.append((limit, days))
                return [{"record_id": "part-1", "任务状态": "已完成"}]

        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "get_workflow", return_value=ReadyWorkflow()),
        ):
            response = self.client.get("/api/part-time-items?limit=100&days=7&force=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"][0]["record_id"], "part-1")
        self.assertEqual(calls, [(None, 7)])

    def test_part_time_publish_route_splits_large_selection_without_loss(self):
        settings = SimpleNamespace(admin_token="", admin_users_json="")
        captured = []

        def enqueue(name, action, payload):
            captured.append({"name": name, "action": action, "payload": payload})
            return f"job-{len(captured)}"

        record_ids = [f"part-{index:03d}" for index in range(440)]
        row_numbers = {record_id: 5000 + index for index, record_id in enumerate(record_ids)}
        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "enqueue_workflow_action", enqueue),
        ):
            response = self.client.post(
                "/api/part-time-items/batch-sync-publish",
                json={
                    "record_ids": record_ids,
                    "row_numbers": row_numbers,
                    "selection_label": "兼职表行号 5000–5439（已选 440 条）",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["job_ids"], ["job-1", "job-2", "job-3"])
        self.assertEqual(body["batch_count"], 3)
        self.assertEqual(body["selected_count"], 440)
        self.assertEqual(body["submitted_count"], 440)
        self.assertEqual([len(item["payload"]["record_ids"]) for item in captured], [200, 200, 40])
        self.assertEqual(
            [record_id for item in captured for record_id in item["payload"]["record_ids"]],
            record_ids,
        )
        self.assertEqual(len(captured[0]["payload"]["row_numbers"]), 200)
        self.assertEqual(len(captured[2]["payload"]["row_numbers"]), 40)
        self.assertIn("第 1/3 批（200 条）", captured[0]["payload"]["selection_label"])
        self.assertIn("第 3/3 批（40 条）", captured[2]["payload"]["selection_label"])

    def test_doubao_publish_sync_rejects_selection_without_completed_video(self):
        settings = SimpleNamespace(admin_token="", admin_users_json="")

        class PendingWorkflow:
            def doubao_publish_readiness(self, record_ids):
                return {
                    "selected_sources": len(record_ids),
                    "ready_sources": 0,
                    "eligible_videos": 0,
                    "pending_sources": len(record_ids),
                    "blocked_face_sources": 1,
                    "missing_product_id_sources": 2,
                    "missing_video_sources": 3,
                }

        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "get_workflow", return_value=PendingWorkflow()),
            patch.object(app_module, "enqueue_workflow_action") as enqueue,
        ):
            response = self.client.post(
                "/api/doubao-items/batch-sync-publish",
                json={"record_ids": ["rec-1"]},
            )

        self.assertEqual(response.status_code, 409)
        message = response.get_json()["message"]
        self.assertIn("图片或人脸未通过 1 条", message)
        self.assertIn("缺少商品 ID 2 条", message)
        self.assertIn("没有已完成有效视频 3 条", message)
        enqueue.assert_not_called()

    def test_unified_doubao_batch_route_maps_face_detection(self):
        settings = SimpleNamespace(admin_token="", admin_users_json="")
        captured = {}

        def enqueue(name, action, payload):
            captured.update({"name": name, "action": action, "payload": payload})
            return "job-face"

        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "enqueue_workflow_action", enqueue),
        ):
            response = self.client.post(
                "/api/doubao-items/batch-operation",
                json={"operation": "detect_faces", "record_ids": ["rec-2", "rec-1", "rec-2"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["name"], "doubao-batch-detect-faces")
        self.assertEqual(captured["action"], "doubao.batch_detect_faces")
        self.assertEqual(captured["payload"]["record_ids"], ["rec-2", "rec-1"])

    def test_unified_doubao_batch_route_maps_full_workflow(self):
        settings = SimpleNamespace(admin_token="", admin_users_json="")
        captured = {}

        def enqueue(name, action, payload):
            captured.update({"name": name, "action": action, "payload": payload})
            return "job-workflow"

        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "enqueue_workflow_action", enqueue),
        ):
            response = self.client.post(
                "/api/doubao-items/batch-operation",
                json={
                    "operation": "process_workflow",
                    "record_ids": ["rec-2", "rec-1"],
                    "row_numbers": {"rec-2": 20, "rec-1": 10, "other": 99},
                    "country_code": "PH",
                    "force_update": True,
                    "selection_label": "豆包素材表行号 10–20",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["name"], "doubao-batch-workflow")
        self.assertEqual(captured["action"], "doubao.batch_workflow")
        self.assertEqual(captured["payload"]["record_ids"], ["rec-2", "rec-1"])
        self.assertEqual(captured["payload"]["row_numbers"], {"rec-2": 20, "rec-1": 10})
        self.assertEqual(captured["payload"]["country_code"], "PH")
        self.assertIs(captured["payload"]["force_update"], True)

    def test_unified_doubao_publish_creates_auditable_job_when_no_video_is_ready(self):
        settings = SimpleNamespace(admin_token="", admin_users_json="")
        captured = {}

        class PendingWorkflow:
            def doubao_publish_readiness(self, record_ids):
                return {
                    "selected_sources": len(record_ids),
                    "ready_sources": 0,
                    "eligible_videos": 0,
                    "pending_sources": len(record_ids),
                }

        def enqueue(name, action, payload):
            captured.update({"name": name, "action": action, "payload": payload})
            return "job-publish-check"

        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "get_workflow", return_value=PendingWorkflow()),
            patch.object(app_module, "enqueue_workflow_action", enqueue),
        ):
            response = self.client.post(
                "/api/doubao-items/batch-operation",
                json={
                    "operation": "sync_publish",
                    "record_ids": ["rec-2", "rec-1"],
                    "row_numbers": {"rec-2": 4934, "rec-1": 4933},
                    "selection_label": "豆包素材表行号 4933–4934（已选 2 条）",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["readiness"]["eligible_videos"], 0)
        self.assertEqual(captured["action"], "publish.sync_doubao")
        self.assertEqual(captured["payload"]["row_numbers"], {"rec-2": 4934, "rec-1": 4933})

    def test_unified_doubao_batch_route_splits_large_selection_without_loss(self):
        settings = SimpleNamespace(admin_token="", admin_users_json="")
        captured = []

        def enqueue(name, action, payload):
            captured.append({"name": name, "action": action, "payload": payload})
            return f"job-{len(captured)}"

        record_ids = [f"rec-{index:03d}" for index in range(400)]
        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "enqueue_workflow_action", enqueue),
        ):
            response = self.client.post(
                "/api/doubao-items/batch-operation",
                json={
                    "operation": "process_workflow",
                    "record_ids": record_ids,
                    "country_code": "PH",
                    "selection_label": "豆包素材表行号 5485–5884（已选 400 条）",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["job_ids"], ["job-1", "job-2"])
        self.assertEqual(body["batch_count"], 2)
        self.assertEqual(body["selected_count"], 400)
        self.assertEqual(body["submitted_count"], 400)
        self.assertEqual([len(item["payload"]["record_ids"]) for item in captured], [200, 200])
        self.assertTrue(body["batch_group_id"])
        self.assertEqual(
            {item["payload"]["batch_group_id"] for item in captured},
            {body["batch_group_id"]},
        )
        self.assertEqual(
            [record_id for item in captured for record_id in item["payload"]["record_ids"]],
            record_ids,
        )
        self.assertIn("第 1/2 批（200 条）", captured[0]["payload"]["selection_label"])
        self.assertIn("第 2/2 批（200 条）", captured[1]["payload"]["selection_label"])

    def test_unified_doubao_batch_route_rejects_selection_above_safe_limit(self):
        settings = SimpleNamespace(admin_token="", admin_users_json="")
        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "enqueue_workflow_action") as enqueue,
        ):
            response = self.client.post(
                "/api/doubao-items/batch-operation",
                json={
                    "operation": "process_workflow",
                    "record_ids": [f"rec-{index:04d}" for index in range(1001)],
                    "country_code": "PH",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["selection_limit"], 1000)
        enqueue.assert_not_called()

    def test_unified_doubao_batch_route_maps_flagged_record_deletion(self):
        settings = SimpleNamespace(admin_token="", admin_users_json="")
        captured = {}

        def enqueue(name, action, payload):
            captured.update({"name": name, "action": action, "payload": payload})
            return "job-delete"

        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "enqueue_workflow_action", enqueue),
        ):
            response = self.client.post(
                "/api/doubao-items/batch-operation",
                json={"operation": "delete_face_records", "record_ids": ["rec-2", "rec-1"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["name"], "doubao-batch-delete-face-records")
        self.assertEqual(captured["action"], "doubao.batch_delete_face_records")
        self.assertEqual(captured["payload"]["record_ids"], ["rec-2", "rec-1"])

    def test_part_time_batch_preserves_explicit_force_update(self):
        settings = SimpleNamespace(admin_token="", admin_users_json="")
        captured = {}

        def enqueue(name, action, payload):
            captured.update({"action": action, "payload": payload})
            return "job-1"

        with (
            patch.object(app_module, "settings", settings),
            patch.object(app_module, "enqueue_workflow_action", enqueue),
        ):
            response = self.client.post(
                "/api/doubao-items/batch-sync-part-time",
                json={"record_ids": ["rec-1"], "country_code": "MX", "force_update": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["action"], "doubao.batch_sync_part_time")
        self.assertIs(captured["payload"]["force_update"], True)

    def test_job_list_returns_compact_failure_summary(self):
        jobs = [{
            "id": "job-1",
            "name": "doubao-batch-store-images",
            "action": "doubao.batch_store_images",
            "payload": {
                "record_ids": ["rec-1", "rec-2", "rec-3"],
                "selection_label": "豆包素材表 4932–5250",
                "country_code": "PH",
            },
            "resource_keys": ["doubao:rec-1", "doubao:rec-2", "doubao:rec-3"],
            "status": "done",
            "result": {
                "processed": 3,
                "failed": 0,
                "results": [
                    {"record_id": "rec-1", "stored_image_url": "https://assets.example/1.png"},
                    {
                        "record_id": "rec-2",
                        "row_number": 4933,
                        "source_table": "豆包素材表",
                        "error": "RecordIdNotFound",
                    },
                    {
                        "record_id": "rec-3",
                        "status": "conflict",
                        "message": "目标兼职任务状态为已完成，已保护现有任务，未覆盖",
                    },
                ],
            },
        }]

        with patch.object(app_module, "list_jobs", return_value=jobs) as list_mock:
            response = self.client.get("/api/jobs?limit=20")

        self.assertEqual(response.status_code, 200)
        list_mock.assert_called_once_with(limit=20)
        item = response.get_json()["data"][0]
        self.assertNotIn("payload", item)
        self.assertNotIn("resource_keys", item)
        self.assertNotIn("results", item["result"])
        self.assertEqual(item["outcome"], "partial_failed")
        self.assertEqual(item["target"], {
            "label": "豆包素材表 4932–5250",
            "record_count": 3,
            "country_code": "PH",
        })
        self.assertEqual(item["result"]["failed"], 1)
        self.assertEqual(item["result"]["conflicts"], 1)
        self.assertEqual(item["result"]["failure_items_total"], 1)
        self.assertEqual(item["result"]["conflict_items_total"], 1)
        self.assertEqual(
            [failure["record_id"] for failure in item["result"]["failures"]],
            ["rec-2"],
        )
        self.assertEqual(item["result"]["failures"][0]["row_number"], 4933)
        self.assertEqual(item["result"]["failures"][0]["source_table"], "豆包素材表")
        self.assertEqual(
            item["result"]["conflict_items"][0]["message"],
            "目标兼职任务状态为已完成，已保护现有任务，未覆盖",
        )

    def test_retry_job_failures_enqueues_only_retryable_failed_records(self):
        job = {
            "id": "job-1",
            "name": "doubao-batch-generate-prompts",
            "action": "doubao.batch_generate_prompts",
            "status": "done",
            "payload": {
                "record_ids": ["rec-1", "rec-2", "rec-3"],
                "country_code": "PH",
                "selection_label": "豆包素材表 4932–5250",
            },
            "result": {
                "processed": 3,
                "failed": 2,
                "results": [
                    {"record_id": "rec-1", "error": "Data not ready"},
                    {"record_id": "rec-2", "status": "failed", "message": "Gemini HTTP 502"},
                    {"record_id": "rec-3", "status": "conflict", "message": "已保护现有任务"},
                ],
            },
        }
        captured = {}

        def enqueue(name, action, payload):
            captured.update({"name": name, "action": action, "payload": payload})
            return "job-retry"

        with (
            patch.object(app_module, "settings", SimpleNamespace(admin_token="", admin_users_json="")),
            patch.object(app_module, "get_job", return_value=job),
            patch.object(app_module, "enqueue_workflow_action", enqueue),
        ):
            response = self.client.post("/api/jobs/job-1/retry-failures", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["job_id"], "job-retry")
        self.assertEqual(response.get_json()["retry_count"], 2)
        self.assertEqual(captured["action"], "doubao.batch_generate_prompts")
        self.assertEqual(captured["payload"]["record_ids"], ["rec-1", "rec-2"])
        self.assertEqual(captured["payload"]["country_code"], "PH")
        self.assertEqual(captured["payload"]["retry_of"], "job-1")
        self.assertEqual(captured["payload"]["selection_label"], "豆包素材表 4932–5250 · 失败重试")

    def test_running_batch_job_can_request_stop(self):
        job = {
            "id": "job-running",
            "action": "doubao.batch_workflow",
            "status": "running",
        }
        updated = {**job, "cancel_requested": True}
        with (
            patch.object(app_module, "get_job", return_value=job),
            patch.object(app_module, "cancel_job", return_value=updated) as cancel,
        ):
            response = self.client.post("/api/jobs/job-running/cancel", json={})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["cancel_requested"])
        self.assertEqual(response.get_json()["status"], "running")
        self.assertIn("正在完成当前处理中的记录", response.get_json()["message"])
        cancel.assert_called_once_with("job-running")

    def test_finished_job_cannot_be_cancelled(self):
        job = {
            "id": "job-done",
            "action": "doubao.batch_workflow",
            "status": "done",
        }
        with patch.object(app_module, "get_job", return_value=job):
            response = self.client.post("/api/jobs/job-done/cancel", json={})

        self.assertEqual(response.status_code, 409)
        self.assertIn("已经结束", response.get_json()["message"])

    def test_stopping_one_chunk_stops_all_active_jobs_in_the_same_batch(self):
        jobs = [
            {
                "id": "job-1",
                "action": "doubao.batch_workflow",
                "status": "running",
                "payload": {"batch_group_id": "group-1"},
            },
            {
                "id": "job-2",
                "action": "doubao.batch_workflow",
                "status": "queued",
                "payload": {"batch_group_id": "group-1"},
            },
        ]

        def cancel(job_id):
            job = next(item for item in jobs if item["id"] == job_id)
            return {**job, "status": "cancelled" if job["status"] == "queued" else "running", "cancel_requested": True}

        with (
            patch.object(app_module, "get_job", return_value=jobs[0]),
            patch.object(app_module, "list_jobs", return_value=jobs),
            patch.object(app_module, "cancel_job", side_effect=cancel) as cancel_mock,
        ):
            response = self.client.post("/api/jobs/job-1/cancel", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["cancelled_job_count"], 2)
        self.assertEqual(response.get_json()["job_ids"], ["job-1", "job-2"])
        self.assertIn("本批次 2 个任务", response.get_json()["message"])
        self.assertEqual([call.args[0] for call in cancel_mock.call_args_list], ["job-1", "job-2"])


if __name__ == "__main__":
    unittest.main()
