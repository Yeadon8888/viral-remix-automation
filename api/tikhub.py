import re
import time
from typing import Any

import requests

from .config import Settings


URL_PATTERN = re.compile(r"https?://[^\s<>\"']+(?:douyin|tiktok|v\.douyin)[^\s<>\"']*", re.I)


class TikHubClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = "https://api.tikhub.io"

    def extract_url(self, text: str) -> str | None:
        match = URL_PATTERN.search(text or "")
        if not match:
            return None
        return match.group(0).rstrip(".,，。）)】』\"'")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.tikhub_api_key}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        }

    def _get(self, path: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = requests.get(f"{self.base_url}{path}", headers=self._headers(), timeout=30)
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                return resp.json()
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2)
        raise RuntimeError(f"TikHub request failed: {last_error}")

    def _try_get(self, path: str) -> tuple[dict[str, Any] | None, str | None]:
        try:
            return self._get(path), None
        except Exception as exc:
            return None, str(exc)

    def _resolve_short_url(self, url: str) -> str:
        if "/t/" not in url:
            return url
        try:
            resp = requests.head(
                url,
                allow_redirects=True,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"},
            )
            return resp.url or url
        except Exception:
            return url

    def _extract_video_url(self, data: dict[str, Any]) -> str | None:
        for key in ("nwm_video_url_HQ", "nwm_video_url"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                urls = value.get("url_list")
                if isinstance(urls, list) and urls and isinstance(urls[0], str):
                    return urls[0]
        video = data.get("video")
        if isinstance(video, dict):
            for key in ("play_addr", "download_addr"):
                addr = video.get(key)
                if isinstance(addr, dict):
                    urls = addr.get("url_list")
                    if isinstance(urls, list) and urls and isinstance(urls[0], str):
                        return urls[0]
        return None

    def resolve_video_url(self, share_text_or_url: str) -> str:
        raw_url = self.extract_url(share_text_or_url) or share_text_or_url
        resolved = self._resolve_short_url(raw_url)
        if "tiktok.com/@" in resolved and not re.search(r"/video/\d+", resolved):
            raise RuntimeError("TikTok 链接不是单条视频链接，请填写包含 /video/ 的具体爆款视频链接")
        encoded = requests.utils.quote(resolved, safe="")

        video_url = None
        errors = []
        result, error = self._try_get(f"/api/v1/hybrid/video_data?url={encoded}&minimal=true")
        if error:
            errors.append(f"hybrid: {error}")
        if result:
            code = result.get("code") or (result.get("detail") or {}).get("code")
            if code == 200:
                data = ((result.get("data") or {}).get("video_data") or {})
                video_url = self._extract_video_url(data)

        if not video_url:
            match = re.search(r"/video/(\d+)", resolved)
            if match:
                result, error = self._try_get(f"/api/v1/tiktok/app/v3/fetch_one_video?aweme_id={match.group(1)}")
                if error:
                    errors.append(f"aweme_id: {error}")
                if result and result.get("code") == 200:
                    detail = ((result.get("data") or {}).get("aweme_detail") or {})
                    video_url = self._extract_video_url(detail)

        if not video_url:
            result, error = self._try_get(f"/api/v1/tiktok/app/v3/fetch_one_video_by_share_url?share_url={encoded}")
            if error:
                errors.append(f"share_url: {error}")
            if result and result.get("code") == 200:
                detail = ((result.get("data") or {}).get("aweme_detail") or {})
                video_url = self._extract_video_url(detail)

        if not video_url:
            suffix = f": {' | '.join(errors)}" if errors else ""
            raise RuntimeError(f"TikHub returned no downloadable video URL{suffix}")
        return video_url

    def download_video_url(self, video_url: str) -> tuple[bytes, str]:
        headers = {
            "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
            "Referer": "https://www.tiktok.com/",
            "User-Agent": "Mozilla/5.0",
        }
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = requests.get(video_url, headers=headers, timeout=120)
                if resp.status_code >= 400:
                    raise RuntimeError(f"download HTTP {resp.status_code}")
                mime_type = resp.headers.get("content-type", "video/mp4").split(";")[0]
                if len(resp.content) < 100_000:
                    raise RuntimeError("downloaded video is too small")
                return resp.content, mime_type
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 + attempt)
        raise RuntimeError(f"TikHub video download failed: {last_error}")

    def download_video(self, share_text_or_url: str) -> tuple[bytes, str]:
        video_url = self.resolve_video_url(share_text_or_url)
        return self.download_video_url(video_url)
