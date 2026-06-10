import base64
import json
import re
from typing import Any

import requests

from .config import Settings
from .prompting import DEFAULT_VIDEO_ANALYSIS_TEMPLATE, render_template


REGION_LANGUAGE = {
    "墨西哥": "Mexican Spanish",
    "马来": "Bahasa Malaysia",
    "马来西亚": "Bahasa Malaysia",
}


def local_language(region: str) -> str:
    return REGION_LANGUAGE.get(region, "the target market language")


def _data_url(mime_type: str, data: bytes) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('utf-8')}"


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _normalize_analysis(text: str, product_name: str) -> dict[str, str]:
    data = _parse_json_object(text)
    if not data:
        return {
            "video_prompt": text.strip(),
            "publish_title": product_name,
            "publish_caption": "",
            "keywords": "",
        }
    keywords = data.get("keywords") or data.get("tags") or ""
    if isinstance(keywords, list):
        keywords = " ".join(str(item).strip() for item in keywords if str(item).strip())
    return {
        "video_prompt": str(data.get("video_prompt") or data.get("prompt") or "").strip(),
        "publish_title": str(data.get("publish_title") or data.get("title") or product_name).strip(),
        "publish_caption": str(data.get("publish_caption") or data.get("caption") or "").strip(),
        "keywords": str(keywords).strip(),
    }


class GeminiClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def analyze_video(
        self,
        *,
        product_name: str,
        region: str,
        custom_prompt: str,
        prompt_template: str | None = None,
        video_bytes: bytes,
        video_mime: str,
        image_bytes: bytes | None,
        image_mime: str | None,
    ) -> dict[str, str]:
        lang = local_language(region)
        prompt = render_template(
            prompt_template or DEFAULT_VIDEO_ANALYSIS_TEMPLATE,
            {
                "product_name": product_name,
                "region": region,
                "lang": lang,
                "custom_prompt": custom_prompt or "无",
            },
        )

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image_bytes and image_mime:
            content.append({"type": "image_url", "image_url": {"url": _data_url(image_mime, image_bytes)}})
        content.append({"type": "image_url", "image_url": {"url": _data_url(video_mime, video_bytes)}})

        resp = requests.post(
            f"{self.settings.gemini_base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.settings.gemini_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.gemini_model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 8192,
            },
            timeout=300,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        if not text:
            raise RuntimeError("Gemini returned empty content")
        analysis = _normalize_analysis(text, product_name)
        if not analysis["video_prompt"]:
            raise RuntimeError("Gemini returned empty video_prompt")
        analysis["analysis_prompt"] = prompt
        return analysis
