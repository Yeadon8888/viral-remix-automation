from datetime import datetime, time as datetime_time, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

from .feishu import field_value


def now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def recent_window_start_ms(days: int, now: datetime | None = None) -> int:
    days = max(0, int(days))
    if not days:
        return 0
    timezone = ZoneInfo("Asia/Shanghai")
    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)
    else:
        current = current.astimezone(timezone)
    first_day = current.date() - timedelta(days=days - 1)
    return int(
        datetime.combine(
            first_day,
            datetime_time.min,
            tzinfo=timezone,
        ).timestamp() * 1000
    )


def record_timestamp_ms(
    record: dict[str, Any],
    preferred_fields: tuple[str, ...],
) -> int:
    fields = record.get("fields", {})
    for name in preferred_fields:
        value = field_value(fields, name, None) if name in fields else record.get(name)
        timestamp = _timestamp_ms(value)
        if timestamp:
            return timestamp
    return 0


def first_http_url(value: Any) -> str:
    text = str(value or "").strip()
    markdown = re.search(r"\[[^\]]*\]\((https?://[^)\s]+)\)", text)
    if markdown:
        return markdown.group(1)
    plain = re.search(r"https?://[^\s<>\]]+", text)
    return plain.group(0).rstrip(").,;") if plain else ""


def _timestamp_ms(value: Any) -> int:
    try:
        timestamp = int(float(value))
    except (TypeError, ValueError):
        return 0
    if timestamp <= 0:
        return 0
    return timestamp * 1000 if timestamp < 100_000_000_000 else timestamp
