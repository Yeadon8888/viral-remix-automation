import time
from typing import Any

import requests

from .config import Settings
from .prompting import DEFAULT_IMAGE_PROMPTS, STYLE_LABELS, render_template


LABEL_TO_STYLE = {label: style for style, label in STYLE_LABELS.items()}


def normalize_styles(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    raw = value if isinstance(value, list) else []
    styles = []
    for item in raw:
        text = str(item).strip()
        style = text if text in DEFAULT_IMAGE_PROMPTS else LABEL_TO_STYLE.get(text)
        if style and style not in styles:
            styles.append(style)
    return tuple(styles) or fallback


class NewTokenImageClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.newtoken_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        resp = requests.request(
            method,
            f"{self.settings.newtoken_base_url}{path}",
            headers=self._headers(),
            timeout=kwargs.pop("timeout", 60),
            **kwargs,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {"error": {"message": resp.text[:500]}}
        if resp.status_code >= 400:
            message = (data.get("error") or {}).get("message") or str(data)[:500]
            raise RuntimeError(f"NewToken HTTP {resp.status_code}: {message}")
        return data

    @staticmethod
    def _extract_url(data: dict[str, Any]) -> str | None:
        return (
            data.get("video_url")
            or data.get("url")
            or (data.get("data") or {}).get("output")
            or (data.get("data") or {}).get("video_url")
            or (data.get("data") or {}).get("url")
        )

    def edit_image(self, image_url: str | list[str], prompt: str, aspect_ratio: str = "9:16") -> str:
        image_urls = image_url if isinstance(image_url, list) else [image_url]
        body = {
            "model": self.settings.newtoken_model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "image_urls": image_urls,
        }
        data = self._request("POST", "/v1/videos", json=body, timeout=60)
        status = str(data.get("status", "queued")).lower()
        if status in {"completed", "success", "succeeded", "done"}:
            url = self._extract_url(data)
            if url:
                return url
        if status in {"failed", "error", "cancelled"}:
            raise RuntimeError((data.get("error") or {}).get("message") or "NewToken image generation failed")

        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            raise RuntimeError("NewToken did not return task_id")

        deadline = time.time() + self.settings.newtoken_timeout_seconds
        while time.time() < deadline:
            time.sleep(5)
            data = self._request("GET", f"/v1/videos/{task_id}", timeout=60)
            status = str(data.get("status", "queued")).lower()
            if status in {"completed", "success", "succeeded", "done"}:
                url = self._extract_url(data)
                if not url:
                    raise RuntimeError("NewToken completed without image URL")
                return url
            if status in {"failed", "error", "cancelled"}:
                raise RuntimeError((data.get("error") or {}).get("message") or "NewToken image generation failed")
        raise TimeoutError(f"NewToken image generation timeout: {task_id}")

    def generate_group_images(
        self,
        source_image_url: str,
        custom_prompt: str,
        styles: tuple[str, ...],
        *,
        prompt_templates: dict[str, str] | None = None,
        override_template: str = "",
        region: str = "墨西哥",
        language: str = "",
        product_name: str = "",
        reference_image_url: str = "",
    ) -> list[dict[str, str]]:
        results = []
        for style in styles:
            key = f"image_{style}"
            base_prompt = (
                override_template.strip()
                or (prompt_templates or {}).get(key, "").strip()
                or DEFAULT_IMAGE_PROMPTS.get(style, DEFAULT_IMAGE_PROMPTS["lifestyle"])
            )
            prompt = render_template(
                base_prompt,
                {
                    "region": region,
                    "lang": language,
                    "style": style,
                    "style_label": STYLE_LABELS.get(style, style),
                    "product_name": product_name or "无",
                    "custom_prompt": custom_prompt or "无",
                },
            )
            if custom_prompt:
                prompt += f"\n\n用户补充要求：{custom_prompt}"
            image_urls = [source_image_url]
            if style == "viral_first_frame" and reference_image_url:
                image_urls.append(reference_image_url)
            try:
                url = self.edit_image(image_urls, prompt)
                results.append({"style": style, "url": url, "prompt": prompt})
            except Exception as exc:
                results.append({"style": style, "url": "", "prompt": prompt, "error": str(exc)})
        return results
