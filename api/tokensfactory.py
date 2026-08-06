import time
from typing import Any, Callable

import requests


SUCCESS_STATES = {"completed", "complete", "succeeded", "success", "done"}
FAILED_STATES = {"failed", "failure", "error"}
CANCELLED_STATES = {"cancelled", "canceled"}


class TokensFactoryImageClient:
    def __init__(
        self,
        settings,
        *,
        session=None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.session = session or requests.Session()
        self.sleep = sleep_fn
        self.monotonic = monotonic_fn

    def generate_white_background(self, image_url: str, prompt: str) -> dict[str, str]:
        task_id = self.create_task(image_url, prompt)
        image_url = self.wait_for_result(task_id)
        return {"task_id": task_id, "image_url": image_url}

    def create_task(self, image_url: str, prompt: str) -> str:
        try:
            response = self.session.post(
                f"{self._base_url()}/v1/videos",
                headers=self._headers(),
                json={
                    "model": str(getattr(self.settings, "tokensfactory_image_model", "nano-banana-2") or "nano-banana-2"),
                    "prompt": str(prompt or "").strip(),
                    "images": [str(image_url or "").strip()],
                    "aspect_ratio": str(getattr(self.settings, "tokensfactory_image_aspect_ratio", "1:1") or "1:1"),
                    "resolution": str(getattr(self.settings, "tokensfactory_image_resolution", "2k") or "2k"),
                },
                timeout=min(60, self._timeout_seconds()),
            )
        except requests.RequestException as exc:
            # POST is not retried: an unknown response must not create a duplicate paid task.
            raise RuntimeError("TokensFactory 创建白底图任务请求失败，已停止且不会自动重试") from exc
        payload = self._json_response(response, "创建白底图任务")
        task_id = str(payload.get("task_id") or payload.get("id") or "").strip()
        if not task_id:
            raise RuntimeError("TokensFactory 创建成功但未返回任务 ID")
        return task_id

    def wait_for_result(self, task_id: str) -> str:
        deadline = self.monotonic() + self._timeout_seconds()
        interval = max(0.0, float(getattr(self.settings, "tokensfactory_poll_interval_seconds", 2) or 0))
        while self.monotonic() <= deadline:
            payload = self._get_task(task_id)
            state = str(payload.get("status") or payload.get("state") or "").strip().lower()
            result_url = self._result_url(payload)
            if result_url and (not state or state in SUCCESS_STATES):
                return result_url
            if state in FAILED_STATES:
                raise RuntimeError("TokensFactory 白底图生成失败")
            if state in CANCELLED_STATES:
                raise RuntimeError("TokensFactory 白底图任务已取消")
            self.sleep(interval)
        raise RuntimeError("TokensFactory 白底图生成超时")

    def _get_task(self, task_id: str) -> dict[str, Any]:
        retries = max(0, int(getattr(self.settings, "tokensfactory_request_retries", 2) or 0))
        for attempt in range(retries + 1):
            try:
                response = self.session.get(
                    f"{self._base_url()}/v1/videos/{task_id}",
                    headers=self._headers(),
                    timeout=min(60, self._timeout_seconds()),
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < retries:
                        self.sleep(min(2 ** attempt, 5))
                        continue
                return self._json_response(response, "查询白底图任务")
            except requests.RequestException as exc:
                if attempt >= retries:
                    raise RuntimeError("TokensFactory 查询请求失败") from exc
                self.sleep(min(2 ** attempt, 5))
        raise RuntimeError("TokensFactory 查询请求失败")

    def _headers(self) -> dict[str, str]:
        key = str(getattr(self.settings, "tokensfactory_api_key", "") or "").strip()
        if not key:
            raise RuntimeError("TOKENSFACTORY_API_KEY 未配置")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _base_url(self) -> str:
        return str(getattr(self.settings, "tokensfactory_base_url", "https://tokensfactory.cc") or "https://tokensfactory.cc").rstrip("/")

    def _timeout_seconds(self) -> int:
        return max(1, int(getattr(self.settings, "tokensfactory_image_timeout_seconds", 900) or 900))

    @staticmethod
    def _json_response(response, operation: str) -> dict[str, Any]:
        if response.status_code >= 400:
            raise RuntimeError(f"TokensFactory {operation}失败：HTTP {response.status_code}")
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"TokensFactory {operation}返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"TokensFactory {operation}返回格式无效")
        return payload

    @staticmethod
    def _result_url(payload: dict[str, Any]) -> str:
        direct = str(payload.get("image_url") or payload.get("url") or "").strip()
        if direct:
            return direct
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            urls = metadata.get("result_urls")
            if isinstance(urls, list) and urls:
                return str(urls[0] or "").strip()
        return ""
