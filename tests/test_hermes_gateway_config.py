from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from hermes_cli.tools_config import _get_platform_tools


def load_gateway_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "run-hermes-gateway.py"
    spec = importlib.util.spec_from_file_location("run_hermes_gateway", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load run-hermes-gateway.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HermesGatewayConfigTests(unittest.TestCase):
    def test_yandex_key_writes_explicit_provider_config(self) -> None:
        module = load_gateway_module()
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HERMES_HOME": tmp,
                "YANDEX_API_KEY": "test-yandex-key",
                "YANDEX_FOLDER_ID": "folder-123",
                "HERMES_MODEL_DEFAULT": "yandexgpt-lite",
            }
            with patch.dict(os.environ, env, clear=True):
                selected = module.prepare_hermes_provider_config()
                config = yaml.safe_load((Path(tmp) / "config.yaml").read_text(encoding="utf-8"))

        self.assertEqual(selected, ("yandex", "gpt://folder-123/yandexgpt-lite/latest", "https://ai.api.cloud.yandex.net/v1"))
        self.assertEqual(config["model"]["provider"], "yandex")
        self.assertEqual(config["model"]["default"], "gpt://folder-123/yandexgpt-lite/latest")
        self.assertEqual(config["model"]["default_headers"]["Authorization"], "Api-Key ${YANDEX_API_KEY}")
        self.assertEqual(config["model"]["default_headers"]["OpenAI-Project"], "folder-123")
        self.assertEqual(config["providers"]["yandex"]["key_env"], "YANDEX_API_KEY")
        self.assertEqual(config["providers"]["yandex"]["default_model"], "gpt://folder-123/yandexgpt-lite/latest")
        self.assertEqual(config["model"]["context_length"], 64_000)
        self.assertEqual(config["toolsets"], [])
        self.assertEqual(config["platform_toolsets"]["cli"], [])
        self.assertEqual(config["platform_toolsets"]["api_server"], [])
        self.assertEqual(config["compression"]["threshold"], 0.35)
        self.assertEqual(config["compression"]["protect_last_n"], 6)
        self.assertEqual(config["agent"]["api_max_retries"], 1)
        self.assertEqual(_get_platform_tools(config, "api_server"), set())

    def test_deepseek_provider_wins_when_yandex_env_is_also_present(self) -> None:
        module = load_gateway_module()
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HERMES_HOME": tmp,
                "YANDEX_API_KEY": "test-yandex-key",
                "YANDEX_FOLDER_ID": "folder-123",
                "DEEPSEEK_API_KEY": "test-deepseek-key",
                "DEEPSEEK_MODEL": "deepseek-chat",
            }
            with patch.dict(os.environ, env, clear=True):
                selected = module.prepare_hermes_provider_config()
                config = yaml.safe_load((Path(tmp) / "config.yaml").read_text(encoding="utf-8"))

        self.assertEqual(selected[0], "deepseek")
        self.assertEqual(config["model"]["provider"], "deepseek")
        self.assertIn("deepseek", config["providers"])
        self.assertNotIn("yandex", config["providers"])

    def test_deepseek_only_writes_deepseek_provider_config(self) -> None:
        module = load_gateway_module()
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HERMES_HOME": tmp,
                "DEEPSEEK_API_KEY": "test-deepseek-key",
                "HERMES_MODEL_DEFAULT": "deepseek-v4-flash",
            }
            with patch.dict(os.environ, env, clear=True):
                selected = module.prepare_hermes_provider_config()
                config = yaml.safe_load((Path(tmp) / "config.yaml").read_text(encoding="utf-8"))

        self.assertEqual(selected, ("deepseek", "deepseek-v4-flash", "https://api.deepseek.com"))
        self.assertEqual(config["model"]["provider"], "deepseek")
        self.assertEqual(config["providers"]["deepseek"]["key_env"], "DEEPSEEK_API_KEY")

    def test_amvera_llm_defaults_to_deepseek_v4(self) -> None:
        module = load_gateway_module()
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HERMES_HOME": tmp,
                "AMVERA_LLM_API_KEY": "test-amvera-key",
                "YANDEX_API_KEY": "test-yandex-key",
            }
            with patch.dict(os.environ, env, clear=True):
                selected = module.prepare_hermes_provider_config()
                config = yaml.safe_load((Path(tmp) / "config.yaml").read_text(encoding="utf-8"))

        self.assertEqual(selected, ("amvera_llm", "deepseek-V4", "https://inference.waw0.amvera.ru/v1"))
        self.assertEqual(config["model"]["provider"], "amvera_llm")
        self.assertEqual(config["model"]["default"], "deepseek-V4")
        self.assertEqual(config["model"]["base_url"], "https://inference.waw0.amvera.ru/v1")
        self.assertEqual(config["providers"]["amvera_llm"]["key_env"], "AMVERA_LLM_API_KEY")
        self.assertEqual(config["agent"]["api_max_retries"], 1)
        self.assertNotIn("memory", config["agent"]["disabled_toolsets"])
        self.assertNotIn("skills", config["agent"]["disabled_toolsets"])

    def test_explicit_openai_compatible_provider_is_supported(self) -> None:
        module = load_gateway_module()
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HERMES_HOME": tmp,
                "HERMES_INFERENCE_PROVIDER": "openai_compatible",
                "OPENAI_COMPATIBLE_BASE_URL": "https://llm.example.test/v1",
                "OPENAI_COMPATIBLE_API_KEY": "test-key",
                "OPENAI_COMPATIBLE_MODEL": "example-model",
            }
            with patch.dict(os.environ, env, clear=True):
                selected = module.prepare_hermes_provider_config()
                config = yaml.safe_load((Path(tmp) / "config.yaml").read_text(encoding="utf-8"))

        self.assertEqual(selected, ("openai_compatible", "example-model", "https://llm.example.test/v1"))
        self.assertEqual(config["model"]["provider"], "openai_compatible")
        self.assertEqual(config["providers"]["openai_compatible"]["key_env"], "OPENAI_COMPATIBLE_API_KEY")

    def test_explicit_provider_fails_fast_when_configuration_is_incomplete(self) -> None:
        module = load_gateway_module()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "HERMES_HOME": tmp,
                    "HERMES_INFERENCE_PROVIDER": "amvera_llm",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "incomplete"):
                    module.prepare_hermes_provider_config()


if __name__ == "__main__":
    unittest.main()
