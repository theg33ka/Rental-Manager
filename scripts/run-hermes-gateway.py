from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


YANDEX_BASE_URL = "https://ai.api.cloud.yandex.net/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
YANDEX_MODEL_ALIASES = {
    "yandexgpt-lite": "yandexgpt-lite/latest",
    "yandexgpt": "yandexgpt/latest",
    "yandexgpt-pro": "yandexgpt/latest",
}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _hermes_home() -> Path:
    return Path(_env("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        backup = path.with_suffix(f"{path.suffix}.rental-manager-bak")
        try:
            backup.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        except Exception:
            pass
        print(f"[HERMES] ignored unreadable config {path}: {exc}", flush=True)
        return {}
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _yandex_folder_id() -> str:
    return _env("YANDEX_FOLDER_ID") or _env("YANDEX_CLOUD_FOLDER") or _env("OPENAI_PROJECT")


def _resolve_yandex_model(model: str, folder_id: str) -> str:
    value = (model or "yandexgpt-lite").strip()
    if value.startswith("gpt://"):
        return value
    alias = YANDEX_MODEL_ALIASES.get(value.lower(), value)
    return f"gpt://{folder_id}/{alias}" if folder_id else alias


def _configure_yandex(config: dict[str, Any]) -> tuple[str, str, str] | None:
    api_key_env = "YANDEX_AI_API_KEY" if _env("YANDEX_AI_API_KEY") else "YANDEX_API_KEY"
    if not _env(api_key_env):
        return None

    folder_id = _yandex_folder_id()
    base_url = _env("YANDEX_AI_BASE_URL", YANDEX_BASE_URL).rstrip("/")
    model = _resolve_yandex_model(
        _env("YANDEX_AI_MODEL") or _env("HERMES_MODEL_DEFAULT", "yandexgpt-lite"),
        folder_id,
    )

    if folder_id and not _env("OPENAI_PROJECT"):
        os.environ["OPENAI_PROJECT"] = folder_id
    if not _env("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = _env(api_key_env)
    os.environ.setdefault("OPENAI_BASE_URL", base_url)
    os.environ["HERMES_INFERENCE_PROVIDER"] = "yandex"

    model_config = config.setdefault("model", {})
    if not isinstance(model_config, dict):
        model_config = {}
        config["model"] = model_config
    model_config.update(
        {
            "provider": "yandex",
            "default": model,
            "base_url": base_url,
            "api_mode": "chat_completions",
            # Hermes 0.17 refuses values below 64K, while Yandex currently
            # enforces a 32K input limit. The conservative compression policy
            # below and an empty API-server tool surface keep the real request
            # safely below Yandex's limit.
            "context_length": 64_000,
            "max_tokens": 1200,
            "default_headers": {
                "Authorization": f"Api-Key ${{{api_key_env}}}",
                **({"OpenAI-Project": folder_id} if folder_id else {}),
            },
        }
    )

    providers = config.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        config["providers"] = providers
    providers["yandex"] = {
        "name": "Yandex AI Studio",
        "api": base_url,
        "key_env": api_key_env,
        "default_model": model,
        "transport": "chat_completions",
        "api_mode": "chat_completions",
    }
    return ("yandex", model, base_url)


def _configure_deepseek(config: dict[str, Any]) -> tuple[str, str, str] | None:
    if not _env("DEEPSEEK_API_KEY"):
        return None

    base_url = _env("DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL).rstrip("/")
    model = _env("DEEPSEEK_MODEL") or _env("HERMES_MODEL_DEFAULT", "deepseek-chat")
    if not _env("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = _env("DEEPSEEK_API_KEY")
    os.environ.setdefault("OPENAI_BASE_URL", base_url)
    os.environ["HERMES_INFERENCE_PROVIDER"] = "deepseek"

    model_config = config.setdefault("model", {})
    if not isinstance(model_config, dict):
        model_config = {}
        config["model"] = model_config
    model_config.update(
        {
            "provider": "deepseek",
            "default": model,
            "base_url": base_url,
            "api_mode": "chat_completions",
            "max_tokens": 1200,
        }
    )

    providers = config.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        config["providers"] = providers
    providers["deepseek"] = {
        "name": "DeepSeek",
        "api": base_url,
        "key_env": "DEEPSEEK_API_KEY",
        "default_model": model,
        "transport": "chat_completions",
        "api_mode": "chat_completions",
    }
    return ("deepseek", model, base_url)


def _configure_openai_compatible(config: dict[str, Any]) -> tuple[str, str, str] | None:
    base_url = _env("OPENAI_COMPATIBLE_BASE_URL")
    model = _env("OPENAI_COMPATIBLE_MODEL")
    if not base_url or not model:
        return None
    key_env = "OPENAI_COMPATIBLE_API_KEY"
    api_key = _env(key_env)
    if api_key and not _env("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_BASE_URL"] = base_url.rstrip("/")
    os.environ["HERMES_INFERENCE_PROVIDER"] = "openai_compatible"

    model_config = config.setdefault("model", {})
    model_config.update(
        {
            "provider": "openai_compatible",
            "default": model,
            "base_url": base_url.rstrip("/"),
            "api_mode": "chat_completions",
            "max_tokens": 1200,
        }
    )
    providers = config.setdefault("providers", {})
    providers["openai_compatible"] = {
        "name": "OpenAI-compatible API",
        "api": base_url.rstrip("/"),
        "key_env": key_env,
        "default_model": model,
        "transport": "chat_completions",
        "api_mode": "chat_completions",
    }
    return ("openai_compatible", model, base_url.rstrip("/"))


def _configure_amvera_llm(config: dict[str, Any]) -> tuple[str, str, str] | None:
    base_url = _env("AMVERA_LLM_BASE_URL")
    model = _env("AMVERA_LLM_MODEL")
    if not base_url or not model:
        return None
    key_env = "AMVERA_LLM_API_KEY"
    api_key = _env(key_env)
    if api_key and not _env("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_BASE_URL"] = base_url.rstrip("/")
    os.environ["HERMES_INFERENCE_PROVIDER"] = "amvera_llm"

    model_config = config.setdefault("model", {})
    model_config.update(
        {
            "provider": "amvera_llm",
            "default": model,
            "base_url": base_url.rstrip("/"),
            "api_mode": "chat_completions",
            "max_tokens": 1200,
        }
    )
    providers = config.setdefault("providers", {})
    providers["amvera_llm"] = {
        "name": "Amvera LLM Inference",
        "api": base_url.rstrip("/"),
        "key_env": key_env,
        "default_model": model,
        "transport": "chat_completions",
        "api_mode": "chat_completions",
    }
    return ("amvera_llm", model, base_url.rstrip("/"))


PROVIDER_BUILDERS = {
    "yandex": _configure_yandex,
    "deepseek": _configure_deepseek,
    "openai_compatible": _configure_openai_compatible,
    "amvera_llm": _configure_amvera_llm,
}


def prepare_hermes_provider_config() -> tuple[str, str, str] | None:
    config_path = _hermes_home() / "config.yaml"
    config = _load_yaml(config_path)
    requested = _env("HERMES_INFERENCE_PROVIDER").lower().replace("-", "_")
    if requested:
        builder = PROVIDER_BUILDERS.get(requested)
        if builder is None:
            allowed = ", ".join(PROVIDER_BUILDERS)
            raise RuntimeError(f"Unsupported HERMES_INFERENCE_PROVIDER={requested}; allowed: {allowed}")
        selected = builder(config)
        if not selected:
            raise RuntimeError(f"Hermes inference provider {requested} is selected but its URL/key/model is incomplete")
    else:
        # Preserve the legacy priority when no explicit upstream provider is
        # configured. New deployments should set HERMES_INFERENCE_PROVIDER.
        selected = (
            _configure_yandex(config)
            or _configure_deepseek(config)
            or _configure_openai_compatible(config)
            or _configure_amvera_llm(config)
        )
    if not selected:
        print("[HERMES] no configured inference provider found; using existing Hermes config", flush=True)
        return None

    # Rental Manager sends all domain data in the prompt context and executes
    # confirmed operations itself. The API-server agent therefore needs no
    # Hermes tools. An explicit empty list matters: an absent platform entry
    # loads the 18-tool default and adds roughly 42 KB of schemas to every
    # Yandex request.
    config["toolsets"] = []
    platform_toolsets = config.setdefault("platform_toolsets", {})
    if not isinstance(platform_toolsets, dict):
        platform_toolsets = {}
        config["platform_toolsets"] = platform_toolsets
    platform_toolsets["cli"] = []
    platform_toolsets["api_server"] = []

    compression = config.setdefault("compression", {})
    if not isinstance(compression, dict):
        compression = {}
        config["compression"] = compression
    compression.update(
        {
            "enabled": True,
            "threshold": 0.35,
            "target_ratio": 0.12,
            "protect_last_n": 6,
            "protect_first_n": 1,
        }
    )

    agent = config.setdefault("agent", {})
    if not isinstance(agent, dict):
        agent = {}
        config["agent"] = agent
    agent.update(
        {
            "max_turns": 4,
            "api_max_retries": 1,
            "environment_probe": False,
            "task_completion_guidance": False,
            "parallel_tool_call_guidance": False,
            "disabled_toolsets": [
                "browser",
                "web",
                "image_gen",
                "video_gen",
                "x_search",
                "memory",
                "skills",
                "context_engine",
            ],
        }
    )

    _write_yaml(config_path, config)
    provider, model, base_url = selected
    print(f"[HERMES] wrote config provider={provider} model={model} base_url={base_url}", flush=True)
    return selected


def main() -> int:
    # Telegram belongs to Rental Manager; Hermes must not attach the same bot token.
    for name in list(os.environ):
        if name.startswith("TELEGRAM_"):
            os.environ.pop(name, None)

    prepare_hermes_provider_config()

    # Hermes 0.17 puts site-packages/plugins before site-packages while starting
    # the gateway. That shadows the real top-level cron package with
    # plugins/cron, so gateway/run.py later cannot import cron.scheduler_provider.
    # Preloading the real cron package pins it in sys.modules before Hermes
    # mutates sys.path.
    import cron
    import cron.jobs  # noqa: F401
    import cron.scheduler_provider  # noqa: F401

    print(f"[HERMES] using cron package: {getattr(cron, '__file__', '')}", flush=True)

    from hermes_cli.main import main as hermes_main

    sys.argv = ["hermes", "gateway"]
    return int(hermes_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
