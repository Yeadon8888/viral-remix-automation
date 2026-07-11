import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Operator:
    id: str
    role: str


def resolve_operator(token: str, legacy_admin_token: str = "", users_json: str = "") -> Operator | None:
    value = str(token or "").strip()
    if not value:
        return None
    try:
        configured = json.loads(users_json or "{}")
    except (TypeError, ValueError):
        configured = {}
    if isinstance(configured, dict):
        entry = configured.get(value)
        if isinstance(entry, str) and entry.strip():
            return Operator(entry.strip(), "operator")
        if isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("id") or "").strip()
            role = str(entry.get("role") or "operator").strip().lower()
            if name and role in {"admin", "operator", "viewer"}:
                return Operator(name, role)
    if legacy_admin_token and value == legacy_admin_token:
        return Operator("legacy-admin", "admin")
    return None
