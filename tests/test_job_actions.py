import time
import unittest
from threading import Lock
from unittest.mock import patch


class JobActionContractTests(unittest.TestCase):
    def helpers(self):
        try:
            from api.jobs.actions import normalize_payload, resource_keys_for
        except (ImportError, ModuleNotFoundError) as exc:
            self.fail(f"缺少可序列化任务 Action: {exc}")
        return normalize_payload, resource_keys_for

    def test_batch_record_ids_are_deduplicated_and_canonical(self):
        normalize_payload, _ = self.helpers()

        payload = normalize_payload(
            "doubao.batch_generate_prompts",
            {
                "record_ids": ["rec-2", "rec-1", "rec-2", ""],
                "row_numbers": {"rec-1": 101, "rec-2": "102", "other": 999},
                "country_code": "mx",
            },
        )

        self.assertEqual(payload["record_ids"], ["rec-2", "rec-1"])
        self.assertEqual(payload["row_numbers"], {"rec-1": 101, "rec-2": 102})
        self.assertEqual(payload["country_code"], "MX")

    def test_batch_failure_keeps_doubao_row_number_context(self):
        from api.jobs.actions import _batch

        result = _batch(
            ["rec-failed"],
            lambda record_id: {
                "record_id": record_id,
                "status": "failed",
                "stage": "提示词生成",
                "error": "上游数据尚未准备好",
            },
            row_numbers={"rec-failed": 5480},
        )

        self.assertEqual(result["results"][0]["row_number"], 5480)
        self.assertEqual(result["results"][0]["source_table"], "豆包素材表")

    def test_batch_stop_skips_records_that_have_not_started(self):
        from api.jobs.actions import _batch

        handled = []
        cancel_checks = iter([False, True, True])
        with patch("api.jobs.job_cancel_requested", side_effect=lambda job_id: next(cancel_checks)):
            result = _batch(
                ["rec-1", "rec-2", "rec-3"],
                lambda record_id: handled.append(record_id) or {"record_id": record_id},
                max_workers=1,
                job_id="job-stop",
                label="测试停止",
            )

        self.assertEqual(handled, ["rec-1"])
        self.assertEqual(result["selected"], 3)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["cancelled"], 2)
        self.assertEqual(result["failed"], 0)

    def test_batch_resume_keeps_checkpointed_failures_without_retrying_them(self):
        from api.jobs.actions import _batch

        handled = []
        checkpointed = {
            "rec-done": {"record_id": "rec-done", "status": "created"},
            "rec-failed": {"record_id": "rec-failed", "status": "failed", "error": "HTTP 502"},
        }
        with (
            patch("api.jobs.job_cancel_requested", return_value=False),
            patch("api.jobs.job_item_results", return_value=checkpointed, create=True),
            patch("api.jobs.save_job_item_result", create=True) as save_result,
        ):
            result = _batch(
                ["rec-done", "rec-failed", "rec-new"],
                lambda record_id: handled.append(record_id) or {"record_id": record_id, "status": "created"},
                max_workers=1,
                job_id="job-resume",
                label="恢复测试",
            )

        self.assertEqual(handled, ["rec-new"])
        self.assertEqual(result["processed"], 3)
        self.assertEqual(result["failed"], 1)
        save_result.assert_called_once_with(
            "job-resume",
            "rec-new",
            {"record_id": "rec-new", "status": "created"},
        )

    def test_batch_does_not_automatically_retry_failed_record(self):
        from api.jobs.actions import _batch

        handled = []
        with (
            patch("api.jobs.job_cancel_requested", return_value=False),
            patch("api.jobs.job_item_results", return_value={}, create=True),
            patch("api.jobs.save_job_item_result", create=True),
        ):
            result = _batch(
                ["rec-failed"],
                lambda record_id: handled.append(record_id) or {
                    "record_id": record_id,
                    "status": "failed",
                    "error": "模型请求失败",
                },
                max_workers=1,
                job_id="job-failed",
            )

        self.assertEqual(handled, ["rec-failed"])
        self.assertEqual(result["failed"], 1)

    def test_checkpoint_write_failure_does_not_turn_success_into_failure(self):
        from api.jobs.actions import _batch

        with (
            patch("api.jobs.job_cancel_requested", return_value=False),
            patch("api.jobs.job_item_results", return_value={}, create=True),
            patch(
                "api.jobs.save_job_item_result",
                side_effect=RuntimeError("redis unavailable"),
                create=True,
            ),
        ):
            result = _batch(
                ["rec-done"],
                lambda record_id: {"record_id": record_id, "status": "created"},
                max_workers=1,
                job_id="job-checkpoint-error",
            )

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["failed"], 0)

    def test_doubao_country_variants_share_the_same_record_lock(self):
        _, resource_keys_for = self.helpers()

        my_keys = resource_keys_for(
            "doubao.generate_prompt",
            {"record_id": "rec-1", "country_code": "MY"},
        )
        mx_keys = resource_keys_for(
            "doubao.generate_prompt",
            {"record_id": "rec-1", "country_code": "MX"},
        )

        self.assertEqual(my_keys, ["doubao:rec-1"])
        self.assertEqual(mx_keys, ["doubao:rec-1"])

    def test_single_and_batch_material_actions_share_record_lock(self):
        _, resource_keys_for = self.helpers()

        single = resource_keys_for("material.create_tasks", {"record_id": "mat-1"})
        batch = resource_keys_for("material.batch_step", {"record_ids": ["mat-1"], "step": "create-tasks"})

        self.assertEqual(single, ["material:mat-1"])
        self.assertEqual(batch, ["material:mat-1"])

    def test_single_action_invokes_serializable_workflow_method(self):
        try:
            from api.jobs.actions import execute_action
        except ImportError as exc:
            self.fail(f"缺少 Action 执行器: {exc}")
        calls = []

        class FakeWorkflow:
            def generate_doubao_prompt(self, record_id, country_code, job_id=None):
                calls.append((record_id, country_code, job_id))
                return {"record_id": record_id}

        result = execute_action(
            "doubao.generate_prompt",
            {"record_id": "rec-1", "country_code": "MX"},
            "job-1",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(result, {"record_id": "rec-1"})
        self.assertEqual(calls, [("rec-1", "MX", "job-1")])

    def test_batch_action_processes_each_selected_record(self):
        try:
            from api.jobs.actions import execute_action
        except ImportError as exc:
            self.fail(f"缺少 Action 执行器: {exc}")
        calls = []

        class FakeWorkflow:
            def generate_doubao_prompt(self, record_id, country_code, job_id=None):
                calls.append(record_id)
                return {"record_id": record_id}

        result = execute_action(
            "doubao.batch_generate_prompts",
            {"record_ids": ["rec-2", "rec-1"], "country_code": "MX"},
            "job-1",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(calls, ["rec-2", "rec-1"])
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 0)

    def test_face_detection_batch_uses_bounded_model_concurrency(self):
        from api.jobs.actions import execute_action

        calls = []

        class FakeWorkflow:
            settings = type("Settings", (), {"batch_face_concurrency": 2})()

            def detect_doubao_face(self, record_id, job_id=None):
                calls.append((record_id, job_id))
                return {"record_id": record_id, "status": "无人脸"}

        result = execute_action(
            "doubao.batch_detect_faces",
            {"record_ids": ["rec-2", "rec-1"]},
            "job-face",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual({item[0] for item in calls}, {"rec-1", "rec-2"})
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 0)

    def test_flagged_record_deletion_batch_deletes_each_selected_record(self):
        from api.jobs.actions import execute_action

        calls = []

        class FakeWorkflow:
            settings = type("Settings", (), {"batch_image_concurrency": 2})()

            def delete_doubao_face_record(self, record_id, job_id=None):
                calls.append((record_id, job_id))
                return {"record_id": record_id, "status": "已删除", "deleted": True}

        result = execute_action(
            "doubao.batch_delete_face_records",
            {"record_ids": ["rec-2", "rec-1"]},
            "job-delete",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual({item[0] for item in calls}, {"rec-1", "rec-2"})
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 0)

    def test_batch_helper_runs_with_bounded_concurrency(self):
        from api.jobs.actions import _batch

        active = 0
        max_active = 0
        lock = Lock()

        def handler(record_id):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return {"record_id": record_id}

        result = _batch(
            ["rec-1", "rec-2", "rec-3", "rec-4"],
            handler,
            max_workers=3,
        )

        self.assertGreater(max_active, 1)
        self.assertLessEqual(max_active, 3)
        self.assertEqual(
            [item["record_id"] for item in result["results"]],
            ["rec-1", "rec-2", "rec-3", "rec-4"],
        )

    def test_batch_image_transfer_includes_external_urls_from_storage_field(self):
        try:
            from api.jobs.actions import execute_action
        except ImportError as exc:
            self.fail(f"缺少 Action 执行器: {exc}")
        calls = []

        class FakeWorkflow:
            def list_doubao_items(self, limit):
                return [
                    {
                        "record_id": "external-record",
                        "商品图存储链接": "https://img.kalocdn.com/product/cover.png",
                        "商品图已入库": False,
                        "商品图可转存": True,
                    },
                    {
                        "record_id": "managed-record",
                        "商品图存储链接": "https://vc-upload.example.com/product/cover.png",
                        "商品图已入库": True,
                        "商品图可转存": False,
                    },
                ]

            def store_doubao_image(self, record_id, job_id=None):
                calls.append(record_id)
                return {"record_id": record_id}

        result = execute_action(
            "doubao.batch_store_images",
            {"limit": 50},
            "job-1",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(calls, ["external-record"])
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["failed"], 0)

    def test_part_time_batch_passes_force_update_and_counts_skipped_rows(self):
        from api.jobs.actions import execute_action

        calls = []

        class FakeFeishu:
            def list_records(self, table_id):
                return []

        class FakeWorkflow:
            settings = type("Settings", (), {"part_time_table_id": "target-table"})()
            feishu = FakeFeishu()

            def sync_doubao_to_part_time(
                self,
                record_id,
                country_code,
                job_id=None,
                target_records=None,
                force_update=False,
            ):
                calls.append((record_id, force_update))
                return {"record_id": record_id, "status": "skipped"}

        result = execute_action(
            "doubao.batch_sync_part_time",
            {"record_ids": ["rec-1"], "country_code": "MX", "force_update": True},
            "job-1",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(calls, [("rec-1", True)])
        self.assertEqual(result["skipped"], 1)

    def test_part_time_batch_uses_bounded_concurrency(self):
        from api.jobs.actions import execute_action

        active = 0
        max_active = 0
        lock = Lock()

        class FakeFeishu:
            def list_records(self, table_id):
                return []

        class FakeWorkflow:
            settings = type(
                "Settings",
                (),
                {"part_time_table_id": "target-table", "batch_sync_concurrency": 2},
            )()
            feishu = FakeFeishu()

            def sync_doubao_to_part_time(
                self,
                record_id,
                country_code,
                job_id=None,
                target_records=None,
                force_update=False,
            ):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return {"record_id": record_id, "status": "created"}

        result = execute_action(
            "doubao.batch_sync_part_time",
            {"record_ids": ["rec-1", "rec-2", "rec-3", "rec-4"], "country_code": "PH"},
            "job-1",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertGreater(max_active, 1)
        self.assertLessEqual(max_active, 2)
        self.assertEqual(result["created"], 4)

    def test_part_time_batch_counts_protected_target_conflicts_as_failed(self):
        from api.jobs.actions import execute_action

        class FakeFeishu:
            def list_records(self, table_id):
                return []

        class FakeWorkflow:
            settings = type("Settings", (), {"part_time_table_id": "target-table"})()
            feishu = FakeFeishu()

            def sync_doubao_to_part_time(
                self,
                record_id,
                country_code,
                job_id=None,
                target_records=None,
                force_update=False,
            ):
                return {
                    "record_id": record_id,
                    "status": "conflict",
                    "message": "目标兼职任务状态为已完成，已保护现有任务，未覆盖",
                }

        result = execute_action(
            "doubao.batch_sync_part_time",
            {"record_ids": ["rec-conflict"], "country_code": "PH", "force_update": True},
            "job-1",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["conflict"], 1)

    def test_doubao_workflow_batch_continues_other_records_and_counts_outcomes(self):
        from api.jobs.actions import execute_action

        calls = []

        class FakeFeishu:
            def list_records(self, table_id):
                return []

        class FakeWorkflow:
            settings = type(
                "Settings",
                (),
                {"part_time_table_id": "target-table", "batch_sync_concurrency": 2},
            )()
            feishu = FakeFeishu()

            def process_doubao_workflow_record(
                self,
                record_id,
                country_code,
                job_id=None,
                target_records=None,
                force_update=False,
                before_part_time_sync=None,
            ):
                if before_part_time_sync:
                    before_part_time_sync()
                calls.append((record_id, country_code, force_update))
                if record_id == "rec-blocked":
                    return {
                        "record_id": record_id,
                        "status": "blocked",
                        "stage": "人脸审核拦截",
                        "message": "商品图包含人脸",
                    }
                if record_id == "rec-failed":
                    return {
                        "record_id": record_id,
                        "status": "failed",
                        "stage": "图片转存",
                        "error": "HTTP 403",
                    }
                return {"record_id": record_id, "status": "created", "stage": "同步兼职表"}

        result = execute_action(
            "doubao.batch_workflow",
            {
                "record_ids": ["rec-ready", "rec-blocked", "rec-failed"],
                "country_code": "MX",
                "force_update": True,
            },
            "job-workflow",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual({item[0] for item in calls}, {"rec-ready", "rec-blocked", "rec-failed"})
        self.assertTrue(all(item[1:] == ("MX", True) for item in calls))
        self.assertEqual(result["processed"], 3)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(result["created"], 1)

    def test_doubao_workflow_commits_to_part_time_in_source_order(self):
        from api.jobs.actions import execute_action

        commits = []

        class FakeFeishu:
            def list_records(self, table_id):
                return []

        class FakeWorkflow:
            settings = type(
                "Settings",
                (),
                {"part_time_table_id": "target-table", "batch_workflow_concurrency": 2},
            )()
            feishu = FakeFeishu()

            def process_doubao_workflow_record(self, record_id, country_code="MY", before_part_time_sync=None, **kwargs):
                if record_id == "rec-first":
                    time.sleep(0.03)
                before_part_time_sync()
                commits.append(record_id)
                return {"record_id": record_id, "status": "created"}

        result = execute_action(
            "doubao.batch_workflow",
            {"record_ids": ["rec-first", "rec-second"], "country_code": "MX"},
            "job-ordered",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(commits, ["rec-first", "rec-second"])
        self.assertEqual(result["created"], 2)

    def test_special_template_replacement_updates_part_time_in_source_order(self):
        from api.jobs.actions import execute_action

        commits = []

        class FakeFeishu:
            def list_records(self, table_id):
                return []

        class FakeWorkflow:
            settings = type(
                "Settings",
                (),
                {"part_time_table_id": "target-table", "batch_workflow_concurrency": 2},
            )()
            feishu = FakeFeishu()

            def retemplate_doubao_and_sync(self, record_id, country_code, before_part_time_sync=None, **kwargs):
                if record_id == "rec-first":
                    time.sleep(0.03)
                before_part_time_sync()
                commits.append(record_id)
                return {"record_id": record_id, "status": "updated"}

        result = execute_action(
            "doubao.batch_retemplate_sync",
            {
                "record_ids": ["rec-first", "rec-second"],
                "country_code": "MX",
                "prompt_snapshot": {"category": "服装", "execution_mode": "direct_prompt"},
            },
            "job-retemplate",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(commits, ["rec-first", "rec-second"])
        self.assertEqual(result["updated"], 2)

    def test_special_template_replacement_protects_taken_part_time_task(self):
        from api.workflow import ViralRemixWorkflow

        calls = []

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {"record_id": record_id, "fields": {"兼职同步记录ID": "part-1"}}

        class FakeWorkflow:
            settings = type("Settings", (), {"doubao_table_id": "doubao", "part_time_table_id": "part-time"})()
            feishu = FakeFeishu()

            def generate_doubao_prompt(self, *args, **kwargs):
                calls.append("prompt")

            def sync_doubao_to_part_time(self, *args, **kwargs):
                calls.append("sync")

        result = ViralRemixWorkflow.retemplate_doubao_and_sync(
            FakeWorkflow(),
            "rec-1",
            "MX",
            prompt_snapshot={"category": "服装"},
            target_records=[{"record_id": "part-1", "fields": {"任务状态": "已完成"}}],
        )

        self.assertEqual(result["status"], "conflict")
        self.assertEqual(calls, [])

    def test_special_template_replacement_overwrites_pending_task(self):
        from api.workflow import ViralRemixWorkflow

        calls = []

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {"record_id": record_id, "fields": {"兼职同步记录ID": "part-1"}}

        class FakeWorkflow:
            settings = type("Settings", (), {"doubao_table_id": "doubao", "part_time_table_id": "part-time"})()
            feishu = FakeFeishu()

            def generate_doubao_prompt(self, record_id, country_code, **kwargs):
                calls.append(("prompt", kwargs["allow_synced_update"]))

            def sync_doubao_to_part_time(self, record_id, country_code, **kwargs):
                calls.append(("sync", kwargs["force_update"]))
                return {"record_id": record_id, "status": "updated"}

        result = ViralRemixWorkflow.retemplate_doubao_and_sync(
            FakeWorkflow(),
            "rec-1",
            "MX",
            prompt_snapshot={"category": "服装"},
            target_records=[{"record_id": "part-1", "fields": {"任务状态": "待接单"}}],
        )

        self.assertEqual(result["status"], "updated")
        self.assertEqual(calls, [("prompt", True), ("sync", True)])

    def test_doubao_workflow_record_runs_each_required_stage_in_order(self):
        from api.workflow import ViralRemixWorkflow

        calls = []
        current = {"next_action": "store_image"}
        fields = {}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {"record_id": record_id, "fields": fields}

        class FakeAssets:
            def describe(self, fields):
                return {
                    "素材下一步": current["next_action"],
                    "流程阶段": current["next_action"],
                    "素材阻塞原因": "",
                    "可执行生成提示词": True,
                }

        class FakeWorkflow:
            settings = type("Settings", (), {"doubao_table_id": "doubao-table"})()
            feishu = FakeFeishu()
            doubao_assets = FakeAssets()

            def store_doubao_image(self, record_id, job_id=None):
                calls.append("store")
                current["next_action"] = "detect_face"

            def detect_doubao_face(self, record_id, job_id=None):
                calls.append("face")
                current["next_action"] = "generate_prompt"

            def generate_doubao_prompt(self, record_id, country_code, job_id=None):
                calls.append("prompt")
                fields.update({"目标国家": "墨西哥", "目标语言": "西班牙语"})
                current["next_action"] = "sync_part_time"

            def sync_doubao_to_part_time(
                self,
                record_id,
                country_code,
                job_id=None,
                target_records=None,
                force_update=False,
            ):
                calls.append("sync")
                return {"record_id": record_id, "status": "created"}

        result = ViralRemixWorkflow.process_doubao_workflow_record(
            FakeWorkflow(),
            "rec-1",
            "MX",
            job_id="job-workflow",
            target_records=[],
        )

        self.assertEqual(calls, ["store", "face", "prompt", "sync"])
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["steps"], ["图片转存", "人脸检测", "提示词生成", "同步兼职表"])

    def test_doubao_workflow_can_explicitly_skip_face_detection(self):
        from api.workflow import ViralRemixWorkflow

        calls = []
        fields = {"商品id": "product-1"}
        current = {"next_action": "detect_face"}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {"record_id": record_id, "fields": fields}

        class FakeAssets:
            def describe(self, fields):
                return {"素材下一步": current["next_action"], "素材阻塞原因": "", "可执行生成提示词": True}

            def skip_face_detection(self, record_id, job_id=None):
                calls.append("skip-face")
                current["next_action"] = "generate_prompt"

        class FakeWorkflow:
            settings = type("Settings", (), {"doubao_table_id": "doubao-table"})()
            feishu = FakeFeishu()
            doubao_assets = FakeAssets()

            def generate_doubao_prompt(self, record_id, country_code, job_id=None):
                calls.append("prompt")
                fields.update({"目标国家": "墨西哥", "目标语言": "西班牙语"})
                current["next_action"] = "sync_part_time"

            def sync_doubao_to_part_time(self, record_id, country_code, job_id=None, target_records=None, force_update=False):
                calls.append("sync")
                return {"record_id": record_id, "status": "created"}

        result = ViralRemixWorkflow.process_doubao_workflow_record(
            FakeWorkflow(), "rec-1", "MX", target_records=[], detect_faces=False
        )

        self.assertEqual(result["status"], "created")
        self.assertEqual(calls, ["skip-face", "prompt", "sync"])

    def test_doubao_workflow_uses_each_records_country_and_scheme(self):
        from api.workflow import ViralRemixWorkflow

        calls = []
        fields = {"目标国家": "马来西亚", "目标语言": "英语", "商品品类": "服装"}
        current = {"next_action": "generate_prompt"}

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {"record_id": record_id, "fields": fields}

        class FakeAssets:
            def describe(self, current_fields):
                return {"素材下一步": current["next_action"], "素材阻塞原因": "", "可执行生成提示词": True}

        class FakePromptConfigs:
            def resolve_doubao_snapshot(self, **kwargs):
                calls.append(("snapshot", kwargs))
                return {"scheme_key": kwargs["scheme_key"], "version": "my-en-t2"}

        class FakeWorkflow:
            settings = type("Settings", (), {"doubao_table_id": "doubao-table"})()
            feishu = FakeFeishu()
            doubao_assets = FakeAssets()
            prompt_configs = FakePromptConfigs()

            def generate_doubao_prompt(self, record_id, country_code, job_id=None, prompt_snapshot=None):
                calls.append(("prompt", country_code, prompt_snapshot["scheme_key"]))
                current["next_action"] = "sync_part_time"

            def sync_doubao_to_part_time(self, record_id, country_code, job_id=None, target_records=None, force_update=False):
                calls.append(("sync", country_code))
                return {"record_id": record_id, "status": "created"}

        result = ViralRemixWorkflow.process_doubao_workflow_record(
            FakeWorkflow(),
            "rec-my-en",
            "PH",
            target_records=[],
            use_record_country=True,
            prompt_scheme_key="t2",
        )

        self.assertEqual(result["status"], "created")
        self.assertEqual(calls[0][1]["region"], "马来-英语")
        self.assertEqual(calls[1], ("prompt", "MY_EN", "t2"))
        self.assertEqual(calls[2], ("sync", "MY_EN"))

    def test_doubao_publish_action_invokes_joined_sync(self):
        from api.jobs.actions import execute_action

        calls = []

        class FakeWorkflow:
            def sync_doubao_publish_items(self, record_ids, limit=100, job_id=None):
                calls.append((record_ids, limit, job_id))
                return {"created": 1}

        result = execute_action(
            "publish.sync_doubao",
            {"record_ids": ["source-2", "source-1"], "limit": 200},
            "job-1",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(result, {"created": 1})
        self.assertEqual(calls, [(["source-2", "source-1"], 200, "job-1")])

    def test_part_time_publish_action_invokes_selected_sync(self):
        from api.jobs.actions import execute_action

        calls = []

        class FakeWorkflow:
            def sync_part_time_publish_items(self, record_ids, limit=100, job_id=None):
                calls.append((record_ids, limit, job_id))
                return {"created": 1, "failed": 0}

        result = execute_action(
            "publish.sync_part_time",
            {"record_ids": ["part-2", "part-1"], "limit": 200},
            "job-1",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(result, {"created": 1, "failed": 0})
        self.assertEqual(calls, [(["part-2", "part-1"], 200, "job-1")])

    def test_part_time_publish_action_keeps_real_row_number_in_failure(self):
        from api.jobs.actions import execute_action

        class FakeWorkflow:
            def sync_part_time_publish_items(self, record_ids, limit=100, job_id=None):
                return {
                    "processed": 1,
                    "failed": 1,
                    "results": [{
                        "record_id": "part-1",
                        "status": "failed",
                        "message": "视频链接无效",
                    }],
                }

        result = execute_action(
            "publish.sync_part_time",
            {
                "record_ids": ["part-1"],
                "row_numbers": {"part-1": 4923},
                "limit": 200,
            },
            "job-1",
            "alice",
            workflow=FakeWorkflow(),
        )

        self.assertEqual(result["results"][0]["row_number"], 4923)
        self.assertEqual(result["results"][0]["source_table"], "兼职表")

    def test_unknown_action_is_rejected(self):
        try:
            from api.jobs.actions import execute_action
        except ImportError as exc:
            self.fail(f"缺少 Action 执行器: {exc}")

        with self.assertRaisesRegex(ValueError, "不支持的任务动作"):
            execute_action("unknown.action", {}, "job-1", "alice", workflow=object())


if __name__ == "__main__":
    unittest.main()
