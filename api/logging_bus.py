from collections import deque
from datetime import datetime
from threading import Lock


_logs = deque(maxlen=500)
_lock = Lock()


def add_log(level: str, message: str, source: str = "system", job_id: str | None = None) -> None:
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "source": source,
        "message": message,
        "job_id": job_id,
    }
    with _lock:
        _logs.append(entry)
    print(f"[{entry['timestamp']}] [{level.upper()}] [{source}] {message}", flush=True)


def recent_logs(limit: int = 100) -> list[dict]:
    with _lock:
        items = list(_logs)
    return items[-limit:]
