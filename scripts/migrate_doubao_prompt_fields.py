from api.config import settings
from api.feishu import FeishuClient


TEXT = 1
DATE = 5

FIELDS = {
    "prompt": {
        "方案Key": TEXT, "方案名称": TEXT, "执行模式": TEXT, "目标语言": TEXT,
        "适用品类": TEXT, "视频时长": TEXT, "输出协议": TEXT, "版本已锁定": TEXT,
    },
    "doubao": {
        "商品品类": TEXT, "Prompt方案Key": TEXT, "Prompt方案名称": TEXT,
        "Prompt配置记录ID": TEXT, "Prompt执行模式": TEXT, "Prompt生成时间": DATE,
        "白底图处理状态": TEXT, "白底图处理时间": DATE, "白底图处理模型": TEXT,
        "白底图处理错误": TEXT,
    },
    "part_time": {
        "Prompt方案Key": TEXT, "Prompt方案名称": TEXT, "Prompt配置版本": TEXT,
        "Prompt执行模式": TEXT,
    },
    "publish": {
        "目标语言": TEXT, "Prompt方案Key": TEXT, "Prompt方案名称": TEXT,
        "Prompt配置版本": TEXT, "Prompt执行模式": TEXT,
    },
}


def main() -> None:
    client = FeishuClient(settings)
    tables = {
        "prompt": settings.prompt_table_id,
        "doubao": settings.doubao_table_id,
        "part_time": settings.part_time_table_id,
        "publish": settings.publish_table_id,
    }
    for scope, definitions in FIELDS.items():
        table_id = tables[scope]
        path = f"/bitable/v1/apps/{settings.feishu_app_token}/tables/{table_id}/fields"
        existing = {
            item.get("field_name")
            for item in client._request("GET", path, params={"page_size": 100}).get("items", [])
        }
        for name, field_type in definitions.items():
            if name in existing:
                print(f"SKIP {scope}.{name}")
                continue
            payload = {"field_name": name, "type": field_type}
            if field_type == DATE:
                payload["property"] = {"date_formatter": "yyyy-MM-dd"}
            client._request("POST", path, json=payload)
            print(f"CREATED {scope}.{name}")


if __name__ == "__main__":
    main()
