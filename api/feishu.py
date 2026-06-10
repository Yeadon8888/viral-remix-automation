import time
from pathlib import Path
from typing import Any

import requests

from .config import Settings


class FeishuError(RuntimeError):
    pass


class FeishuClient:
    base_url = "https://open.feishu.cn/open-apis"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._token = ""
        self._token_expires_at = 0.0

    def _tenant_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token

        resp = requests.post(
            f"{self.base_url}/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self.settings.feishu_app_id,
                "app_secret": self.settings.feishu_app_secret,
            },
            timeout=30,
        )
        data = resp.json()
        if resp.status_code != 200 or data.get("code") != 0:
            raise FeishuError(f"Feishu auth failed: {data.get('msg') or resp.text[:300]}")
        self._token = data["tenant_access_token"]
        self._token_expires_at = time.time() + int(data.get("expire", 7200)) - 300
        return self._token

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._tenant_token()}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        headers = kwargs.pop("headers", self.headers())
        resp = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            timeout=kwargs.pop("timeout", 60),
            **kwargs,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {"code": resp.status_code, "msg": resp.text[:500]}
        if resp.status_code >= 400 or data.get("code", 0) != 0:
            raise FeishuError(data.get("msg") or str(data)[:500])
        return data.get("data", data)

    def list_records(self, table_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET",
                f"/bitable/v1/apps/{self.settings.feishu_app_token}/tables/{table_id}/records",
                params=params,
            )
            items.extend(data.get("items", []))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
            if not page_token:
                break
        return items

    def get_record(self, table_id: str, record_id: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/bitable/v1/apps/{self.settings.feishu_app_token}/tables/{table_id}/records/{record_id}",
        )
        return data.get("record", data)

    def update_record(self, table_id: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        data = self._request(
            "PUT",
            f"/bitable/v1/apps/{self.settings.feishu_app_token}/tables/{table_id}/records/{record_id}",
            json={"fields": fields},
        )
        return data.get("record", data)

    def create_record(self, table_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        data = self._request(
            "POST",
            f"/bitable/v1/apps/{self.settings.feishu_app_token}/tables/{table_id}/records",
            json={"fields": fields},
        )
        return data.get("record", data)

    def download_media(self, file_token: str, output_path: Path) -> tuple[Path, str]:
        resp = requests.get(
            f"{self.base_url}/drive/v1/medias/{file_token}/download",
            headers={"Authorization": f"Bearer {self._tenant_token()}"},
            timeout=120,
        )
        if resp.status_code >= 400:
            raise FeishuError(f"Feishu media download failed: HTTP {resp.status_code} {resp.text[:200]}")
        content_type = resp.headers.get("content-type", "application/octet-stream").split(";")[0]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)
        return output_path, content_type


def field_value(fields: dict[str, Any], name: str, default: Any = "") -> Any:
    value = fields.get(name, default)
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0]
    if isinstance(value, dict) and isinstance(value.get("link"), str):
        return value["link"]
    return value


def first_attachment(fields: dict[str, Any], name: str) -> dict[str, Any] | None:
    value = fields.get(name)
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return first
    return None
