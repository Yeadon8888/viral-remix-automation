from datetime import datetime
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from typing import Any

from api.config import Settings
from api.feishu import FeishuClient, field_value
from api.logging_bus import add_log
from api.r2 import R2Storage
from api.tikhub import TikHubClient


def now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def url_cell(url: str) -> dict[str, str]:
    return {"text": url, "link": url}


class ViralVideoAssetService:
    def __init__(self, settings: Settings, feishu: FeishuClient, r2: R2Storage, tikhub: TikHubClient):
        self.settings = settings
        self.feishu = feishu
        self.r2 = r2
        self.tikhub = tikhub

    def extract_first_frame(self, video_bytes: bytes, job_id: str | None = None) -> str:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            video_path = tmp / "viral-video.mp4"
            frame_path = tmp / "viral-first-frame.jpg"
            video_path.write_bytes(video_bytes)
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(video_path),
                        "-frames:v",
                        "1",
                        str(frame_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("ffmpeg 未安装，无法截取爆款视频首帧") from exc
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()[:300]
                raise RuntimeError(f"ffmpeg 截取爆款首帧失败: {detail}") from exc
            if not frame_path.exists() or frame_path.stat().st_size == 0:
                raise RuntimeError("ffmpeg 未生成爆款首帧")
            stored = self.r2.upload_bytes(frame_path.read_bytes(), "viral-first-frame.jpg", "image/jpeg")
            add_log("info", f"爆款视频首帧已入库: {stored['url']}", "workflow", job_id)
            return stored["url"]

    def download_cached(self, record_id: str, fields: dict[str, Any], job_id: str | None = None) -> tuple[bytes, str]:
        viral_url = str(field_value(fields, "爆款视频链接", "") or "").strip()
        cached_url = str(field_value(fields, "爆款视频直链", "") or "").strip()
        if cached_url.startswith("http"):
            try:
                add_log("info", "使用缓存爆款视频直链下载视频", "workflow", job_id)
                return self.tikhub.download_video_url(cached_url)
            except Exception as exc:
                add_log("error", f"缓存爆款视频直链已失效，将重新解析: {exc}", "workflow", job_id)

        if not viral_url:
            raise RuntimeError("爆款视频链接为空")
        video_url = self.tikhub.resolve_video_url(viral_url)
        video_bytes, video_mime = self.tikhub.download_video_url(video_url)
        timestamp = now_ms()
        self.feishu.update_record(
            self.settings.source_table_id,
            record_id,
            {
                "爆款视频直链": url_cell(video_url),
                "爆款视频直链解析时间": timestamp,
                "自动化处理时间": timestamp,
            },
        )
        add_log("info", "爆款视频直链已缓存到素材表", "workflow", job_id)
        return video_bytes, video_mime

    def ensure_first_frame(self, record_id: str, fields: dict[str, Any], job_id: str | None = None) -> str:
        first_frame_url = str(field_value(fields, "爆款首帧链接", "") or "").strip()
        if first_frame_url.startswith("http"):
            return first_frame_url
        video_bytes, _ = self.download_cached(record_id, fields, job_id)
        first_frame_url = self.extract_first_frame(video_bytes, job_id)
        self.feishu.update_record(
            self.settings.source_table_id,
            record_id,
            {"爆款首帧链接": url_cell(first_frame_url), "自动化处理时间": now_ms()},
        )
        return first_frame_url
