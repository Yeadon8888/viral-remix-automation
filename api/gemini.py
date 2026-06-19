import base64
import json
import re
import time
from threading import Lock
from typing import Any

import requests

from .config import Settings
from .prompting import DEFAULT_VIDEO_ANALYSIS_TEMPLATE, render_template

GEMINI_MAX_RETRIES = 4
GEMINI_MIN_INTERVAL_SECONDS = 2.0
_gemini_lock = Lock()
_last_gemini_request_at = 0.0


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


def _normalize_doubao_result(text: str, product_name: str) -> dict[str, str]:
    data = _parse_json_object(text)
    if not data:
        return {
            "final_video_prompt": text.strip(),
            "title": product_name,
            "caption": "",
            "tags": "",
        }
    tags = data.get("tags") or data.get("keywords") or ""
    if isinstance(tags, list):
        tags = " ".join(str(item).strip() for item in tags if str(item).strip())
    return {
        "final_video_prompt": str(data.get("final_video_prompt") or data.get("video_prompt") or data.get("prompt") or "").strip(),
        "title": str(data.get("title") or data.get("publish_title") or product_name).strip(),
        "caption": str(data.get("caption") or data.get("publish_caption") or "").strip(),
        "tags": str(tags).strip(),
    }


class GeminiClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        global _last_gemini_request_at

        last_error = ""
        for attempt in range(GEMINI_MAX_RETRIES + 1):
            with _gemini_lock:
                wait = GEMINI_MIN_INTERVAL_SECONDS - (time.time() - _last_gemini_request_at)
                if wait > 0:
                    time.sleep(wait)
                resp = requests.post(
                    f"{self.settings.gemini_base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.gemini_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=300,
                )
                _last_gemini_request_at = time.time()

            if resp.status_code < 400:
                return resp.json()

            last_error = resp.text[:500]
            if resp.status_code != 429 or attempt >= GEMINI_MAX_RETRIES:
                raise RuntimeError(f"Gemini HTTP {resp.status_code}: {last_error}")

            retry_after = resp.headers.get("Retry-After", "")
            try:
                delay = float(retry_after)
            except ValueError:
                delay = min(60.0, 5.0 * (attempt + 1))
            time.sleep(delay)

        raise RuntimeError(f"Gemini request failed after retries: {last_error}")

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

        data = self._post_chat(
            {
                "model": self.settings.gemini_model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 8192,
            }
        )
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        if not text:
            raise RuntimeError("Gemini returned empty content")
        analysis = _normalize_analysis(text, product_name)
        if not analysis["video_prompt"]:
            raise RuntimeError("Gemini returned empty video_prompt")
        analysis["analysis_prompt"] = prompt
        return analysis

    def generate_doubao_video_prompt(
        self,
        *,
        product_name: str,
        product_link: str,
        prompt_rules: str,
        image_bytes: bytes,
        image_mime: str,
    ) -> dict[str, str]:
        prompt = f"""
请先理解商品图片中的真实商品，再严格参考以下【豆包/Seedance 视频导演规则】，生成可直接回填到表格的内容。

【豆包/Seedance 视频导演规则】
{prompt_rules}

【商品信息】
- 商品名称：{product_name or "未填写"}
- 商品链接：{product_link or "未填写"}
- 目标市场：墨西哥
- 口播语言：墨西哥西班牙语

请只输出 JSON，不要 Markdown，不要代码块：
{{
  "final_video_prompt": "[Video Prompt]\\n中文视频生成提示词，口播台词用墨西哥西班牙语",
  "title": "可直接发布的墨西哥西班牙语标题",
  "caption": "可直接发布的墨西哥西班牙语正文",
  "tags": "#tag1 #tag2 #tag3"
}}

要求：
1. final_video_prompt 必须用于 10 秒 4 镜头 TikTok 墨西哥带货视频，中文为主，口播/字幕文本用墨西哥西班牙语。
2. 必须严格锁定图片中的商品外观、颜色、材质、结构和比例，不把海报文字、箭头、边框、光效当作商品本体。
3. title、caption、tags 必须基于商品图片分析生成，适合 TikTok 墨西哥站直接发布。
""".strip()
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": _data_url(image_mime, image_bytes)}},
        ]
        data = self._post_chat(
            {
                "model": self.settings.gemini_model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 8192,
            }
        )
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        if not text:
            raise RuntimeError("Gemini returned empty content")
        result = _normalize_doubao_result(text, product_name)
        if not result["final_video_prompt"]:
            raise RuntimeError("Gemini returned empty final_video_prompt")
        result["analysis_prompt"] = prompt
        return result
