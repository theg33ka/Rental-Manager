from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


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
        self.assertEqual(config["model"]["default_headers"]["Authorization"], "Api-Key test-yandex-key")
        self.assertEqual(config["model"]["default_headers"]["OpenAI-Project"], "folder-123")
        self.assertEqual(config["providers"]["yandex"]["key_env"], "YANDEX_API_KEY")
        self.assertEqual(config["providers"]["yandex"]["default_model"], "gpt://folder-123/yandexgpt-lite/latest")
        self.assertEqual(config["toolsets"], [])
        self.assertEqual(config["platform_toolsets"]["cli"], ["no_mcp"])
        self.assertEqual(config["agent"]["api_max_retries"], 1)

    def test_yandex_provider_wins_when_deepseek_env_is_also_present(self) -> None:
        module = load_gateway_module()
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HERMES_HOME": tmp,
                "YANDEX_API_KEY": "test-yandex-key",
                "YANDEX_FOLDER_ID": "folder-123",
                "DEEPSEEK_API_KEY": "test-deepseek-key",
            }
            with patch.dict(os.environ, env, clear=True):
                selected = module.prepare_hermes_provider_config()
                config = yaml.safe_load((Path(tmp) / "config.yaml").read_text(encoding="utf-8"))

        self.assertEqual(selected[0], "yandex")
        self.assertEqual(config["model"]["provider"], "yandex")
        self.assertIn("yandex", config["providers"])
        self.assertNotIn("deepseek", config["providers"])

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


if __name__ == "__main__":
    unittest.main()
