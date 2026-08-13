from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.db import Database
from app.services.model_config import ModelConfigService


class ModelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "test.db")
        self.database.initialize()
        self.settings = Settings(
            _env_file=None,
            ai_provider="openrouter",
            ai_base_url="https://openrouter.ai/api/v1",
            ai_model="openai/gpt-5.4-mini",
            ai_api_key="shared-production-secret",
        )
        self.service = ModelConfigService(self.database, self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_response_masks_api_key_and_never_returns_plaintext(self) -> None:
        payload = self.service.get()
        self.assertEqual("bi_center_deployment", payload["effective"]["source"])
        self.assertTrue(payload["effective"]["hasApiKey"])
        self.assertNotIn("apiKey", payload["effective"])
        self.assertNotIn("shared-production-secret", str(payload))

    def test_blank_api_key_reuses_current_key_and_reset_restores_inherited(self) -> None:
        updated = self.service.update(
            {
                "provider": "openrouter",
                "apiBase": "https://openrouter.ai/api/v1/chat/completions",
                "modelName": "z-ai/glm-5.2",
                "apiKey": "",
            }
        )
        self.assertEqual("weekly_assistant", updated["effective"]["source"])
        self.assertEqual("shared-production-secret", self.service.override()["apiKey"])
        self.assertEqual("https://openrouter.ai/api/v1", self.service.override()["apiBase"])

        reset = self.service.reset()
        self.assertIsNone(reset["override"])
        self.assertEqual("openai/gpt-5.4-mini", reset["effective"]["modelName"])

    def test_connection_test_uses_compatible_openrouter_payload_without_leaking_key(self) -> None:
        response = {"choices": [{"message": {"content": '{"status":"ok"}'}}]}
        with patch("app.services.model_config.request_json", return_value=response) as request:
            result = self.service.test(
                {
                    "provider": "openrouter",
                    "apiBase": "https://openrouter.ai/api/v1",
                    "modelName": "openai/gpt-5.4-mini",
                    "apiKey": "",
                }
            )
        sent = request.call_args.kwargs["payload"]
        self.assertEqual({"effort": "none", "exclude": True}, sent["reasoning"])
        self.assertIn("max_tokens", sent)
        self.assertNotIn("temperature", sent)
        self.assertNotIn("apiKey", result)
        self.assertTrue(result["ok"])
        self.assertTrue(self.service.test_status()["ok"])

    def test_failed_connection_is_persisted_without_the_api_key(self) -> None:
        from app.integrations.http_json import JsonHttpError

        with patch(
            "app.services.model_config.request_json",
            side_effect=JsonHttpError("HTTP 403: region unavailable"),
        ):
            with self.assertRaisesRegex(Exception, "region unavailable"):
                self.service.test(
                    {
                        "provider": "openrouter",
                        "apiBase": "https://openrouter.ai/api/v1",
                        "modelName": "openai/gpt-5.4-mini",
                        "apiKey": "",
                    }
                )
        status = self.service.test_status()
        self.assertTrue(status["tested"])
        self.assertFalse(status["ok"])
        self.assertNotIn("shared-production-secret", str(status))


if __name__ == "__main__":
    unittest.main()
