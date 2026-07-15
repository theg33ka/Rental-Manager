from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import os
from typing import Protocol

from rental_manager.services.deepseek_client import DeepSeekClient, DeepSeekResult


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")


class AiProvider(StrEnum):
    DEEPSEEK = "deepseek"


class AiProviderConfigError(ValueError):
    pass


class ChatCompletionClient(Protocol):
    def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 1200,
        session_id: str = "",
    ) -> DeepSeekResult: ...


@dataclass(frozen=True)
class AiProviderRuntime:
    provider: AiProvider
    model: str
    client: ChatCompletionClient


class AiProviderAdapter(Protocol):
    def build(
        self,
        *,
        requested_model: str,
        deepseek_api_key: str,
        environ: Mapping[str, str],
    ) -> AiProviderRuntime: ...


def _env(source: Mapping[str, str], name: str, default: str = "") -> str:
    return (source.get(name) or default).strip()


def _env_int(
    source: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = _env(source, name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AiProviderConfigError(f"{name} должен быть целым числом") from exc
    return min(maximum, max(minimum, value))


def normalize_deepseek_model(value: str) -> str:
    model = (value or "").strip().lower()
    if model not in DEEPSEEK_MODELS:
        allowed = ", ".join(DEEPSEEK_MODELS)
        raise AiProviderConfigError(f"Модель DeepSeek должна быть одной из: {allowed}")
    return model


def primary_provider(environ: Mapping[str, str] | None = None) -> AiProvider:
    return AiProvider.DEEPSEEK


def provider_chain(environ: Mapping[str, str] | None = None) -> list[AiProvider]:
    return [AiProvider.DEEPSEEK]


class DeepSeekProviderAdapter:
    def build(
        self,
        *,
        requested_model: str,
        deepseek_api_key: str,
        environ: Mapping[str, str],
    ) -> AiProviderRuntime:
        model = normalize_deepseek_model(_env(environ, "DEEPSEEK_MODEL") or requested_model)
        api_key = _env(environ, "DEEPSEEK_API_KEY") or (deepseek_api_key or "").strip()
        if not api_key:
            raise AiProviderConfigError("API-ключ DeepSeek не задан")
        client = DeepSeekClient(
            _env(environ, "DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL),
            api_key,
            timeout_seconds=_env_int(
                environ,
                "DEEPSEEK_REQUEST_TIMEOUT_SECONDS",
                60,
                minimum=10,
                maximum=300,
            ),
            provider_name=AiProvider.DEEPSEEK.value,
        )
        return AiProviderRuntime(provider=AiProvider.DEEPSEEK, model=model, client=client)


PROVIDER_ADAPTERS: dict[AiProvider, AiProviderAdapter] = {
    AiProvider.DEEPSEEK: DeepSeekProviderAdapter(),
}


def register_provider_adapter(provider: AiProvider, adapter: AiProviderAdapter) -> None:
    PROVIDER_ADAPTERS[provider] = adapter


def build_provider_runtime(
    provider: AiProvider,
    *,
    requested_model: str,
    deepseek_api_key: str = "",
    environ: Mapping[str, str] | None = None,
) -> AiProviderRuntime:
    adapter = PROVIDER_ADAPTERS.get(provider)
    if not adapter:
        raise AiProviderConfigError(f"LLM provider {provider} не зарегистрирован")
    return adapter.build(
        requested_model=requested_model,
        deepseek_api_key=deepseek_api_key,
        environ=environ if environ is not None else os.environ,
    )


def build_provider_chain(
    *,
    requested_model: str,
    deepseek_api_key: str = "",
    environ: Mapping[str, str] | None = None,
) -> list[AiProviderRuntime]:
    return [
        build_provider_runtime(
            AiProvider.DEEPSEEK,
            requested_model=requested_model,
            deepseek_api_key=deepseek_api_key,
            environ=environ,
        )
    ]
