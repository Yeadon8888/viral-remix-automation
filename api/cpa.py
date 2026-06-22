import mimetypes
import re
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

import requests

from .config import Settings


class CpaVideoClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.cpa_api_key}"}

    def _video_form(self, prompt: str) -> dict[str, str]:
        return {
            "model": self.settings.cpa_video_model,
            "prompt": prompt,
            "seconds": str(self.settings.cpa_seconds),
            "size": self.settings.cpa_size,
            "resolution_name": self.settings.cpa_resolution,
            "preset": self.settings.cpa_preset,
        }

    def _raise_submit_error(self, resp: requests.Response) -> None:
        if resp.status_code >= 400:
            raise RuntimeError(f"CPA submit failed: HTTP {resp.status_code}: {resp.text[:500]}")

    def _extract_video_id(self, resp: requests.Response) -> str:
        self._raise_submit_error(resp)
        data = resp.json()
        video_id = data.get("id")
        if not video_id:
            raise RuntimeError(f"CPA did not return video id: {data}")
        return video_id

    def _video_url_from_status(self, data: dict) -> str:
        video = data.get("video")
        if isinstance(video, dict):
            url = str(video.get("url") or "").strip()
            if url.startswith("http"):
                return url
        for key in ("url", "video_url", "output_url"):
            url = str(data.get(key) or "").strip()
            if url.startswith("http"):
                return url
        return ""

    def download_image(self, image_url: str) -> Path:
        resp = requests.get(image_url, timeout=90)
        if resp.status_code >= 400:
            raise RuntimeError(f"image download failed: HTTP {resp.status_code}")
        content_type = resp.headers.get("content-type", "")
        suffix = ".png"
        if "jpeg" in content_type or "jpg" in content_type:
            suffix = ".jpg"
        elif "webp" in content_type:
            suffix = ".webp"
        tmp = NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(resp.content)
        tmp.close()
        return Path(tmp.name)

    def submit_video_with_upload(self, prompt: str, image_url: str) -> str:
        image_path = self.download_image(image_url)
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        try:
            with image_path.open("rb") as f:
                resp = requests.post(
                    f"{self.settings.cpa_base_url}/v1/videos",
                    headers=self._headers(),
                    data=self._video_form(prompt),
                    files={"input_reference[]": (image_path.name, f, mime)},
                    timeout=90,
                )
        finally:
            try:
                image_path.unlink(missing_ok=True)
            except Exception:
                pass
        return self._extract_video_id(resp)

    def submit_video_with_image_url(self, prompt: str, image_url: str) -> str:
        data = self._video_form(prompt)
        data["input_reference[image_url]"] = image_url
        resp = requests.post(
            f"{self.settings.cpa_base_url}/v1/videos",
            headers=self._headers(),
            data=data,
            timeout=90,
        )
        return self._extract_video_id(resp)

    def submit_video(self, prompt: str, image_url: str) -> str:
        try:
            return self.submit_video_with_upload(prompt, image_url)
        except RuntimeError as exc:
            message = str(exc)
            if "Text-to-video is not supported" not in message and "input reference" not in message:
                raise
        return self.submit_video_with_image_url(prompt, image_url)

    @staticmethod
    def _should_fallback_to_chat(exc: Exception) -> bool:
        message = str(exc)
        return (
            "Text-to-video is not supported" in message
            or "body must be valid JSON" in message
            or "valid JSON" in message
            or "invalid_request_error" in message
        )

    def wait_video(
        self,
        video_id: str,
        timeout_seconds: int = 900,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict:
        started = time.time()
        while time.time() - started < timeout_seconds:
            resp = requests.get(
                f"{self.settings.cpa_base_url}/v1/videos/{video_id}",
                headers=self._headers(),
                timeout=60,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"CPA poll failed: HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            status = str(data.get("status", "")).lower()
            try:
                progress = int(float(data.get("progress", 0) or 0))
            except (TypeError, ValueError):
                progress = 0
            if progress_callback:
                progress_callback(max(0, min(progress, 100)), status)
            if status in {"completed", "done", "succeeded", "success"} or progress >= 100:
                return data
            if status == "failed":
                raise RuntimeError((data.get("error") or {}).get("message") or "CPA video generation failed")
            time.sleep(8)
        raise TimeoutError(f"CPA video generation timeout: {video_id}")

    def _download_video_url(self, url: str) -> tuple[bytes, str]:
        resp = requests.get(url, timeout=180)
        if resp.status_code >= 400:
            raise RuntimeError(f"CPA video URL download failed: HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.content, resp.headers.get("content-type", "video/mp4").split(";")[0]

    def get_video_status(self, video_id: str) -> dict:
        resp = requests.get(
            f"{self.settings.cpa_base_url}/v1/videos/{video_id}",
            headers=self._headers(),
            timeout=60,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"CPA poll failed: HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def download_video(self, video_id: str, status_data: dict | None = None, timeout_seconds: int = 600) -> tuple[bytes, str]:
        direct_url = self._video_url_from_status(status_data or {})
        if direct_url:
            return self._download_video_url(direct_url)

        started = time.time()
        last_error = ""
        while time.time() - started < timeout_seconds:
            resp = requests.get(
                f"{self.settings.cpa_base_url}/v1/videos/{video_id}/content",
                headers=self._headers(),
                timeout=180,
            )
            if resp.status_code < 400:
                return resp.content, resp.headers.get("content-type", "video/mp4").split(";")[0]
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            try:
                status_data = self.get_video_status(video_id)
                direct_url = self._video_url_from_status(status_data)
                if direct_url:
                    return self._download_video_url(direct_url)
            except Exception as exc:
                last_error = f"{last_error}; status fallback failed: {exc}"
            time.sleep(8)
        raise RuntimeError(f"CPA content download failed: {last_error}")

    def generate_via_chat_completions(self, prompt: str, image_url: str) -> dict:
        resp = requests.post(
            f"{self.settings.cpa_base_url}/v1/chat/completions",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={
                "model": self.settings.cpa_video_model,
                "stream": False,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                "video_config": {
                    "seconds": self.settings.cpa_seconds,
                    "size": self.settings.cpa_size,
                    "resolution_name": self.settings.cpa_resolution,
                    "preset": self.settings.cpa_preset,
                },
            },
            timeout=300,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"CPA chat video failed: HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        content = str((data.get("choices") or [{}])[0].get("message", {}).get("content", "") or "")
        match = re.search(r"https?://\S+", content)
        if not match:
            raise RuntimeError(f"CPA chat video did not return a video URL: {content[:300]}")
        video_url = match.group(0).rstrip(").,，。]")
        video_resp = requests.get(video_url, headers=self._headers(), timeout=180)
        if video_resp.status_code >= 400:
            video_resp = requests.get(video_url, timeout=180)
        if video_resp.status_code >= 400:
            raise RuntimeError(f"CPA chat video download failed: HTTP {video_resp.status_code}")
        return {
            "video_id": data.get("id") or "chat-completions-video",
            "data": video_resp.content,
            "mime_type": video_resp.headers.get("content-type", "video/mp4").split(";")[0],
        }

    def generate(
        self,
        prompt: str,
        image_url: str,
        progress_callback: Callable[[int, str], None] | None = None,
        submit_callback: Callable[[str], None] | None = None,
    ) -> dict:
        try:
            video_id = self.submit_video(prompt, image_url)
            if submit_callback:
                submit_callback(video_id)
            if progress_callback:
                progress_callback(1, "queued")
            status_data = self.wait_video(video_id, progress_callback=progress_callback)
            data, mime_type = self.download_video(video_id, status_data=status_data)
            return {"video_id": video_id, "data": data, "mime_type": mime_type}
        except RuntimeError as exc:
            if not self._should_fallback_to_chat(exc):
                raise
        if progress_callback:
            progress_callback(1, "fallback")
        return self.generate_via_chat_completions(prompt, image_url)
