import unittest


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
            {"record_ids": ["rec-2", "rec-1", "rec-2", ""], "country_code": "mx"},
        )

        self.assertEqual(payload["record_ids"], ["rec-1", "rec-2"])
        self.assertEqual(payload["country_code"], "MX")

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

        self.assertEqual(calls, ["rec-1", "rec-2"])
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 0)

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
        self.assertEqual(calls, [(["source-1", "source-2"], 200, "job-1")])

    def test_unknown_action_is_rejected(self):
        try:
            from api.jobs.actions import execute_action
        except ImportError as exc:
            self.fail(f"缺少 Action 执行器: {exc}")

        with self.assertRaisesRegex(ValueError, "不支持的任务动作"):
            execute_action("unknown.action", {}, "job-1", "alice", workflow=object())


if __name__ == "__main__":
    unittest.main()
