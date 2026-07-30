import base64
import json
import random
import re
import time
from threading import BoundedSemaphore, Lock
from typing import Any

import requests

from .config import Settings
from .prompting import DEFAULT_VIDEO_ANALYSIS_TEMPLATE, render_template

GEMINI_MAX_RETRIES = 4
GEMINI_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


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


def _normalize_face_result(text: str) -> dict[str, Any]:
    data = _parse_json_object(text)
    if not data:
        raise RuntimeError("人脸检测没有返回有效 JSON")

    raw_status = str(data.get("status") or data.get("result") or "").strip().lower()
    status_aliases = {
        "face": "face",
        "present": "face",
        "detected": "face",
        "true": "face",
        "clear": "clear",
        "absent": "clear",
        "none": "clear",
        "no_face": "clear",
        "false": "clear",
        "uncertain": "uncertain",
        "review": "uncertain",
        "unknown": "uncertain",
    }
    status = status_aliases.get(raw_status)
    if not status and isinstance(data.get("contains_face"), bool):
        status = "face" if data["contains_face"] else "clear"
    if not status:
        raise RuntimeError("人脸检测返回了不支持的状态")

    has_subject_contract = any(
        key in data
        for key in ("subject_type", "face_kind", "human_face_count", "visibility")
    )
    subject_type = str(
        data.get("subject_type")
        or data.get("face_kind")
        or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")
    human_subjects = {"human", "real_human", "photorealistic_human"}
    non_human_subjects = {
        "cartoon",
        "anime",
        "animal",
        "doll",
        "emoji",
        "mascot",
        "game_character",
        "non_human",
        "none",
        "object",
    }
    visibility = str(data.get("visibility") or "").strip().lower()

    raw_count = (
        data.get("human_face_count")
        if "human_face_count" in data
        else data.get("face_count")
    )
    try:
        face_count = max(0, int(raw_count or 0))
    except (TypeError, ValueError):
        face_count = 0
    try:
        confidence = min(1.0, max(0.0, float(data.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0.0

    if has_subject_contract:
        clearly_human = subject_type in human_subjects
        clearly_visible = visibility in {"", "clear", "obvious", "recognizable"}
        if (
            status != "face"
            or not clearly_human
            or subject_type in non_human_subjects
            or not clearly_visible
            or face_count < 1
            or confidence < 0.85
        ):
            status = "clear"
            face_count = 0

    return {
        "status": status,
        "face_count": face_count,
        "confidence": confidence,
        "reason": str(data.get("reason") or data.get("detail") or "").strip(),
    }


class GeminiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._slots = BoundedSemaphore(max(
            1,
            int(getattr(settings, "gemini_request_concurrency", 6)),
        ))
        self._rate_lock = Lock()
        self._last_request_at = 0.0

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error = ""
        for attempt in range(GEMINI_MAX_RETRIES + 1):
            try:
                with self._slots:
                    with self._rate_lock:
                        min_interval = max(
                            0.0,
                            float(getattr(self.settings, "gemini_min_interval_seconds", 0.25)),
                        )
                        wait = min_interval - (time.time() - self._last_request_at)
                        if wait > 0:
                            time.sleep(wait)
                        self._last_request_at = time.time()
                    resp = requests.post(
                        f"{self.settings.gemini_base_url}/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.settings.gemini_api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=300,
                    )
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt >= GEMINI_MAX_RETRIES:
                    raise RuntimeError(
                        f"Gemini request failed after retries: {last_error}"
                    ) from exc
                delay = min(30.0, 1.5 * (2**attempt)) + random.uniform(0.0, 0.25)
                time.sleep(delay)
                continue

            if resp.status_code < 400:
                return resp.json()

            last_error = resp.text[:500]
            if (
                resp.status_code not in GEMINI_RETRYABLE_STATUS_CODES
                or attempt >= GEMINI_MAX_RETRIES
            ):
                raise RuntimeError(f"Gemini HTTP {resp.status_code}: {last_error}")

            retry_after = resp.headers.get("Retry-After", "")
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                delay = min(30.0, 1.5 * (2**attempt)) + random.uniform(0.0, 0.25)
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

    def detect_human_faces(self, *, image_bytes: bytes, image_mime: str) -> dict[str, Any]:
        prompt = """
你是商品素材合规检测器。只检测明确、可辨认的人类脸部。

判定规则：
- 只有明确、可辨认的人类脸部才判为 face，包括真人或逼真的写实生成人脸；印刷品、屏幕和倒影中的人脸也必须足够清晰。
- 正脸、清晰侧脸或清晰局部脸可以判为 face，但必须具有连贯的人类面部结构，并能稳定辨认至少两个关键部位（眼睛、鼻子、嘴巴）。
- 卡通、动漫、游戏角色、吉祥物、玩偶、动物脸、Emoji、商品装饰图案一律判为 clear。
- 单独的眼睛、嘴唇、皮肤局部、手、人体轮廓，以及看起来“像脸”的物体图案一律判为 clear。
- 模糊、过小、严重遮挡、五官不可辨认或不确定是否为人类脸部时，一律判为 clear；不要因为疑似或联想而拦截。
- status=face 时，subject_type 必须为 human、visibility 必须为 clear、human_face_count 必须大于 0、confidence 必须至少为 0.85。

只输出一个 JSON 对象，不要 Markdown：
{
  "status": "face | clear",
  "subject_type": "human | non_human | none",
  "visibility": "clear | not_clear",
  "human_face_count": 0,
  "confidence": 0.0,
  "reason": "简短中文理由"
}
""".strip()
        data = self._post_chat(
            {
                "model": self.settings.gemini_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": _data_url(image_mime, image_bytes)}},
                        ],
                    }
                ],
                "temperature": 0,
                "max_tokens": 512,
            }
        )
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        if not text:
            raise RuntimeError("Gemini 人脸检测返回空内容")
        return _normalize_face_result(text)
