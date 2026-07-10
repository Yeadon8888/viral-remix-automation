from datetime import datetime
from typing import Any

from ..config import Settings
from ..countries import Country, get_country
from ..feishu import FeishuClient, field_value


LOCKED_TARGET_STATUSES = {"接单中", "已完成"}


def now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def url_cell(url: str) -> dict[str, str]:
    return {"text": url, "link": url}


class PartTimeSyncService:
    def __init__(self, settings: Settings, feishu: FeishuClient):
        self.settings = settings
        self.feishu = feishu

    def _find_target(
        self,
        source_record_id: str,
        country: Country,
        target_records: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        legacy_match = None
        for record in target_records:
            fields = record.get("fields", {})
            if str(field_value(fields, "来源记录ID", "") or "").strip() != source_record_id:
                continue
            target_country = str(field_value(fields, "目标国家", "") or "").strip()
            target_language = str(field_value(fields, "目标语言", "") or "").strip()
            if target_country == country.name and target_language == country.language_name:
                return record
            if country.legacy_default and target_country in ("", country.name) and not target_language:
                legacy_match = record
        return legacy_match

    def _track_source(self, record_id: str, fields: dict[str, Any]) -> None:
        self.feishu.update_record(self.settings.doubao_table_id, record_id, fields)

    def sync_record(
        self,
        source_record_id: str,
        country_code: str = "MY",
        target_records: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        country = get_country(country_code)
        source = self.feishu.get_record(self.settings.doubao_table_id, source_record_id)
        source_fields = source.get("fields", {})
        product_name = str(field_value(source_fields, "商品名称", "") or "").strip() or source_record_id
        prompt = str(field_value(source_fields, "最终视频生成提示词", "") or "").strip()
        image_url = str(field_value(source_fields, "商品图存储链接", "") or "").strip()
        if not image_url.startswith("http"):
            image_url = str(field_value(source_fields, "商品图链接", "") or "").strip()
        if not prompt:
            raise RuntimeError("缺少最终视频生成提示词，请先生成提示词")
        if not image_url.startswith("http"):
            raise RuntimeError("缺少可同步的商品图片链接，请先完成图片入库")
        prompt_country = str(field_value(source_fields, "目标国家", "") or "").strip()
        if not prompt_country and not country.legacy_default:
            raise RuntimeError(f"历史提示词未标记国家，请先按{country.name}重新生成提示词")
        if prompt_country and prompt_country != country.name:
            raise RuntimeError(f"当前提示词属于{prompt_country}，请先按{country.name}重新生成提示词")
        prompt_language = str(field_value(source_fields, "目标语言", "") or "").strip()
        if not prompt_language and not country.legacy_default:
            raise RuntimeError(f"历史提示词未标记语言，请先按{country.language_name}重新生成提示词")
        if prompt_language and prompt_language != country.language_name:
            raise RuntimeError(f"当前提示词语言为{prompt_language}，请先按{country.language_name}重新生成提示词")

        records = target_records
        if records is None:
            records = self.feishu.list_records(self.settings.part_time_table_id)
        target = self._find_target(source_record_id, country, records)
        target_fields = {
            "文本": f"豆包视频｜{country.name}｜{country.language_name}｜{product_name}",
            "图片地址": url_cell(image_url),
            "提示词": prompt,
            "任务状态": "待接单",
            "失败次数": 0,
            "来源记录ID": source_record_id,
            "目标国家": country.name,
            "目标语言": country.language_name,
        }
        if target:
            target_record_id = str(target.get("record_id") or "")
            target_status = str(field_value(target.get("fields", {}), "任务状态", "") or "").strip()
            if target_status in LOCKED_TARGET_STATUSES:
                message = f"目标兼职任务状态为{target_status}，已保护现有任务，未覆盖"
                self._track_source(source_record_id, {
                    "目标国家": country.name,
                    "目标语言": country.language_name,
                    "兼职同步记录ID": target_record_id,
                    "兼职同步状态": "同步冲突",
                    "兼职同步时间": now_ms(),
                    "兼职同步错误": message,
                })
                return {
                    "record_id": source_record_id,
                    "target_record_id": target_record_id,
                    "country_code": country.code,
                    "status": "conflict",
                    "message": message,
                }
            self.feishu.update_record(self.settings.part_time_table_id, target_record_id, target_fields)
            target["fields"] = target_fields
            result_status = "updated"
        else:
            created = self.feishu.create_record(self.settings.part_time_table_id, target_fields)
            target_record_id = str(created.get("record_id") or "")
            if not target_record_id:
                raise RuntimeError("兼职表创建成功但未返回记录 ID")
            if target_records is not None:
                target_records.append({"record_id": target_record_id, "fields": target_fields})
            result_status = "created"

        self._track_source(source_record_id, {
            "目标国家": country.name,
            "目标语言": country.language_name,
            "兼职同步记录ID": target_record_id,
            "兼职同步状态": "已同步",
            "兼职同步时间": now_ms(),
            "兼职同步错误": "",
        })
        return {
            "record_id": source_record_id,
            "target_record_id": target_record_id,
            "country_code": country.code,
            "status": result_status,
        }
