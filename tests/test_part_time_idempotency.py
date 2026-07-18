import threading
import unittest
from types import SimpleNamespace

from api.feishu import FeishuError
from api.persistence import InMemoryIdempotencyLedger
from api.services.part_time_sync import PartTimeSyncService


class PartTimeIdempotencyTests(unittest.TestCase):
    def test_deleted_completed_target_is_recreated_on_resync(self):
        source_fields = {
            "商品名称": "Test product",
            "商品图存储链接": "https://storage.example/product.jpg",
            "最终视频生成提示词": "Mexican Spanish prompt",
            "目标国家": "墨西哥",
            "目标语言": "西班牙语",
        }
        created = []
        target_records = []

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                if table_id == "source":
                    return {"record_id": record_id, "fields": dict(source_fields)}
                if record_id == "target-deleted":
                    raise FeishuError("RecordIdNotFound")
                return next(record for record in target_records if record["record_id"] == record_id)

            def list_records(self, table_id, **kwargs):
                return target_records

            def create_record(self, table_id, fields):
                record = {"record_id": "target-new", "fields": dict(fields)}
                target_records.append(record)
                created.append(record)
                return record

            def update_record(self, table_id, record_id, fields):
                return {"record_id": record_id, "fields": dict(fields)}

        ledger = InMemoryIdempotencyLedger()
        scope = "part-time:source-1:MX:西班牙语"
        ledger.complete(scope, "old-job", "target-deleted")
        service = PartTimeSyncService(
            SimpleNamespace(doubao_table_id="source", part_time_table_id="target"),
            FakeFeishu(),
            ledger=ledger,
        )

        result = service.sync_record("source-1", "MX", owner_id="retry-job")
        repeated = service.sync_record("source-1", "MX", owner_id="later-job")

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["target_record_id"], "target-new")
        self.assertEqual(repeated["status"], "updated")
        self.assertEqual(len(created), 1)

    def test_pending_ledger_reservation_blocks_duplicate_create(self):
        create_started = threading.Event()
        release_create = threading.Event()
        created = []
        source_fields = {
            "商品名称": "Test product",
            "商品图链接": "https://cdn.example/product.jpg",
            "最终视频生成提示词": "Mexican Spanish prompt",
            "目标国家": "墨西哥",
            "目标语言": "西班牙语",
        }

        class FakeFeishu:
            def get_record(self, table_id, record_id):
                return {"record_id": record_id, "fields": dict(source_fields)}

            def list_records(self, table_id, **kwargs):
                return []

            def create_record(self, table_id, fields):
                create_started.set()
                release_create.wait(2)
                record = {"record_id": "target-1", "fields": dict(fields)}
                created.append(record)
                return record

            def update_record(self, table_id, record_id, fields):
                return {"record_id": record_id, "fields": dict(fields)}

        ledger = InMemoryIdempotencyLedger()
        settings = SimpleNamespace(doubao_table_id="source", part_time_table_id="target")
        service = PartTimeSyncService(settings, FakeFeishu(), ledger=ledger)
        first_result = []
        first_error = []

        def first_sync():
            try:
                first_result.append(service.sync_record("source-1", "MX", owner_id="job-a"))
            except Exception as exc:
                first_error.append(str(exc))

        thread = threading.Thread(target=first_sync)
        thread.start()
        self.assertTrue(create_started.wait(1))

        with self.assertRaisesRegex(RuntimeError, "正在处理中"):
            service.sync_record("source-1", "MX", owner_id="job-b")

        release_create.set()
        thread.join(2)
        self.assertEqual(first_error, [])
        self.assertEqual(first_result[0]["target_record_id"], "target-1")
        self.assertEqual(len(created), 1)


if __name__ == "__main__":
    unittest.main()
