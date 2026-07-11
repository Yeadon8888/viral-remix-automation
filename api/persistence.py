import time
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class Reservation:
    acquired: bool
    target_record_id: str = ""
    owner: str = ""


class InMemoryIdempotencyLedger:
    def __init__(self):
        self._lock = Lock()
        self._entries: dict[str, dict] = {}

    def reserve(self, scope: str, owner: str, ttl_seconds: int = 3600) -> Reservation:
        now = time.time()
        with self._lock:
            entry = self._entries.get(scope)
            if entry and entry.get("target_record_id"):
                return Reservation(False, str(entry["target_record_id"]), str(entry.get("owner") or ""))
            if not entry or float(entry.get("expires_at") or 0) <= now or entry.get("owner") == owner:
                self._entries[scope] = {
                    "owner": owner,
                    "target_record_id": "",
                    "expires_at": now + ttl_seconds,
                }
                return Reservation(True, owner=owner)
            return Reservation(False, owner=str(entry.get("owner") or ""))

    def complete(self, scope: str, owner: str, target_record_id: str) -> None:
        with self._lock:
            entry = self._entries.get(scope)
            if entry and entry.get("owner") not in (None, "", owner) and not entry.get("target_record_id"):
                raise RuntimeError("幂等预留已被其他任务持有")
            self._entries[scope] = {
                "owner": owner,
                "target_record_id": target_record_id,
                "expires_at": float("inf"),
            }

    def release(self, scope: str, owner: str) -> None:
        with self._lock:
            entry = self._entries.get(scope)
            if entry and entry.get("owner") == owner and not entry.get("target_record_id"):
                self._entries.pop(scope, None)

    def audit(self, operator: str, action: str, resource: str, status: str, detail: str = "") -> None:
        return None

    def health(self) -> dict:
        return {"backend": "memory", "persistent": False}


class PostgresIdempotencyLedger:
    def __init__(self, database_url: str, connect_factory=None, ensure_schema: bool = True):
        self.database_url = database_url
        self._connect_factory = connect_factory
        if ensure_schema:
            self.ensure_schema()

    def _connect(self):
        if self._connect_factory:
            return self._connect_factory()
        import psycopg

        return psycopg.connect(self.database_url)

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS automation_idempotency (
                        scope TEXT PRIMARY KEY,
                        owner TEXT NOT NULL,
                        target_record_id TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL DEFAULT 'pending',
                        lease_expires_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS automation_audit (
                        id BIGSERIAL PRIMARY KEY,
                        operator_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        resource TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL,
                        detail TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS automation_audit_created_at_idx
                    ON automation_audit (created_at DESC)
                """)

    def reserve(self, scope: str, owner: str, ttl_seconds: int = 3600) -> Reservation:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO automation_idempotency
                        (scope, owner, state, lease_expires_at)
                    VALUES (%s, %s, 'pending', NOW() + make_interval(secs => %s))
                    ON CONFLICT (scope) DO NOTHING
                    RETURNING scope
                    """,
                    (scope, owner, ttl_seconds),
                )
                if cursor.fetchone():
                    return Reservation(True, owner=owner)
                cursor.execute(
                    """
                    SELECT owner, target_record_id, lease_expires_at < NOW()
                    FROM automation_idempotency
                    WHERE scope = %s
                    FOR UPDATE
                    """,
                    (scope,),
                )
                current_owner, target_record_id, expired = cursor.fetchone()
                if target_record_id:
                    return Reservation(False, str(target_record_id), str(current_owner))
                if current_owner == owner or expired:
                    cursor.execute(
                        """
                        UPDATE automation_idempotency
                        SET owner = %s,
                            state = 'pending',
                            lease_expires_at = NOW() + make_interval(secs => %s),
                            updated_at = NOW()
                        WHERE scope = %s
                        """,
                        (owner, ttl_seconds, scope),
                    )
                    return Reservation(True, owner=owner)
                return Reservation(False, owner=str(current_owner))

    def complete(self, scope: str, owner: str, target_record_id: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO automation_idempotency
                        (scope, owner, target_record_id, state, lease_expires_at)
                    VALUES (%s, %s, %s, 'completed', NULL)
                    ON CONFLICT (scope) DO UPDATE
                    SET target_record_id = EXCLUDED.target_record_id,
                        state = 'completed',
                        lease_expires_at = NULL,
                        updated_at = NOW()
                    WHERE automation_idempotency.owner = EXCLUDED.owner
                       OR automation_idempotency.target_record_id = EXCLUDED.target_record_id
                    RETURNING scope
                    """,
                    (scope, owner, target_record_id),
                )
                if not cursor.fetchone():
                    raise RuntimeError("幂等预留已被其他任务持有")

    def release(self, scope: str, owner: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM automation_idempotency
                    WHERE scope = %s AND owner = %s AND target_record_id = ''
                    """,
                    (scope, owner),
                )

    def audit(self, operator: str, action: str, resource: str, status: str, detail: str = "") -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO automation_audit (operator_id, action, resource, status, detail)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (operator, action, resource, status, detail[:4000]),
                )

    def health(self) -> dict:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        return {"backend": "postgres", "persistent": True}


_persistence_lock = Lock()
_persistence = None


def get_persistence(settings):
    global _persistence
    with _persistence_lock:
        if _persistence is None:
            if getattr(settings, "database_url", ""):
                _persistence = PostgresIdempotencyLedger(settings.database_url)
            elif getattr(settings, "coordination_required", False):
                raise RuntimeError("生产环境缺少 DATABASE_URL，已拒绝启用不安全的内存幂等台账")
            else:
                _persistence = InMemoryIdempotencyLedger()
        return _persistence


def reset_persistence_for_tests() -> None:
    global _persistence
    with _persistence_lock:
        _persistence = None
