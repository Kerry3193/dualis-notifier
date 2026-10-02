import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config import ConfigError, PROJECT_DIR, load_config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.env = {"DUALIS_USER": "s123456", "DUALIS_PASSWD": " secret&+% ",
                    "DISCORD_WEBHOOK": "https://discord.com/api/webhooks/123/synthetic-token"}

    def test_defaults_and_password_are_preserved(self):
        config = load_config(self.env)
        self.assertEqual(config["passwd"], " secret&+% ")
        self.assertEqual(config["semester_id"], "")
        self.assertEqual(config["timeout"], (5, 30))
        self.assertEqual(config["data_dir"], PROJECT_DIR / "data")

    def test_missing_fields_are_rejected_without_leaking_secrets(self):
        for key in self.env:
            with self.subTest(key=key), self.assertRaises(ConfigError) as error:
                load_config({name: value for name, value in self.env.items() if name != key})
            self.assertNotIn("synthetic-token", str(error.exception))

    def test_invalid_webhook_urls_are_rejected(self):
        for url in ("http://discord.com/api/webhooks/123/token", "https://evil.invalid/api/webhooks/123/token",
                    "https://discord.com@evil.invalid/api/webhooks/123/token", "https://discord.com/api/webhooks/id/token",
                    "https://discord.com/api/webhooks/123/token#fragment", "https://discord.com/api/webhooks/123/token?thread_id=a"):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                load_config({**self.env, "DISCORD_WEBHOOK": url})

    def test_thread_id_is_retained_but_wait_is_not_duplicated(self):
        config = load_config({**self.env, "DISCORD_WEBHOOK": self.env["DISCORD_WEBHOOK"] + "?wait=false&thread_id=12345"})
        self.assertTrue(config["hook_url"].endswith("?thread_id=12345"))

    def test_invalid_numbers_and_semester_are_rejected(self):
        for key, value in (("CONNECT_TIMEOUT", "nan"), ("READ_TIMEOUT", "0"), ("CHECK_INTERVAL", "-1"),
                           ("CHECK_INTERVAL", "inf"), ("SEMESTER_ID", "None"), ("AGENT_NAME", "bad\nheader")):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_config({**self.env, key: value})

    def test_dotenv_does_not_override_existing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text("DUALIS_USER=s654321\nDUALIS_PASSWD=file-secret\n")
            with patch("config.PROJECT_DIR", Path(directory)), patch.dict(os.environ, self.env, clear=True):
                config = load_config()
            self.assertEqual(config["user"], "s123456")
            self.assertEqual(config["passwd"], self.env["DUALIS_PASSWD"])

    def test_dotenv_password_variable_syntax_remains_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text("DUALIS_PASSWD='valid${SECRET}+&%'\n")
            env = {key: value for key, value in self.env.items() if key != "DUALIS_PASSWD"}
            with patch("config.PROJECT_DIR", Path(directory)), patch.dict(os.environ, {**env, "SECRET": "wrong"}, clear=True):
                config = load_config()
            self.assertEqual(config["passwd"], "valid${SECRET}+&%")


if __name__ == "__main__":
    unittest.main()
