import hashlib
import json
from typing import Any

from ..countries import COUNTRIES, prompt_regions
from ..feishu import field_value
from ..prompting import (
    DEFAULT_IMAGE_PROMPTS,
    DEFAULT_VIDEO_ANALYSIS_TEMPLATE,
)


DOUBAO_PROMPT_KEY = "doubao_seedance_video_prompt"
DEFAULT_SCHEME_KEY = "current_conversion"
DEFAULT_SCHEME_NAME = "T1 当前转化模板"
DEFAULT_EXECUTION_MODE = "meta_generate"
LANGUAGE_BY_PROMPT_REGION = {
    country.prompt_region: country.language_name
    for country in COUNTRIES
}
EDITABLE_FIELDS = (
    "Prompt名称",
    "模板Key",
    "方案Key",
    "方案名称",
    "执行模式",
    "版本",
    "是否启用",
    "目标地区",
    "目标语言",
    "适用品类",
    "视频时长",
    "模板内容",
    "输出协议",
    "变量说明",
    "备注",
)
REVISION_FIELDS = (*EDITABLE_FIELDS, "版本已锁定")
REQUIRED_FIELDS = ("Prompt名称", "版本", "目标地区", "模板内容")
IMMUTABLE_VERSION_FIELDS = {
    "模板Key",
    "方案Key",
    "执行模式",
    "版本",
    "目标地区",
    "目标语言",
    "适用品类",
    "视频时长",
    "模板内容",
    "输出协议",
    "变量说明",
}


class PromptConflictError(RuntimeError):
    pass


def prompt_revision(fields: dict[str, Any]) -> str:
    body = json.dumps(
        {key: field_value(fields, key, "") for key in REVISION_FIELDS},
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
        dimensions = self._dimensions(fields)
        self._ensure_unique_version(rows, *dimensions, fields["版本"])
        if fields["是否启用"] == "启用":
            fields["版本已锁定"] = "是"
            self._disable_siblings(rows, *dimensions)
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
        current_is_locked = self._is_locked(current_row)
        if current_is_locked:
            changed_locked_fields = [
                name
                for name in IMMUTABLE_VERSION_FIELDS
                if name in fields and fields[name] != current_row.get(name)
            ]
            if changed_locked_fields:
                raise ValueError("已启用或已使用的 Prompt 内容不可修改，请复制为新版本")
            fields["版本已锁定"] = "是"
            merged["版本已锁定"] = "是"
        if merged.get("是否启用") == "启用":
            fields["版本已锁定"] = "是"
            merged["版本已锁定"] = "是"
        self._validate(merged)
        rows = self.list_configs()
        dimensions = self._dimensions(merged)
        version = str(merged.get("版本") or "").strip()
        self._ensure_unique_version(
            rows,
            *dimensions,
            version,
            exclude_record_id=record_id,
        )
        if merged.get("是否启用") == "启用":
            self._disable_siblings(
                rows,
                *dimensions,
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
            scheme_key = str(row.get("方案Key") or DEFAULT_SCHEME_KEY).strip()
            target_language = str(row.get("目标语言") or "").strip()
            category = str(row.get("适用品类") or "通用").strip()
            content = str(row.get("模板内容") or "").strip()
            enabled = str(row.get("是否启用") or "").strip()
            target_region = str(row.get("目标地区") or "").strip()
            if (
                not key
                or not content
                or enabled != "启用"
                or scheme_key != DEFAULT_SCHEME_KEY
                or category != "通用"
                or target_language not in {"", LANGUAGE_BY_PROMPT_REGION.get(region, "")}
            ):
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
            "方案Key": field_value(fields, "方案Key", DEFAULT_SCHEME_KEY),
            "方案名称": field_value(fields, "方案名称", DEFAULT_SCHEME_NAME),
            "执行模式": field_value(fields, "执行模式", DEFAULT_EXECUTION_MODE),
            "版本": field_value(fields, "版本", ""),
            "是否启用": field_value(fields, "是否启用", ""),
            "目标地区": field_value(fields, "目标地区", ""),
            "目标语言": field_value(fields, "目标语言", ""),
            "适用品类": field_value(fields, "适用品类", "通用"),
            "视频时长": field_value(fields, "视频时长", ""),
            "模板内容": field_value(fields, "模板内容", ""),
            "输出协议": field_value(fields, "输出协议", "json"),
            "变量说明": field_value(fields, "变量说明", ""),
            "备注": field_value(fields, "备注", ""),
            "版本已锁定": field_value(fields, "版本已锁定", ""),
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
            fields.setdefault("模板Key", DOUBAO_PROMPT_KEY)
            fields.setdefault("方案Key", DEFAULT_SCHEME_KEY)
            fields.setdefault("方案名称", DEFAULT_SCHEME_NAME)
            fields.setdefault("执行模式", DEFAULT_EXECUTION_MODE)
            fields.setdefault("目标语言", "")
            fields.setdefault("适用品类", "通用")
            fields.setdefault("视频时长", "")
            fields.setdefault("输出协议", "json")
            fields.setdefault("是否启用", "停用")
        return fields

    @staticmethod
    def _validate(fields: dict[str, Any]) -> None:
        for name in REQUIRED_FIELDS:
            if not str(fields.get(name) or "").strip():
                raise ValueError(f"{name}不能为空")
        if fields.get("是否启用") not in {"启用", "停用"}:
            raise ValueError("是否启用只能是：启用 / 停用")
        if fields.get("执行模式", DEFAULT_EXECUTION_MODE) not in {"meta_generate", "direct_prompt"}:
            raise ValueError("执行模式只能是：meta_generate / direct_prompt")
        allowed_regions = {"墨西哥", "通用", *prompt_regions()}
        if fields.get("目标地区") not in allowed_regions:
            raise ValueError(f"目标地区只能是：{' / '.join(sorted(allowed_regions))}")

    @staticmethod
    def _ensure_unique_version(
        rows: list[dict[str, Any]],
        scheme_key: str,
        region: str,
        language: str,
        category: str,
        version: str,
        *,
        exclude_record_id: str = "",
    ) -> None:
        for row in rows:
            if row.get("record_id") == exclude_record_id:
                continue
            if (
                str(row.get("方案Key") or DEFAULT_SCHEME_KEY).strip() == scheme_key
                and str(row.get("目标地区") or "").strip() == region
                and str(row.get("目标语言") or "").strip() == language
                and str(row.get("适用品类") or "通用").strip() == category
                and str(row.get("版本") or "").strip().casefold() == version.casefold()
            ):
                raise ValueError(f"版本 {version} 在 {region} 已存在，请使用新版本号")

    def _disable_siblings(
        self,
        rows: list[dict[str, Any]],
        scheme_key: str,
        region: str,
        language: str,
        category: str,
        *,
        exclude_record_id: str = "",
    ) -> None:
        for row in rows:
            if row.get("record_id") == exclude_record_id:
                continue
            if (
                str(row.get("方案Key") or DEFAULT_SCHEME_KEY).strip() == scheme_key
                and str(row.get("目标地区") or "").strip() == region
                and str(row.get("目标语言") or "").strip() == language
                and str(row.get("适用品类") or "通用").strip() == category
                and str(row.get("是否启用") or "").strip() == "启用"
            ):
                self.feishu.update_record(
                    self.settings.prompt_table_id,
                    str(row["record_id"]),
                    {"是否启用": "停用", "版本已锁定": "是"},
                )

    @staticmethod
    def _dimensions(fields: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(fields.get("方案Key") or DEFAULT_SCHEME_KEY).strip(),
            str(fields.get("目标地区") or "").strip(),
            str(fields.get("目标语言") or "").strip(),
            str(fields.get("适用品类") or "通用").strip(),
        )

    @staticmethod
    def _is_locked(fields: dict[str, Any]) -> bool:
        return (
            str(fields.get("版本已锁定") or "").strip() == "是"
            or str(fields.get("是否启用") or "").strip() == "启用"
        )
