import json
import unittest
from types import SimpleNamespace

from api.feishu import FeishuClient


class FeishuFilteredRecordTests(unittest.TestCase):
    def test_filtered_records_are_paged_and_converted_to_regular_record_shape(self):
        client = object.__new__(FeishuClient)
        client.settings = SimpleNamespace(feishu_app_token="base-token")
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs["params"]))
            offset = kwargs["params"]["offset"]
            if offset == 0:
                return {
                    "fields": ["来源记录ID", "视频地址"],
                    "record_id_list": ["part-1"],
                    "data": [["source-1", "https://cdn.example/one.mp4"]],
                    "has_more": True,
                }
            return {
                "fields": ["来源记录ID", "视频地址"],
                "record_id_list": ["part-2"],
                "data": [["source-2", "https://cdn.example/two.mp4"]],
                "has_more": False,
            }

        client._request = request
        filter_config = {"logic": "and", "conditions": [["任务状态", "intersects", ["已完成"]]]}

        records = client.list_records_filtered(
            "part-time",
            field_names=["来源记录ID", "视频地址"],
            filter_config=filter_config,
            page_size=1,
        )

        self.assertEqual(records, [
            {"record_id": "part-1", "fields": {"来源记录ID": "source-1", "视频地址": "https://cdn.example/one.mp4"}},
            {"record_id": "part-2", "fields": {"来源记录ID": "source-2", "视频地址": "https://cdn.example/two.mp4"}},
        ])
        self.assertEqual([call[2]["offset"] for call in calls], [0, 1])
        self.assertEqual(json.loads(calls[0][2]["filter"]), filter_config)
        self.assertEqual(calls[0][1], "/base/v3/bases/base-token/tables/part-time/records")


if __name__ == "__main__":
    unittest.main()
