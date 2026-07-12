from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import os

from rental_manager.services.deepseek_client import DeepSeekClient


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")


class AiProvider(StrEnum):
    DEEPSEEK = "deepseek"


class AiProviderConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AiProviderRuntime:
    provider: AiProvider
    model: str
    client: DeepSeekClient


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


def build_provider_runtime(
    provider: AiProvider,
    *,
    requested_model: str,
    deepseek_api_key: str = "",
    environ: Mapping[str, str] | None = None,
) -> AiProviderRuntime:
    if provider != AiProvider.DEEPSEEK:
        raise AiProviderConfigError("Поддерживается только прямой DeepSeek API")

    source = environ if environ is not None else os.environ
    model = normalize_deepseek_model(_env(source, "DEEPSEEK_MODEL") or requested_model)
    api_key = _env(source, "DEEPSEEK_API_KEY") or (deepseek_api_key or "").strip()
    if not api_key:
        raise AiProviderConfigError("API-ключ DeepSeek не задан")

    client = DeepSeekClient(
        _env(source, "DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL),
        api_key,
        timeout_seconds=_env_int(
            source,
            "DEEPSEEK_REQUEST_TIMEOUT_SECONDS",
            60,
            minimum=10,
            maximum=300,
        ),
        provider_name=provider.value,
    )
    return AiProviderRuntime(provider=provider, model=model, client=client)


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
