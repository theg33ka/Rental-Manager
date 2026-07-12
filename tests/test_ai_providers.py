from __future__ import annotations

import unittest

from rental_manager.services.ai_providers import (
    DEEPSEEK_BASE_URL,
    AiProvider,
    AiProviderConfigError,
    build_provider_runtime,
    provider_chain,
)
from rental_manager.services.deepseek_client import DeepSeekClient


class AiProviderConfigTests(unittest.TestCase):
    def test_provider_chain_only_contains_deepseek(self) -> None:
        chain = provider_chain(
            {
                "AI_PROVIDER": "hermes",
                "AMVERA_LLM_API_KEY": "legacy-key",
                "YANDEX_API_KEY": "legacy-key",
            }
        )

        self.assertEqual(chain, [AiProvider.DEEPSEEK])

    def test_runtime_uses_saved_key_and_selected_model(self) -> None:
        runtime = build_provider_runtime(
            AiProvider.DEEPSEEK,
            requested_model="deepseek-v4-flash",
            deepseek_api_key="settings-key",
            environ={},
        )

        self.assertIsInstance(runtime.client, DeepSeekClient)
        self.assertEqual(runtime.provider, AiProvider.DEEPSEEK)
        self.assertEqual(runtime.model, "deepseek-v4-flash")
        self.assertEqual(runtime.client.base_url, DEEPSEEK_BASE_URL)
        self.assertEqual(runtime.client.api_key, "settings-key")
        self.assertEqual(runtime.client.provider_name, "deepseek")

    def test_environment_overrides_saved_key_and_model(self) -> None:
        runtime = build_provider_runtime(
            AiProvider.DEEPSEEK,
            requested_model="deepseek-v4-flash",
            deepseek_api_key="settings-key",
            environ={
                "DEEPSEEK_API_KEY": "environment-key",
                "DEEPSEEK_MODEL": "deepseek-v4-pro",
            },
        )

        self.assertEqual(runtime.model, "deepseek-v4-pro")
        self.assertEqual(runtime.client.api_key, "environment-key")

    def test_missing_api_key_is_rejected(self) -> None:
        with self.assertRaises(AiProviderConfigError):
            build_provider_runtime(
                AiProvider.DEEPSEEK,
                requested_model="deepseek-v4-flash",
                environ={},
            )

    def test_unknown_model_is_rejected(self) -> None:
        with self.assertRaises(AiProviderConfigError):
            build_provider_runtime(
                AiProvider.DEEPSEEK,
                requested_model="deepseek-chat",
                deepseek_api_key="key",
                environ={},
            )

    def test_timeout_is_configurable(self) -> None:
        default_runtime = build_provider_runtime(
            AiProvider.DEEPSEEK,
            requested_model="deepseek-v4-flash",
            deepseek_api_key="key",
            environ={},
        )
        overridden_runtime = build_provider_runtime(
            AiProvider.DEEPSEEK,
            requested_model="deepseek-v4-flash",
            deepseek_api_key="key",
            environ={"DEEPSEEK_REQUEST_TIMEOUT_SECONDS": "90"},
        )

        self.assertEqual(default_runtime.client.timeout_seconds, 60)
        self.assertEqual(overridden_runtime.client.timeout_seconds, 90)

    def test_invalid_timeout_is_rejected(self) -> None:
        with self.assertRaises(AiProviderConfigError):
            build_provider_runtime(
                AiProvider.DEEPSEEK,
                requested_model="deepseek-v4-flash",
                deepseek_api_key="key",
                environ={"DEEPSEEK_REQUEST_TIMEOUT_SECONDS": "long"},
            )
