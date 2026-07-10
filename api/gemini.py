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
        business_prompt = render_template(
            prompt_rules,
            {
                "product_name": product_name or "未填写",
                "product_link": product_link or "未填写",
            },
        )
        technical_contract = f"""
【本次商品输入】
- 商品名称：{product_name or "未填写"}
- 商品链接：{product_link or "未填写"}
- 商品图片：使用随本次请求附带的图片

【系统回填格式】
为便于系统回填，请只输出 JSON，不要 Markdown，不要代码块：
{{
  "final_video_prompt": "严格按照手动模板生成的完整视频提示词",
  "title": "发布标题；手动模板未要求时可为空字符串",
  "caption": "发布正文；手动模板未要求时可为空字符串",
  "tags": "发布标签；手动模板未要求时可为空字符串"
}}
""".strip()
        prompt = "\n\n".join((business_prompt, technical_contract)).strip()
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
