from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.update_server_env import update_env


class UpdateServerEnvTests(unittest.TestCase):
    def test_updates_values_without_removing_unrelated_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env"
            path.write_text("APP_ENV=production\nAI_MODEL=old\n", encoding="utf-8")
            update_env(path, {"AI_MODEL": "new", "AI_PROVIDER": "openrouter"})
            content = path.read_text(encoding="utf-8")
            self.assertIn("APP_ENV=production", content)
            self.assertIn("AI_MODEL=new", content)
            self.assertIn("AI_PROVIDER=openrouter", content)
            self.assertNotIn("AI_MODEL=old", content)


if __name__ == "__main__":
    unittest.main()
