from __future__ import annotations

import unittest

from rental_manager.services.ai_providers import (
    AiProvider,
    AiProviderConfigError,
    build_provider_runtime,
    provider_chain,
)
from rental_manager.services.hermes_client import HermesClient, YandexOpenAIClient


class AiProviderConfigTests(unittest.TestCase):
    def test_explicit_provider_and_fallback_are_ordered(self) -> None:
        chain = provider_chain(
            {
                "AI_PROVIDER": "hermes",
                "AI_FALLBACK_PROVIDER": "yandex",
            }
        )

        self.assertEqual(chain, [AiProvider.HERMES, AiProvider.YANDEX])

    def test_legacy_yandex_selection_remains_compatible(self) -> None:
        chain = provider_chain(
            {
                "YANDEX_API_KEY": "key",
                "AI_DIRECT_YANDEX": "1",
            }
        )

        self.assertEqual(chain, [AiProvider.YANDEX])

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaises(AiProviderConfigError):
            provider_chain({"AI_PROVIDER": "mystery"})

    def test_yandex_runtime_uses_new_env_aliases(self) -> None:
        runtime = build_provider_runtime(
            AiProvider.YANDEX,
            requested_model="ignored",
            hermes_base_url="",
            hermes_api_key="",
            environ={
                "YANDEX_AI_API_KEY": "key",
                "YANDEX_AI_FOLDER_ID": "folder",
                "YANDEX_AI_MODEL": "yandexgpt-lite",
            },
        )

        self.assertIsInstance(runtime.client, YandexOpenAIClient)
        self.assertEqual(runtime.model, "gpt://folder/yandexgpt-lite/latest")

    def test_hermes_runtime_uses_longer_configurable_timeout(self) -> None:
        default_runtime = build_provider_runtime(
            AiProvider.HERMES,
            requested_model="hermes-agent",
            hermes_base_url="http://127.0.0.1:8642",
            hermes_api_key="key",
            environ={},
        )
        overridden_runtime = build_provider_runtime(
            AiProvider.HERMES,
            requested_model="hermes-agent",
            hermes_base_url="http://127.0.0.1:8642",
            hermes_api_key="key",
            environ={"HERMES_REQUEST_TIMEOUT_SECONDS": "90"},
        )

        self.assertIsInstance(default_runtime.client, HermesClient)
        self.assertEqual(default_runtime.client.timeout_seconds, 60)
        self.assertEqual(overridden_runtime.client.timeout_seconds, 90)

    def test_invalid_hermes_timeout_is_rejected(self) -> None:
        with self.assertRaises(AiProviderConfigError):
            build_provider_runtime(
                AiProvider.HERMES,
                requested_model="hermes-agent",
                hermes_base_url="http://127.0.0.1:8642",
                hermes_api_key="key",
                environ={"HERMES_REQUEST_TIMEOUT_SECONDS": "долго"},
            )
