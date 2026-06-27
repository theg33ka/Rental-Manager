from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from collections.abc import Mapping

from rental_manager.services.hermes_client import HermesClient, YandexOpenAIClient


YANDEX_BASE_URL = "https://ai.api.cloud.yandex.net/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
AMVERA_LLM_BASE_URL = "https://inference.waw0.amvera.ru"
AMVERA_LLM_MODEL = "deepseek-V4"
YANDEX_MODEL_ALIASES = {
    "yandexgpt-lite": "yandexgpt-lite/latest",
    "yandexgpt": "yandexgpt/latest",
    "yandexgpt-pro": "yandexgpt/latest",
}
AMVERA_MODEL_ALIASES = {
    "deepseek_v4": "deepseek-V4",
    "deepseek-v4": "deepseek-V4",
    "deepseek v4": "deepseek-V4",
    "deepseek_v3": "deepseek-V3",
    "deepseek-v3": "deepseek-V3",
    "deepseek_r1": "deepseek-R1",
    "deepseek-r1": "deepseek-R1",
}


class AiProvider(StrEnum):
    HERMES = "hermes"
    YANDEX = "yandex"
    DEEPSEEK = "deepseek"
    OPENAI_COMPATIBLE = "openai_compatible"
    AMVERA_LLM = "amvera_llm"


class AiProviderConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AiProviderRuntime:
    provider: AiProvider
    model: str
    client: HermesClient


def _env(source: Mapping[str, str], name: str, default: str = "") -> str:
    return (source.get(name) or default).strip()


def _env_bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _env(source, name)
    if not raw:
        return default
    return raw.lower() not in {"0", "false", "no", "off"}


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
        raise AiProviderConfigError(f"{name} must be an integer") from exc
    return min(maximum, max(minimum, value))


def parse_provider(value: str, *, setting_name: str) -> AiProvider:
    normalized = (value or "").strip().lower().replace("-", "_")
    try:
        return AiProvider(normalized)
    except ValueError as exc:
        allowed = ", ".join(provider.value for provider in AiProvider)
        raise AiProviderConfigError(f"{setting_name} must be one of: {allowed}") from exc


def primary_provider(environ: Mapping[str, str] | None = None) -> AiProvider:
    source = environ if environ is not None else os.environ
    configured = _env(source, "AI_PROVIDER")
    if configured:
        return parse_provider(configured, setting_name="AI_PROVIDER")

    # New deployments normally run the application through Hermes while
    # Hermes itself talks to an upstream inference endpoint. If such upstream
    # config exists, prefer Hermes even when legacy Yandex keys are still
    # present for fallback. Иначе опять получим Яндекс, который «случайно»
    # стал основным.
    if (
        _env(source, "HERMES_INFERENCE_PROVIDER")
        or _env(source, "AMVERA_LLM_API_KEY")
        or _env(source, "DEEPSEEK_API_KEY")
        or _env(source, "OPENAI_COMPATIBLE_API_KEY")
        or _env(source, "OPENAI_API_KEY")
    ):
        return AiProvider.HERMES

    # Backward compatibility: the project historically selected direct Yandex
    # through AI_DIRECT_YANDEX and otherwise called the local Hermes gateway.
    if _env(source, "YANDEX_AI_API_KEY") or _env(source, "YANDEX_API_KEY"):
        if _env_bool(source, "AI_DIRECT_YANDEX", True):
            return AiProvider.YANDEX
    return AiProvider.HERMES


def fallback_provider(environ: Mapping[str, str] | None = None) -> AiProvider | None:
    source = environ if environ is not None else os.environ
    configured = _env(source, "AI_FALLBACK_PROVIDER")
    if not configured:
        return None
    return parse_provider(configured, setting_name="AI_FALLBACK_PROVIDER")


def provider_chain(environ: Mapping[str, str] | None = None) -> list[AiProvider]:
    source = environ if environ is not None else os.environ
    result = [primary_provider(source)]
    fallback = fallback_provider(source)
    if fallback is not None and fallback not in result:
        result.append(fallback)
    return result


def _yandex_folder_id(source: Mapping[str, str]) -> str:
    return (
        _env(source, "YANDEX_AI_FOLDER_ID")
        or _env(source, "YANDEX_FOLDER_ID")
        or _env(source, "YANDEX_CLOUD_FOLDER")
        or _env(source, "OPENAI_PROJECT")
    )


def _yandex_model(source: Mapping[str, str], requested_model: str) -> str:
    value = _env(source, "YANDEX_AI_MODEL") or requested_model or "yandexgpt-lite"
    if value.startswith("gpt://"):
        return value
    alias = YANDEX_MODEL_ALIASES.get(value.lower(), value)
    folder_id = _yandex_folder_id(source)
    return f"gpt://{folder_id}/{alias}" if folder_id else alias


def _amvera_model(value: str) -> str:
    raw = (value or AMVERA_LLM_MODEL).strip()
    return AMVERA_MODEL_ALIASES.get(raw.lower(), raw)


def provider_model(
    provider: AiProvider,
    requested_model: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    source = environ if environ is not None else os.environ
    if provider == AiProvider.YANDEX:
        return _yandex_model(source, requested_model)
    if provider == AiProvider.HERMES:
        return _env(source, "HERMES_MODEL") or "hermes-agent"
    if provider == AiProvider.DEEPSEEK:
        return _env(source, "DEEPSEEK_MODEL") or requested_model
    if provider == AiProvider.OPENAI_COMPATIBLE:
        return _env(source, "OPENAI_COMPATIBLE_MODEL") or requested_model
    if provider == AiProvider.AMVERA_LLM:
        return _amvera_model(_env(source, "AMVERA_LLM_MODEL") or requested_model or AMVERA_LLM_MODEL)
    return requested_model


def build_provider_runtime(
    provider: AiProvider,
    *,
    requested_model: str,
    hermes_base_url: str,
    hermes_api_key: str,
    environ: Mapping[str, str] | None = None,
) -> AiProviderRuntime:
    source = environ if environ is not None else os.environ
    model = provider_model(provider, requested_model, source)
    if not model:
        raise AiProviderConfigError(f"No model configured for provider {provider.value}")

    if provider == AiProvider.HERMES:
        base_url = _env(source, "HERMES_API_BASE_URL") or hermes_base_url
        api_key = _env(source, "HERMES_API_KEY") or hermes_api_key
        if not base_url:
            raise AiProviderConfigError("HERMES_API_BASE_URL is empty")
        client = HermesClient(
            base_url,
            api_key,
            timeout_seconds=_env_int(
                source,
                "HERMES_REQUEST_TIMEOUT_SECONDS",
                60,
                minimum=10,
                maximum=300,
            ),
            provider_name=provider.value,
        )
    elif provider == AiProvider.YANDEX:
        base_url = _env(source, "YANDEX_AI_BASE_URL", YANDEX_BASE_URL)
        api_key = _env(source, "YANDEX_AI_API_KEY") or _env(source, "YANDEX_API_KEY")
        if not api_key:
            raise AiProviderConfigError("YANDEX_AI_API_KEY or YANDEX_API_KEY is empty")
        client = YandexOpenAIClient(
            base_url,
            api_key,
            _yandex_folder_id(source),
            provider_name=provider.value,
        )
    elif provider == AiProvider.DEEPSEEK:
        base_url = _env(source, "DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL)
        api_key = _env(source, "DEEPSEEK_API_KEY")
        if not api_key:
            raise AiProviderConfigError("DEEPSEEK_API_KEY is empty")
        client = HermesClient(
            base_url,
            api_key,
            provider_name=provider.value,
        )
    elif provider == AiProvider.OPENAI_COMPATIBLE:
        base_url = _env(source, "OPENAI_COMPATIBLE_BASE_URL") or _env(source, "OPENAI_BASE_URL")
        if not base_url:
            raise AiProviderConfigError("OPENAI_COMPATIBLE_BASE_URL is empty")
        client = HermesClient(
            base_url,
            _env(source, "OPENAI_COMPATIBLE_API_KEY") or _env(source, "OPENAI_API_KEY"),
            provider_name=provider.value,
        )
    elif provider == AiProvider.AMVERA_LLM:
        base_url = _env(source, "AMVERA_LLM_BASE_URL", AMVERA_LLM_BASE_URL)
        api_key = _env(source, "AMVERA_LLM_API_KEY")
        if not api_key:
            raise AiProviderConfigError("AMVERA_LLM_API_KEY is empty")
        client = HermesClient(
            base_url,
            api_key,
            timeout_seconds=_env_int(
                source,
                "AMVERA_LLM_REQUEST_TIMEOUT_SECONDS",
                60,
                minimum=10,
                maximum=300,
            ),
            provider_name=provider.value,
        )
    else:
        raise AiProviderConfigError(f"Unsupported AI provider: {provider}")

    return AiProviderRuntime(provider=provider, model=model, client=client)


def build_provider_chain(
    *,
    requested_model: str,
    hermes_base_url: str,
    hermes_api_key: str,
    environ: Mapping[str, str] | None = None,
) -> list[AiProviderRuntime]:
    source = environ if environ is not None else os.environ
    return [
        build_provider_runtime(
            provider,
            requested_model=requested_model,
            hermes_base_url=hermes_base_url,
            hermes_api_key=hermes_api_key,
            environ=source,
        )
        for provider in provider_chain(source)
    ]
