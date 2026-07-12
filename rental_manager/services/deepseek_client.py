from __future__ import annotations

from dataclasses import dataclass
import json
import urllib.error
import urllib.request
from typing import Any


class DeepSeekClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeepSeekResult:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] | None = None
    provider: str = "deepseek"


class DeepSeekClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout_seconds: int = 20,
        provider_name: str = "deepseek",
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = timeout_seconds
        self.provider_name = (provider_name or "deepseek").strip()

    def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 700,
        session_id: str = "",
    ) -> DeepSeekResult:
        if not self.base_url:
            raise DeepSeekClientError("DeepSeek API URL is empty")
        if not model:
            raise DeepSeekClientError("DeepSeek model is empty")
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = self._post_json("/chat/completions", payload)
        choices = response.get("choices") or []
        if not choices:
            raise DeepSeekClientError("DeepSeek API returned no choices")
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        usage = response.get("usage") or {}
        return DeepSeekResult(
            content=content,
            model=str(response.get("model") or model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            raw=response,
            provider=self.provider_name,
        )

    def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized_path = path
        if self.base_url.endswith("/v1") and normalized_path.startswith("/v1/"):
            normalized_path = normalized_path[3:]
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(
            f"{self.base_url}{normalized_path}",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise DeepSeekClientError(f"DeepSeek API failed: HTTP {exc.code}: {body[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DeepSeekClientError(f"DeepSeek API request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise DeepSeekClientError("DeepSeek API returned invalid JSON") from exc
