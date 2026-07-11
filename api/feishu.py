import time
import mimetypes
from pathlib import Path
from threading import Lock
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
        self._token_lock = Lock()
        self._session = requests.Session()

    def _tenant_token(self) -> str:
        with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token

            resp = self._session.post(
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
        method = method.upper()
        headers = kwargs.pop("headers", self.headers())
        timeout = kwargs.pop("timeout", 60)
        attempts = 3 if method in {"GET", "PUT", "DELETE"} else 1
        last_data: dict[str, Any] = {}
        for attempt in range(attempts):
            resp = self._session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
            try:
                data = resp.json()
            except ValueError:
                data = {"code": resp.status_code, "msg": resp.text[:500]}
            if resp.status_code < 400 and data.get("code", 0) == 0:
                return data.get("data", data)
            last_data = data
            transient = resp.status_code == 429 or resp.status_code >= 500 or data.get("code") == 1254291
            if not transient or attempt + 1 >= attempts:
                break
            time.sleep(0.25 * (2 ** attempt))
        raise FeishuError(last_data.get("msg") or str(last_data)[:500])

    def list_records_page(self, table_id: str, page_size: int = 100, page_token: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        data = self._request(
            "GET",
            f"/bitable/v1/apps/{self.settings.feishu_app_token}/tables/{table_id}/records",
            params=params,
        )
        return {
            "items": data.get("items", []),
            "has_more": bool(data.get("has_more")),
            "page_token": data.get("page_token", ""),
        }

    def list_records(self, table_id: str, page_size: int = 100, max_records: int | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            page = self.list_records_page(table_id, page_size=page_size, page_token=page_token)
            items.extend(page["items"])
            if max_records and len(items) >= max_records:
                return items[:max_records]
            if not page["has_more"]:
                break
            page_token = page["page_token"]
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

    def upload_base_attachment(
        self,
        table_id: str,
        record_id: str,
        field_id_or_name: str,
        file_path: Path,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        mime = content_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        file_size = file_path.stat().st_size
        with file_path.open("rb") as fh:
            data = self._request(
                "POST",
                "/drive/v1/medias/upload_all",
                headers={"Authorization": f"Bearer {self._tenant_token()}"},
                data={
                    "file_name": file_path.name,
                    "parent_node": self.settings.feishu_app_token,
                    "parent_type": "bitable_file",
                    "size": str(file_size),
                },
                files={"file": (file_path.name, fh, mime)},
                timeout=240,
            )
        file_token = (
            data.get("file_token")
            or data.get("token")
            or (data.get("file") or {}).get("file_token")
        )
        if not file_token:
            raise FeishuError(f"Feishu attachment upload returned no file_token: {data}")
        append_data = self._request(
            "POST",
            f"/base/v3/bases/{self.settings.feishu_app_token}/tables/{table_id}/append_attachments",
            json={
                "attachments": {
                    record_id: {
                        field_id_or_name: [{"file_token": file_token}],
                    }
                }
            },
        )
        return {"file_token": file_token, "append": append_data}


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


def attachment_count(fields: dict[str, Any], name: str) -> int:
    value = fields.get(name)
    if not isinstance(value, list):
        return 0
    return len([
        item
        for item in value
        if isinstance(item, dict) and (item.get("file_token") or item.get("name") or item.get("url"))
    ])
