import hashlib
import json
from typing import Any

from ..countries import prompt_regions
from ..feishu import field_value
from ..prompting import (
    DEFAULT_IMAGE_PROMPTS,
    DEFAULT_VIDEO_ANALYSIS_TEMPLATE,
)


DOUBAO_PROMPT_KEY = "doubao_seedance_video_prompt"
EDITABLE_FIELDS = (
    "Prompt名称",
    "版本",
    "是否启用",
    "目标地区",
    "模板内容",
    "变量说明",
    "备注",
)
REQUIRED_FIELDS = ("Prompt名称", "版本", "目标地区", "模板内容")


class PromptConflictError(RuntimeError):
    pass


def prompt_revision(fields: dict[str, Any]) -> str:
    body = json.dumps(
        {key: field_value(fields, key, "") for key in EDITABLE_FIELDS},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class PromptConfigService:
    def __init__(self, settings, feishu):
        self.settings = settings
        self.feishu = feishu

    def list_configs(self) -> list[dict[str, Any]]:
        if not self.settings.prompt_table_id:
            return []
        return [
            self._config_row(record)
            for record in self.feishu.list_records(self.settings.prompt_table_id)
        ]

    def create(self, values: dict[str, Any]) -> dict[str, Any]:
        fields = self._write_fields(values, creating=True)
        self._validate(fields)
        rows = self.list_configs()
        key = fields["模板Key"]
        region = fields["目标地区"]
        self._ensure_unique_version(rows, key, region, fields["版本"])
        if fields["是否启用"] == "启用":
            self._disable_siblings(rows, key, region)
        created = self.feishu.create_record(self.settings.prompt_table_id, fields)
        return self._config_row(created)

    def update(
        self,
        record_id: str,
        patch: dict[str, Any],
        *,
        expected_revision: str = "",
    ) -> dict[str, Any]:
        fields = self._write_fields(patch)
        if not fields:
            raise RuntimeError("没有可更新的 Prompt 字段")
        current = self.feishu.get_record(self.settings.prompt_table_id, record_id)
        if expected_revision and prompt_revision(current.get("fields", {})) != expected_revision:
            raise PromptConflictError("Prompt 已被其他人修改，请刷新后重新编辑")
        current_row = self._config_row(current)
        merged = {**current_row, **fields}
        self._validate(merged)
        rows = self.list_configs()
        key = str(merged.get("模板Key") or "").strip()
        region = str(merged.get("目标地区") or "").strip()
        version = str(merged.get("版本") or "").strip()
        self._ensure_unique_version(
            rows,
            key,
            region,
            version,
            exclude_record_id=record_id,
        )
        if merged.get("是否启用") == "启用":
            self._disable_siblings(
                rows,
                key,
                region,
                exclude_record_id=record_id,
            )
        return self.feishu.update_record(
            self.settings.prompt_table_id,
            record_id,
            fields,
        )

    def active_templates(self, region: str) -> tuple[dict[str, str], dict[str, str]]:
        templates: dict[str, tuple[int, str]] = {
            "video_analysis": (0, DEFAULT_VIDEO_ANALYSIS_TEMPLATE),
            **{
                f"image_{style}": (0, prompt)
                for style, prompt in DEFAULT_IMAGE_PROMPTS.items()
            },
        }
        versions: dict[str, str] = {key: "default" for key in templates}
        if not self.settings.prompt_table_id:
            return {
                key: value
                for key, (_, value) in templates.items()
            }, versions

        for row in self.list_configs():
            key = str(row.get("模板Key") or "").strip()
            content = str(row.get("模板内容") or "").strip()
            enabled = str(row.get("是否启用") or "").strip()
            target_region = str(row.get("目标地区") or "").strip()
            if not key or not content or enabled != "启用":
                continue
            rank = (
                2
                if target_region == region
                else 1
                if target_region in ("", "通用")
                else -1
            )
            if rank < 0:
                continue
            current_rank = templates.get(key, (-1, ""))[0]
            if rank >= current_rank:
                templates[key] = (rank, content)
                version = str(row.get("版本") or "未标版本").strip()
                versions[key] = f"{key}:{version}"
        return {
            key: value
            for key, (_, value) in templates.items()
        }, versions

    def active_template(
        self,
        key: str,
        region: str,
        fallback: str,
    ) -> tuple[str, str]:
        templates, versions = self.active_templates(region)
        return templates.get(key, fallback), versions.get(key, f"{key}:default")

    @staticmethod
    def _config_row(record: dict[str, Any]) -> dict[str, Any]:
        fields = record.get("fields", {})
        return {
            "record_id": record.get("record_id"),
            "Prompt名称": field_value(fields, "Prompt名称", ""),
            "Prompt类型": field_value(fields, "Prompt类型", ""),
            "模板Key": field_value(fields, "模板Key", ""),
            "版本": field_value(fields, "版本", ""),
            "是否启用": field_value(fields, "是否启用", ""),
            "目标地区": field_value(fields, "目标地区", ""),
            "模板内容": field_value(fields, "模板内容", ""),
            "变量说明": field_value(fields, "变量说明", ""),
            "备注": field_value(fields, "备注", ""),
            "revision": prompt_revision(fields),
        }

    @staticmethod
    def _write_fields(
        values: dict[str, Any],
        *,
        creating: bool = False,
    ) -> dict[str, str]:
        fields = {
            key: str(values.get(key) or "").strip()
            for key in EDITABLE_FIELDS
            if key in values
        }
        if creating:
            fields["Prompt类型"] = "豆包视频"
            fields["模板Key"] = DOUBAO_PROMPT_KEY
            fields.setdefault("是否启用", "停用")
        return fields

    @staticmethod
    def _validate(fields: dict[str, Any]) -> None:
        for name in REQUIRED_FIELDS:
            if not str(fields.get(name) or "").strip():
                raise ValueError(f"{name}不能为空")
        if fields.get("是否启用") not in {"启用", "停用"}:
            raise ValueError("是否启用只能是：启用 / 停用")
        allowed_regions = {"墨西哥", "通用", *prompt_regions()}
        if fields.get("目标地区") not in allowed_regions:
            raise ValueError(f"目标地区只能是：{' / '.join(sorted(allowed_regions))}")

    @staticmethod
    def _ensure_unique_version(
        rows: list[dict[str, Any]],
        key: str,
        region: str,
        version: str,
        *,
        exclude_record_id: str = "",
    ) -> None:
        for row in rows:
            if row.get("record_id") == exclude_record_id:
                continue
            if (
                str(row.get("模板Key") or "").strip() == key
                and str(row.get("目标地区") or "").strip() == region
                and str(row.get("版本") or "").strip().casefold() == version.casefold()
            ):
                raise ValueError(f"版本 {version} 在 {region} 已存在，请使用新版本号")

    def _disable_siblings(
        self,
        rows: list[dict[str, Any]],
        key: str,
        region: str,
        *,
        exclude_record_id: str = "",
    ) -> None:
        for row in rows:
            if row.get("record_id") == exclude_record_id:
                continue
            if (
                str(row.get("模板Key") or "").strip() == key
                and str(row.get("目标地区") or "").strip() == region
                and str(row.get("是否启用") or "").strip() == "启用"
            ):
                self.feishu.update_record(
                    self.settings.prompt_table_id,
                    str(row["record_id"]),
                    {"是否启用": "停用"},
                )
