from __future__ import annotations

from dataclasses import dataclass
import json
import urllib.error
import urllib.request
from typing import Any


class HermesClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class HermesResult:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] | None = None
    provider: str = "hermes"


class HermesClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout_seconds: int = 20,
        provider_name: str = "hermes",
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = timeout_seconds
        self.provider_name = (provider_name or "hermes").strip()

    def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 700,
        session_id: str = "",
    ) -> HermesResult:
        if not self.base_url:
            raise HermesClientError("Hermes API URL is empty")
        if not model:
            raise HermesClientError("Hermes model is empty")
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        extra_headers = {}
        if self.provider_name == "hermes" and session_id.strip():
            extra_headers["X-Hermes-Session-Id"] = session_id.strip()[:240]
        response = self._post_json("/v1/chat/completions", payload, extra_headers=extra_headers)
        choices = response.get("choices") or []
        if not choices:
            raise HermesClientError("Hermes returned no choices")
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        usage = response.get("usage") or {}
        return HermesResult(
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
            headers["X-API-Key"] = self.api_key
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
            raise HermesClientError(f"Hermes API failed: HTTP {exc.code}: {body[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HermesClientError(f"Hermes API request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise HermesClientError("Hermes returned invalid JSON") from exc


class YandexOpenAIClient(HermesClient):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        folder_id: str = "",
        timeout_seconds: int = 20,
        provider_name: str = "yandex",
    ) -> None:
        super().__init__(base_url, api_key, timeout_seconds, provider_name)
        self.folder_id = (folder_id or "").strip()

    def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise HermesClientError("Yandex API key is empty")

        normalized_path = path
        if self.base_url.endswith("/v1") and normalized_path.startswith("/v1/"):
            normalized_path = normalized_path[3:]

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Api-Key {self.api_key}",
        }
        if self.folder_id:
            headers["OpenAI-Project"] = self.folder_id

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
            raise HermesClientError(f"Yandex AI API failed: HTTP {exc.code}: {body[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HermesClientError(f"Yandex AI API request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise HermesClientError("Yandex AI returned invalid JSON") from exc
