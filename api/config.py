import os
from dataclasses import dataclass


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    admin_token: str = env("ADMIN_TOKEN")

    feishu_app_id: str = env("FEISHU_APP_ID")
    feishu_app_secret: str = env("FEISHU_APP_SECRET")
    feishu_app_token: str = env("FEISHU_APP_TOKEN", "H3RybjMFqabStts0zFUcWrz9nvb")
    source_table_id: str = env("FEISHU_SOURCE_TABLE_ID", "tblDEJZwbA7iEuL1")
    task_table_id: str = env("FEISHU_TASK_TABLE_ID", "tblJSkNvzYaku9Kg")
    prompt_table_id: str = env("FEISHU_PROMPT_TABLE_ID", "tblvFcsSoHnTP5Te")

    tikhub_api_key: str = env("TIKHUB_API_KEY")

    gemini_base_url: str = env("GEMINI_BASE_URL", "https://yunwu.ai").rstrip("/")
    gemini_api_key: str = env("GEMINI_API_KEY")
    gemini_model: str = env("GEMINI_MODEL", "gemini-3.1-flash-lite")

    newtoken_base_url: str = env("NEWTOKEN_IMAGE_BASE_URL", "https://api.newtoken.club").rstrip("/")
    newtoken_api_key: str = env("NEWTOKEN_IMAGE_API_KEY")
    newtoken_model: str = env("NEWTOKEN_IMAGE_MODEL", "gpt-image-2-1K")
    image_scene_styles: tuple[str, ...] = tuple(
        s.strip()
        for s in env("IMAGE_SCENE_STYLES", "lifestyle,model,detail,flatlay,outdoor,studio").split(",")
        if s.strip()
    )

    cpa_base_url: str = env("CPA_BASE_URL", "https://cliproxyapi-production-2dde.up.railway.app").rstrip("/")
    cpa_api_key: str = env("CPA_API_KEY")
    cpa_video_model: str = env("CPA_VIDEO_MODEL", "grok-imagine-video-1.5-preview")
    cpa_seconds: int = env_int("CPA_SECONDS", 16)
    cpa_size: str = env("CPA_SIZE", "720x1280")
    cpa_resolution: str = env("CPA_RESOLUTION", "720p")
    cpa_preset: str = env("CPA_PRESET", "normal")

    r2_endpoint: str = env("R2_ENDPOINT")
    r2_access_key: str = env("R2_ACCESS_KEY")
    r2_secret_key: str = env("R2_SECRET_KEY")
    r2_bucket: str = env("R2_BUCKET")
    r2_public_url: str = env("R2_PUBLIC_URL").rstrip("/")
    r2_prefix: str = env("R2_PREFIX", "viral-remix-7d").strip("/")
    upload_api_url: str = env("UPLOAD_API_URL").rstrip("/")
    upload_api_key: str = env("UPLOAD_API_KEY")
    upload_prefix: str = env("UPLOAD_PREFIX", "vidclaw-assets").strip("/")

    @property
    def has_upload_gateway(self) -> bool:
        return bool(self.upload_api_url and self.upload_api_key)

    @property
    def has_direct_r2(self) -> bool:
        return bool(
            self.r2_endpoint
            and self.r2_access_key
            and self.r2_secret_key
            and self.r2_bucket
            and self.r2_public_url
        )

    def missing_required(self) -> list[str]:
        required = {
            "FEISHU_APP_ID": self.feishu_app_id,
            "FEISHU_APP_SECRET": self.feishu_app_secret,
            "FEISHU_APP_TOKEN": self.feishu_app_token,
            "FEISHU_SOURCE_TABLE_ID": self.source_table_id,
            "FEISHU_TASK_TABLE_ID": self.task_table_id,
            "TIKHUB_API_KEY": self.tikhub_api_key,
            "GEMINI_API_KEY": self.gemini_api_key,
            "NEWTOKEN_IMAGE_API_KEY": self.newtoken_api_key,
            "CPA_API_KEY": self.cpa_api_key,
        }
        missing = [name for name, value in required.items() if not value]
        if not self.has_upload_gateway and not self.has_direct_r2:
            missing.extend(["UPLOAD_API_URL", "UPLOAD_API_KEY"])
        return missing


settings = Settings()
