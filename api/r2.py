from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import boto3
import requests
from botocore.config import Config

from .config import Settings


class R2Storage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.has_direct_r2:
            self.client = boto3.client(
                "s3",
                endpoint_url=settings.r2_endpoint,
                aws_access_key_id=settings.r2_access_key,
                aws_secret_access_key=settings.r2_secret_key,
                region_name="auto",
                config=Config(signature_version="s3v4"),
            )

    def _build_key(self, filename: str, content_type: str) -> str:
        suffix = Path(filename).suffix or ".bin"
        date_prefix = datetime.utcnow().strftime("%Y%m%d")
        media_type = "videos" if content_type.startswith("video/") else "images"
        return f"{self.settings.r2_prefix}/{date_prefix}/{media_type}/{uuid4().hex}{suffix}"

    def is_managed_url(self, url: str) -> bool:
        candidate = urlparse(str(url or "").strip())
        if candidate.scheme not in {"http", "https"} or not candidate.netloc:
            return False
        for base_url in (self.settings.upload_api_url, self.settings.r2_public_url):
            base = urlparse(str(base_url or "").strip())
            if candidate.scheme == base.scheme and candidate.netloc == base.netloc:
                return True
        return False

    def _upload_via_gateway(self, data: bytes, key: str, content_type: str) -> dict:
        gateway_key = f"{self.settings.upload_prefix}/{key}".strip("/")
        resp = requests.post(
            f"{self.settings.upload_api_url}/upload",
            params={"key": gateway_key},
            headers={
                "x-upload-key": self.settings.upload_api_key,
                "Content-Type": content_type or "application/octet-stream",
            },
            data=data,
            timeout=180,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Upload gateway failed: HTTP {resp.status_code}: {resp.text[:300]}")
        result = resp.json()
        return {
            "key": result.get("key", gateway_key),
            "url": result.get("url"),
            "size": result.get("size", len(data)),
        }

    def upload_bytes(self, data: bytes, filename: str, content_type: str) -> dict:
        key = self._build_key(filename, content_type)
        if self.settings.has_upload_gateway:
            return self._upload_via_gateway(data, key, content_type)
        if not self.client:
            raise RuntimeError("R2 storage is not configured")
        self.client.put_object(
            Bucket=self.settings.r2_bucket,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
            CacheControl="public, max-age=604800",
        )
        return {
            "key": key,
            "url": f"{self.settings.r2_public_url}/{key}",
            "size": len(data),
        }

    def upload_file(self, path: Path, content_type: str) -> dict:
        return self.upload_bytes(path.read_bytes(), path.name, content_type)
