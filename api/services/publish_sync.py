from typing import Any, Callable

from ..feishu import field_value
from ..logging_bus import add_log
from ..record_values import (
    first_http_url,
    now_ms,
    recent_window_start_ms,
    record_timestamp_ms,
)
from .doubao_assets import product_id_from_fields


class PublishSyncService:
    def __init__(
        self,
        settings,
        feishu,
        doubao_assets,
        *,
        list_tasks: Callable[..., list[dict[str, Any]]] | None = None,
        clock_ms: Callable[[], int] = now_ms,
    ):
        self.settings = settings
        self.feishu = feishu
        self.doubao_assets = doubao_assets
        self.list_tasks = list_tasks
        self._clock_ms = clock_ms

    def sync_publish_items(
        self,
        record_ids: list[str] | None = None,
        limit: int = 100,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.settings.publish_table_id:
            raise RuntimeError("缺少 FEISHU_PUBLISH_TABLE_ID")
        if self.list_tasks is None:
            raise RuntimeError("发布服务缺少视频任务读取器")

        existing_by_video: dict[str, str] = {}
        for record in self.feishu.list_records(self.settings.publish_table_id):
            fields = record.get("fields", {})
            video_url = str(field_value(fields, "视频链接", "") or "").strip()
            if video_url.startswith("http"):
                existing_by_video[video_url] = str(record.get("record_id") or "")

        rows = self.list_tasks(limit=max(limit, 200))
        if record_ids:
            allowed = set(record_ids)
            rows = [row for row in rows if row.get("record_id") in allowed]
        rows = [
            row
            for row in rows
            if str(row.get("生成视频链接") or "").startswith("http")
        ][:limit]

        created = 0
        updated = 0
        skipped = 0
        for row in rows:
            video_url = str(row.get("生成视频链接") or "").strip()
            task_name = str(row.get("生成任务名称") or "").strip()
            product_title = (
                task_name.rsplit(" - 视频任务 ", 1)[0]
                if " - 视频任务 " in task_name
                else task_name
            )
            source_ids = (
                row.get("关联素材记录IDs")
                if isinstance(row.get("关联素材记录IDs"), list)
                else []
            )
            fields = {
                "标题": str(row.get("生成标题") or product_title).strip(),
                "文案": str(row.get("生成文案") or "").strip(),
                "标签": str(row.get("关键词") or "").strip(),
                "商品标题": product_title,
                "视频链接": video_url,
            }
            if source_ids:
                fields["关联复刻素材"] = source_ids[:1]
            try:
                existing_id = existing_by_video.get(video_url)
                if existing_id:
                    try:
                        self.feishu.update_record(
                            self.settings.publish_table_id,
                            existing_id,
                            fields,
                        )
                    except Exception:
                        fields.pop("关联复刻素材", None)
                        self.feishu.update_record(
                            self.settings.publish_table_id,
                            existing_id,
                            fields,
                        )
                    updated += 1
                else:
                    create_fields = {
                        **fields,
                        "生成时间": self._clock_ms(),
                        "发布状态": "待发布",
                    }
                    try:
                        created_record = self.feishu.create_record(
                            self.settings.publish_table_id,
                            create_fields,
                        )
                    except Exception:
                        create_fields.pop("关联复刻素材", None)
                        created_record = self.feishu.create_record(
                            self.settings.publish_table_id,
                            create_fields,
                        )
                    existing_by_video[video_url] = str(
                        created_record.get("record_id") or ""
                    )
                    created += 1
            except Exception as exc:
                skipped += 1
                add_log(
                    "error",
                    f"发布表同步失败({row.get('record_id')}): {exc}",
                    "workflow",
                    job_id,
                )

        add_log(
            "success",
            f"发布表同步完成: 新增 {created} / 更新 {updated} / 跳过 {skipped}",
            "workflow",
            job_id,
        )
        return {"created": created, "updated": updated, "skipped": skipped}

    def list_part_time_publish_items(
        self,
        limit: int | None = None,
        days: int = 0,
    ) -> list[dict[str, Any]]:
        if not self.settings.publish_table_id:
            raise RuntimeError("缺少 FEISHU_PUBLISH_TABLE_ID")
        max_records = max(1, int(limit)) if limit is not None else None
        records = self.feishu.list_records(
            self.settings.part_time_table_id,
            max_records=max_records,
        )
        existing_by_part_time, existing_by_video = self._publish_record_indexes()
        cutoff = recent_window_start_ms(days)
        rows: list[dict[str, Any]] = []
        for row_number, record in enumerate(records, start=1):
            timestamp = record_timestamp_ms(
                record,
                (
                    "完成时间",
                    "接单时间",
                    "last_modified_time",
                    "modified_time",
                    "updated_time",
                    "created_time",
                ),
            )
            if cutoff and (not timestamp or timestamp < cutoff):
                continue
            fields = record.get("fields", {})
            task_status = str(field_value(fields, "任务状态", "") or "").strip()
            part_time_id = str(record.get("record_id") or "")
            video_url = first_http_url(field_value(fields, "视频地址", ""))
            if task_status != "已完成" or not video_url:
                continue
            review = str(field_value(fields, "审核", "") or "").strip()
            existing = (
                existing_by_part_time.get(part_time_id)
                or existing_by_video.get(video_url)
            )
            blocked_reason = ""
            if review in {"无效链接", "未去水印"}:
                blocked_reason = f"兼职表审核标记为{review}"
            rows.append({
                "行号": row_number,
                "record_id": part_time_id,
                "来源记录ID": str(
                    field_value(fields, "来源记录ID", "") or ""
                ).strip(),
                "文本": str(field_value(fields, "文本", "") or "").strip(),
                "任务状态": task_status,
                "审核": review,
                "视频地址": video_url,
                "目标国家": str(
                    field_value(fields, "目标国家", "") or ""
                ).strip(),
                "目标语言": str(
                    field_value(fields, "目标语言", "") or ""
                ).strip(),
                "已同步发布": bool(existing),
                "发布记录ID": existing[0] if existing else "",
                "发布状态": existing[1] if existing else "待审核",
                "可同步发布": not blocked_reason and not existing,
                "发布阻断原因": blocked_reason,
            })
        return rows

    def sync_part_time_publish_items(
        self,
        record_ids: list[str],
        limit: int = 200,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.settings.publish_table_id:
            raise RuntimeError("缺少 FEISHU_PUBLISH_TABLE_ID")
        selected_ids = list(
            dict.fromkeys(
                str(item).strip()
                for item in record_ids
                if str(item).strip()
            )
        )
        selected_ids = selected_ids[:max(1, min(int(limit), 200))]
        if not selected_ids:
            raise ValueError("请先选择要审核通过并同步发布表的兼职记录")

        selected_set = set(selected_ids)
        part_time_by_id = {
            str(record.get("record_id") or ""): record
            for record in self._completed_part_time_records()
            if str(record.get("record_id") or "") in selected_set
        }
        source_ids = {
            str(
                field_value(
                    record.get("fields", {}),
                    "来源记录ID",
                    "",
                )
                or ""
            ).strip()
            for record in part_time_by_id.values()
        }
        source_by_id = {
            str(record.get("record_id") or ""): record.get("fields", {})
            for record in self.feishu.list_records(self.settings.doubao_table_id)
            if str(record.get("record_id") or "") in source_ids
        }
        existing_by_part_time, existing_by_video = self._publish_record_indexes()

        created = 0
        updated = 0
        results: list[dict[str, Any]] = []
        for part_time_id in selected_ids:
            part_time_record = part_time_by_id.get(part_time_id)
            if not part_time_record:
                results.append({
                    "record_id": part_time_id,
                    "status": "failed",
                    "message": "兼职记录不存在，或尚未完成/缺少视频",
                })
                continue
            part_time_fields = part_time_record.get("fields", {})
            review = str(
                field_value(part_time_fields, "审核", "") or ""
            ).strip()
            if review in {"无效链接", "未去水印"}:
                results.append({
                    "record_id": part_time_id,
                    "status": "failed",
                    "message": f"兼职表审核标记为{review}",
                })
                continue
            video_url = first_http_url(
                field_value(part_time_fields, "视频地址", "")
            )
            source_id = str(
                field_value(part_time_fields, "来源记录ID", "") or ""
            ).strip()
            source_fields = source_by_id.get(source_id)
            if not source_fields:
                results.append({
                    "record_id": part_time_id,
                    "status": "failed",
                    "message": f"找不到豆包素材记录 {source_id or '（空）'}",
                })
                continue
            if not self.doubao_assets.describe(source_fields)["发布前置条件通过"]:
                results.append({
                    "record_id": part_time_id,
                    "status": "failed",
                    "message": (
                        "豆包素材前置条件未通过，请先完成图片转存、"
                        "人脸检测、商品 ID 和提示词"
                    ),
                })
                continue

            product_title = str(
                field_value(source_fields, "商品名称", "") or ""
            ).strip()
            target_country = (
                str(
                    field_value(part_time_fields, "目标国家", "") or ""
                ).strip()
                or str(
                    field_value(source_fields, "目标国家", "") or ""
                ).strip()
            )
            if not target_country:
                results.append({
                    "record_id": part_time_id,
                    "status": "failed",
                    "message": "兼职表和豆包素材表均缺少目标国家，不能同步发布表",
                })
                continue
            publish_fields = {
                "标题": str(
                    field_value(source_fields, "标题", product_title)
                    or product_title
                ).strip(),
                "商品标题": product_title,
                "商品ID": product_id_from_fields(source_fields),
                "目标国家": target_country,
                "目标语言": str(field_value(part_time_fields, "目标语言", "") or field_value(source_fields, "目标语言", "") or "").strip(),
                "Prompt方案Key": str(field_value(part_time_fields, "Prompt方案Key", "") or field_value(source_fields, "Prompt方案Key", "current_conversion") or "current_conversion").strip(),
                "Prompt方案名称": str(field_value(part_time_fields, "Prompt方案名称", "") or field_value(source_fields, "Prompt方案名称", "T1 当前转化模板") or "T1 当前转化模板").strip(),
                "Prompt配置版本": str(field_value(part_time_fields, "Prompt配置版本", "") or field_value(source_fields, "Prompt配置版本", "") or "").strip(),
                "Prompt执行模式": str(field_value(part_time_fields, "Prompt执行模式", "") or field_value(source_fields, "Prompt执行模式", "meta_generate") or "meta_generate").strip(),
                "视频链接": video_url,
                "文案": str(
                    field_value(source_fields, "文案", "") or ""
                ).strip(),
                "标签": str(
                    field_value(source_fields, "标签", "") or ""
                ).strip(),
                "豆包素材记录ID": source_id,
                "豆包兼职记录ID": part_time_id,
            }
            try:
                existing = (
                    existing_by_part_time.get(part_time_id)
                    or existing_by_video.get(video_url)
                )
                if existing:
                    self.feishu.update_record(
                        self.settings.publish_table_id,
                        existing[0],
                        publish_fields,
                    )
                    updated += 1
                    results.append({
                        "record_id": part_time_id,
                        "status": "updated",
                    })
                else:
                    created_record = self.feishu.create_record(
                        self.settings.publish_table_id,
                        {
                            **publish_fields,
                            "发布状态": "待发布",
                            "生成时间": self._clock_ms(),
                        },
                    )
                    publish_id = str(created_record.get("record_id") or "")
                    existing_by_part_time[part_time_id] = (
                        publish_id,
                        "待发布",
                    )
                    existing_by_video[video_url] = (publish_id, "待发布")
                    created += 1
                    results.append({
                        "record_id": part_time_id,
                        "status": "created",
                    })
            except Exception as exc:
                results.append({
                    "record_id": part_time_id,
                    "status": "failed",
                    "message": str(exc),
                })

        failed = sum(1 for item in results if item["status"] == "failed")
        result = {
            "processed": len(selected_ids),
            "eligible": created + updated,
            "created": created,
            "updated": updated,
            "failed": failed,
            "results": results,
        }
        add_log(
            "success" if not failed else "error",
            f"兼职成片审核发布完成: 新增 {created} / 更新 {updated} / 失败 {failed}",
            "workflow",
            job_id,
        )
        return result

    def doubao_publish_readiness(
        self,
        record_ids: list[str],
    ) -> dict[str, int]:
        allowed_sources, part_time_rows = self._doubao_publish_part_time_rows(
            record_ids
        )
        source_by_id = {
            str(record.get("record_id") or ""): record.get("fields", {})
            for record in self.feishu.list_records(self.settings.doubao_table_id)
            if str(record.get("record_id") or "") in allowed_sources
        }
        face_safe_sources = {
            source_id
            for source_id, fields in source_by_id.items()
            if self.doubao_assets.describe(fields)["人脸检测通过"]
        }
        product_id_sources = {
            source_id
            for source_id, fields in source_by_id.items()
            if product_id_from_fields(fields)
        }
        prompt_sources = {
            source_id
            for source_id, fields in source_by_id.items()
            if str(
                field_value(fields, "最终视频生成提示词", "") or ""
            ).strip()
        }
        publishable_sources = {
            source_id
            for source_id, fields in source_by_id.items()
            if self.doubao_assets.describe(fields)["发布前置条件通过"]
        }
        eligible_rows = [
            row
            for row in part_time_rows
            if row[1] in publishable_sources
        ]
        video_ready_sources = {
            source_id
            for _, source_id, _ in part_time_rows
        }
        ready_sources = {
            source_id
            for _, source_id, _ in eligible_rows
        }
        return {
            "selected_sources": len(allowed_sources),
            "ready_sources": len(ready_sources),
            "eligible_videos": len(eligible_rows),
            "pending_sources": len(allowed_sources - ready_sources),
            "blocked_face_sources": len(allowed_sources - face_safe_sources),
            "missing_product_id_sources": len(
                allowed_sources - product_id_sources
            ),
            "missing_prompt_sources": len(allowed_sources - prompt_sources),
            "missing_video_sources": len(
                allowed_sources - video_ready_sources
            ),
        }

    def sync_doubao_publish_items(
        self,
        record_ids: list[str],
        limit: int = 200,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.settings.publish_table_id:
            raise RuntimeError("缺少 FEISHU_PUBLISH_TABLE_ID")

        selected_ids = list(
            dict.fromkeys(
                str(item).strip()
                for item in record_ids
                if str(item).strip()
            )
        )
        selected_ids = selected_ids[:max(1, min(int(limit), 200))]
        allowed_sources, part_time_rows = self._doubao_publish_part_time_rows(
            selected_ids
        )
        part_time_rows = part_time_rows[:max(1, min(int(limit), 200))]

        source_by_id = {
            str(record.get("record_id") or ""): record.get("fields", {})
            for record in self.feishu.list_records(self.settings.doubao_table_id)
            if str(record.get("record_id") or "") in allowed_sources
        }
        publishable_sources = {
            source_id
            for source_id, fields in source_by_id.items()
            if self.doubao_assets.describe(fields)["发布前置条件通过"]
        }
        rows_by_source: dict[
            str,
            list[tuple[dict[str, Any], str]],
        ] = {}
        for part_time_record, source_id, video_url in part_time_rows:
            rows_by_source.setdefault(source_id, []).append(
                (part_time_record, video_url)
            )
        eligible_rows = [
            row
            for row in part_time_rows
            if row[1] in publishable_sources
        ]

        existing_by_part_time: dict[str, str] = {}
        existing_by_video: dict[str, str] = {}
        for record in self.feishu.list_records(self.settings.publish_table_id):
            fields = record.get("fields", {})
            publish_id = str(record.get("record_id") or "")
            part_time_id = str(
                field_value(fields, "豆包兼职记录ID", "") or ""
            ).strip()
            video_url = first_http_url(
                field_value(fields, "视频链接", "")
            )
            if part_time_id:
                existing_by_part_time[part_time_id] = publish_id
            if video_url:
                existing_by_video[video_url] = publish_id

        created = 0
        updated = 0
        skipped = 0
        results: list[dict[str, Any]] = []
        for source_id in selected_ids:
            source_fields = source_by_id.get(source_id)
            if not source_fields:
                message = (
                    "在豆包素材表中找不到该记录，可能已被删除或行号已变化"
                )
                results.append({
                    "record_id": source_id,
                    "status": "failed",
                    "stage": "读取素材",
                    "message": message,
                })
                add_log(
                    "error",
                    f"发布表同步失败({source_id})：{message}",
                    "workflow",
                    job_id,
                )
                continue
            if source_id not in publishable_sources:
                message = (
                    "素材发布前置条件未通过，请先完成图片转存、人脸检测、"
                    "商品 ID 和提示词"
                )
                results.append({
                    "record_id": source_id,
                    "status": "failed",
                    "stage": "素材前置检查",
                    "message": message,
                })
                add_log(
                    "error",
                    f"发布表同步失败({source_id})：{message}",
                    "workflow",
                    job_id,
                )
                continue

            source_rows = rows_by_source.get(source_id) or []
            if not source_rows:
                message = "对应兼职成片尚未完成，或视频链接无效"
                results.append({
                    "record_id": source_id,
                    "status": "failed",
                    "stage": "兼职成片检查",
                    "message": message,
                })
                add_log(
                    "error",
                    f"发布表同步失败({source_id})：{message}",
                    "workflow",
                    job_id,
                )
                continue

            product_title = str(
                field_value(source_fields, "商品名称", "") or ""
            ).strip()
            product_id = product_id_from_fields(source_fields)
            source_created = 0
            source_updated = 0
            source_errors: list[str] = []
            for part_time_record, video_url in source_rows:
                part_time_id = str(part_time_record.get("record_id") or "")
                part_time_fields = part_time_record.get("fields", {})
                target_country = str(
                    field_value(part_time_fields, "目标国家", "")
                    or field_value(source_fields, "目标国家", "")
                    or ""
                ).strip()
                fields = {
                    "标题": str(
                        field_value(
                            source_fields,
                            "标题",
                            product_title,
                        )
                        or product_title
                    ).strip(),
                    "商品标题": product_title,
                    "商品ID": product_id,
                    "目标国家": target_country,
                    "目标语言": str(field_value(part_time_fields, "目标语言", "") or field_value(source_fields, "目标语言", "") or "").strip(),
                    "Prompt方案Key": str(field_value(part_time_fields, "Prompt方案Key", "") or field_value(source_fields, "Prompt方案Key", "current_conversion") or "current_conversion").strip(),
                    "Prompt方案名称": str(field_value(part_time_fields, "Prompt方案名称", "") or field_value(source_fields, "Prompt方案名称", "T1 当前转化模板") or "T1 当前转化模板").strip(),
                    "Prompt配置版本": str(field_value(part_time_fields, "Prompt配置版本", "") or field_value(source_fields, "Prompt配置版本", "") or "").strip(),
                    "Prompt执行模式": str(field_value(part_time_fields, "Prompt执行模式", "") or field_value(source_fields, "Prompt执行模式", "meta_generate") or "meta_generate").strip(),
                    "视频链接": video_url,
                    "文案": str(
                        field_value(source_fields, "文案", "") or ""
                    ).strip(),
                    "标签": str(
                        field_value(source_fields, "标签", "") or ""
                    ).strip(),
                    "豆包素材记录ID": source_id,
                    "豆包兼职记录ID": part_time_id,
                }
                try:
                    existing_id = (
                        existing_by_part_time.get(part_time_id)
                        or existing_by_video.get(video_url)
                    )
                    if existing_id:
                        self.feishu.update_record(
                            self.settings.publish_table_id,
                            existing_id,
                            fields,
                        )
                        updated += 1
                        source_updated += 1
                    else:
                        created_record = self.feishu.create_record(
                            self.settings.publish_table_id,
                            {
                                **fields,
                                "发布状态": "待发布",
                                "生成时间": self._clock_ms(),
                            },
                        )
                        publish_id = str(
                            created_record.get("record_id") or ""
                        )
                        existing_by_part_time[part_time_id] = publish_id
                        existing_by_video[video_url] = publish_id
                        created += 1
                        source_created += 1
                except Exception as exc:
                    skipped += 1
                    source_errors.append(f"兼职记录 {part_time_id}: {exc}")
                    add_log(
                        "error",
                        f"发布表同步失败({part_time_id}): {exc}",
                        "workflow",
                        job_id,
                    )

            if source_errors:
                results.append({
                    "record_id": source_id,
                    "status": "failed",
                    "stage": "写入发布表",
                    "message": "；".join(source_errors),
                    "created": source_created,
                    "updated": source_updated,
                })
            else:
                results.append({
                    "record_id": source_id,
                    "status": "created" if source_created else "updated",
                    "created": source_created,
                    "updated": source_updated,
                    "message": f"对应 {len(source_rows)} 条已完成兼职成片已同步",
                })

        failed = sum(
            1
            for item in results
            if item.get("status") == "failed"
        )
        result = {
            "selected": len(selected_ids),
            "processed": len(selected_ids),
            "eligible": len(eligible_rows),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "results": results,
        }
        add_log(
            "success" if failed == 0 else "error",
            (
                f"豆包发布表同步完成: 素材 {len(selected_ids)} 条 / "
                f"新增 {created} / 更新 {updated} / 失败 {failed}"
            ),
            "workflow",
            job_id,
        )
        return result

    def _doubao_publish_part_time_rows(
        self,
        record_ids: list[str],
    ) -> tuple[set[str], list[tuple[dict[str, Any], str, str]]]:
        allowed_sources = {
            str(item).strip()
            for item in record_ids
            if str(item).strip()
        }
        if not allowed_sources:
            raise ValueError("请先选择要同步的豆包素材记录")
        part_time_rows: list[
            tuple[dict[str, Any], str, str]
        ] = []
        records = self.feishu.list_records_filtered(
            self.settings.part_time_table_id,
            field_names=[
                "来源记录ID",
                "视频地址",
                "任务状态",
                "审核",
                "目标国家",
                "目标语言",
            ],
            filter_config={
                "logic": "and",
                "conditions": [
                    ["任务状态", "intersects", ["已完成"]],
                    ["视频地址", "non_empty"],
                ],
            },
        )
        for record in records:
            fields = record.get("fields", {})
            source_id = str(
                field_value(fields, "来源记录ID", "") or ""
            ).strip()
            status = str(
                field_value(fields, "任务状态", "") or ""
            ).strip()
            review = str(field_value(fields, "审核", "") or "").strip()
            video_url = first_http_url(
                field_value(fields, "视频地址", "")
            )
            if (
                source_id in allowed_sources
                and status == "已完成"
                and review not in {"无效链接", "未去水印"}
                and video_url
            ):
                part_time_rows.append((record, source_id, video_url))
        return allowed_sources, part_time_rows

    def _completed_part_time_records(
        self,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.feishu.list_records_filtered(
            self.settings.part_time_table_id,
            field_names=[
                "来源记录ID",
                "文本",
                "任务状态",
                "审核",
                "视频地址",
                "目标国家",
                "目标语言",
            ],
            filter_config={
                "logic": "and",
                "conditions": [
                    ["任务状态", "intersects", ["已完成"]],
                    ["视频地址", "non_empty"],
                ],
            },
            max_records=limit,
        )

    def _publish_record_indexes(
        self,
    ) -> tuple[
        dict[str, tuple[str, str]],
        dict[str, tuple[str, str]],
    ]:
        by_part_time: dict[str, tuple[str, str]] = {}
        by_video: dict[str, tuple[str, str]] = {}
        records = self.feishu.list_records_filtered(
            self.settings.publish_table_id,
            field_names=["豆包兼职记录ID", "视频链接", "发布状态"],
            filter_config={
                "logic": "and",
                "conditions": [["视频链接", "non_empty"]],
            },
        )
        for record in records:
            fields = record.get("fields", {})
            record_id = str(record.get("record_id") or "")
            publish_status = (
                str(
                    field_value(fields, "发布状态", "") or ""
                ).strip()
                or "待发布"
            )
            part_time_id = str(
                field_value(fields, "豆包兼职记录ID", "") or ""
            ).strip()
            video_url = first_http_url(
                field_value(fields, "视频链接", "")
            )
            if part_time_id:
                by_part_time[part_time_id] = (
                    record_id,
                    publish_status,
                )
            if video_url:
                by_video[video_url] = (record_id, publish_status)
        return by_part_time, by_video
