from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.update_server_env import main, update_env


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

    def test_ensure_random_creates_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env"
            path.write_text("APP_ENV=production\n", encoding="utf-8")
            argv = [
                "update_server_env.py", "--env-file", str(path),
                "--ensure-random", "ADMIN_SESSION_SECRET",
            ]
            with patch("sys.argv", argv):
                self.assertEqual(0, main())
            first = path.read_text(encoding="utf-8")
            with patch("sys.argv", argv):
                self.assertEqual(0, main())
            self.assertEqual(first, path.read_text(encoding="utf-8"))
            self.assertRegex(first, r"ADMIN_SESSION_SECRET=.{40,}")


if __name__ == "__main__":
    unittest.main()
