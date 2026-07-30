from dataclasses import dataclass
import json
from threading import Lock
import time
from typing import Any, Callable


INDEX_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class DoubaoRecordRange:
    records: list[dict[str, Any]]
    row_numbers: dict[str, int]
    total: int


class DoubaoRecordIndex:
    def __init__(
        self,
        settings,
        feishu,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.settings = settings
        self.feishu = feishu
        self._clock = clock
        self._lock = Lock()
        self._record_ids: list[str] = []
        self._cached_at = 0.0
        self._redis = None

    def range(
        self,
        start_row: int,
        end_row: int,
        *,
        force_refresh: bool = False,
    ) -> DoubaoRecordRange:
        start = max(1, min(int(start_row), int(end_row)))
        end = max(int(start_row), int(end_row))
        record_ids, total = self._snapshot(required_end=end, force_refresh=force_refresh)
        selected_ids = record_ids[start - 1:end]
        row_numbers = {
            record_id: start + index
            for index, record_id in enumerate(selected_ids)
        }
        records: list[dict[str, Any]] = []
        for index in range(0, len(selected_ids), 100):
            records.extend(
                self.feishu.batch_get_records(
                    self.settings.doubao_table_id,
                    selected_ids[index:index + 100],
                )
            )
        records_by_id = {
            str(record.get("record_id") or ""): record
            for record in records
            if str(record.get("record_id") or "")
        }
        return DoubaoRecordRange(
            records=[
                records_by_id[record_id]
                for record_id in selected_ids
                if record_id in records_by_id
            ],
            row_numbers=row_numbers,
            total=total,
        )

    def row_numbers(self, *, force_refresh: bool = False) -> dict[str, int]:
        record_ids, _total = self._snapshot(force_refresh=force_refresh)
        return {
            record_id: index
            for index, record_id in enumerate(record_ids, start=1)
        }

    def invalidate(self) -> None:
        self._record_ids = []
        self._cached_at = 0.0
        redis_url = str(getattr(self.settings, "redis_url", "") or "")
        if not redis_url:
            return
        try:
            self._redis_client().delete(self._cache_key())
        except Exception:
            return

    def _snapshot(
        self,
        *,
        required_end: int = 0,
        force_refresh: bool = False,
    ) -> tuple[list[str], int]:
        with self._lock:
            if force_refresh:
                self.invalidate()
            cached_ids = self._load()
            cache_fresh = bool(
                self._cached_at
                and self._clock() - self._cached_at < INDEX_CACHE_TTL_SECONDS
            )
            if cached_ids and cache_fresh and (not required_end or required_end <= len(cached_ids)):
                return cached_ids, len(cached_ids)

            first_page = self.feishu.list_records_page(
                self.settings.doubao_table_id,
                page_size=500,
                field_names=["商品名称"],
            )
            total = int(first_page.get("total") or 0)
            record_ids = self._record_ids_from(first_page["items"])
            page = first_page
            while page["has_more"]:
                page_token = str(page.get("page_token") or "")
                if not page_token:
                    break
                page = self.feishu.list_records_page(
                    self.settings.doubao_table_id,
                    page_size=500,
                    page_token=page_token,
                    field_names=["商品名称"],
                )
                record_ids.extend(self._record_ids_from(page["items"]))
            self._save(record_ids)
            return record_ids, total or len(record_ids)

    @staticmethod
    def _record_ids_from(records: list[dict[str, Any]]) -> list[str]:
        return [
            str(record.get("record_id") or "")
            for record in records
            if str(record.get("record_id") or "")
        ]

    def _load(self) -> list[str]:
        if self._record_ids:
            return list(self._record_ids)
        redis_url = str(getattr(self.settings, "redis_url", "") or "")
        if not redis_url:
            return []
        try:
            data = json.loads(str(self._redis_client().get(self._cache_key()) or "[]"))
            if isinstance(data, dict):
                raw_ids = data.get("record_ids", [])
                self._cached_at = float(data.get("cached_at") or 0)
            else:
                raw_ids = data
                self._cached_at = 0.0
            self._record_ids = [
                str(record_id)
                for record_id in raw_ids
                if str(record_id)
            ]
            return list(self._record_ids)
        except Exception:
            return []

    def _save(self, record_ids: list[str]) -> None:
        self._record_ids = [
            str(record_id)
            for record_id in record_ids
            if str(record_id)
        ]
        self._cached_at = self._clock()
        redis_url = str(getattr(self.settings, "redis_url", "") or "")
        if not redis_url:
            return
        try:
            self._redis_client().set(
                self._cache_key(),
                json.dumps(
                    {
                        "record_ids": self._record_ids,
                        "cached_at": self._cached_at,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                ex=86400,
            )
        except Exception:
            return

    def _redis_client(self):
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(
                str(getattr(self.settings, "redis_url", "") or ""),
                decode_responses=True,
            )
        return self._redis

    def _cache_key(self) -> str:
        return f"viral-remix:doubao-row-ids:{self.settings.doubao_table_id}:v1"
