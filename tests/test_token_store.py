"""Token persistence checks. No network, no real credentials."""
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tasksync.clients.ticktick_client import TickTickClient
from tasksync.config import load_config
from tasksync.main import save_if_refreshed
from tasksync.token_store import TOKEN_FILE_NAME, load_tokens, save_tokens

ACCESS = "access-AAAA1111"
REFRESH = "refresh-BBBB2222"


class TokenStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _config(self, access="env-access", refresh="env-refresh"):
        env = {
            "NOTION_TOKEN": "x",
            "TICKTICK_ACCESS_TOKEN": access,
            "TICKTICK_REFRESH_TOKEN": refresh,
            "DATA_DIR": str(self.dir),
        }
        with patch.dict(os.environ, env), patch("tasksync.config.load_dotenv"):
            return load_config()

    def test_save_then_load_returns_same_tokens(self):
        path = save_tokens(self.dir, ACCESS, REFRESH)
        self.assertEqual(path, self.dir / TOKEN_FILE_NAME)
        self.assertEqual(load_tokens(self.dir), (ACCESS, REFRESH))
        self.assertIn("refreshed_at", json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual([p.name for p in self.dir.iterdir()], [TOKEN_FILE_NAME])

    @unittest.skipUnless(os.name == "posix", "file modes apply on POSIX only")
    def test_file_mode_is_owner_only(self):
        path = save_tokens(self.dir, ACCESS, REFRESH)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_file_tokens_override_environment(self):
        save_tokens(self.dir, ACCESS, REFRESH)
        config = self._config()
        self.assertEqual((config.ticktick.access_token, config.ticktick.refresh_token), (ACCESS, REFRESH))

    def test_malformed_file_falls_back_to_environment(self):
        (self.dir / TOKEN_FILE_NAME).write_text("{not json", encoding="utf-8")
        with self.assertLogs("tasksync", level="WARNING") as logs:
            config = self._config()
        self.assertEqual((config.ticktick.access_token, config.ticktick.refresh_token), ("env-access", "env-refresh"))
        self.assertIn(TOKEN_FILE_NAME, "\n".join(logs.output))

    def test_missing_file_falls_back_to_environment(self):
        # A missing file is the normal state before the first refresh, so it logs nothing.
        with self.assertNoLogs("tasksync", level="WARNING"):
            config = self._config()
        self.assertEqual(config.ticktick.access_token, "env-access")

    def test_refresh_writes_file_and_logs_no_token(self):
        config = self._config()
        client = Mock()
        client.get_tokens.return_value = (ACCESS, REFRESH)
        with self.assertLogs("tasksync", level="DEBUG") as logs:
            save_if_refreshed(client, config, logging.getLogger("tasksync"))
        self.assertEqual(load_tokens(self.dir), (ACCESS, REFRESH))
        text = "\n".join(logs.output)
        self.assertIn("refreshed and saved to", text)
        self.assertIn(str(self.dir / TOKEN_FILE_NAME), text)
        for secret in (ACCESS, REFRESH):
            self.assertNotIn(secret, text)

    def test_unchanged_tokens_write_nothing(self):
        config = self._config()
        client = Mock()
        client.get_tokens.return_value = ("env-access", "env-refresh")
        save_if_refreshed(client, config, logging.getLogger("tasksync"))
        self.assertFalse((self.dir / TOKEN_FILE_NAME).exists())

    def test_failed_refresh_logs_status_not_provider_body(self):
        client = TickTickClient("id", "secret", ACCESS, REFRESH, "proj")
        response = Mock(status_code=400, text=f"bad token {ACCESS} {REFRESH}")
        with patch("tasksync.clients.ticktick_client.requests.post", return_value=response):
            with self.assertLogs("tasksync", level="DEBUG") as logs:
                self.assertFalse(client._refresh_access_token())
        text = "\n".join(logs.output)
        self.assertIn("400", text)
        for secret in (ACCESS, REFRESH):
            self.assertNotIn(secret, text)

    def test_api_error_logs_status_not_body(self):
        client = TickTickClient("id", "secret", ACCESS, REFRESH, "proj")
        response = Mock(status_code=500, text=f"echo {ACCESS}")
        response.raise_for_status.side_effect = RuntimeError("HTTP 500")
        client._session = Mock(request=Mock(return_value=response))
        with self.assertLogs("tasksync", level="DEBUG") as logs:
            with self.assertRaises(RuntimeError):
                client._request("GET", "/project")
        self.assertNotIn(ACCESS, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
