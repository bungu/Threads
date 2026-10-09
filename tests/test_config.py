import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from threads_bot.cli import _prompt
from threads_bot.config import ConfigurationError, gemini_credentials, threads_credentials


class ConfigTest(unittest.TestCase):
    def test_gemini_requires_both_keys(self):
        with patch.dict(os.environ, {"GEMINI_PRO_API_KEY": "pro", "GEMINI_API_KEY": ""}):
            with self.assertRaises(ConfigurationError):
                gemini_credentials()
        with patch.dict(os.environ, {"GEMINI_PRO_API_KEY": "pro", "GEMINI_API_KEY": "flash"}):
            self.assertEqual(gemini_credentials().flash_key, "flash")

    def test_threads_user_id_is_numeric(self):
        with patch.dict(os.environ, {"THREADS_USER_ID": "me", "THREADS_ACCESS_TOKEN": "secret"}):
            with self.assertRaises(ConfigurationError):
                threads_credentials()
        with patch.dict(os.environ, {"THREADS_USER_ID": "123", "THREADS_ACCESS_TOKEN": "secret"}):
            self.assertEqual(threads_credentials().user_id, "123")

    def test_prompt_file_and_empty_prompt(self):
        from argparse import Namespace

        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "prompt.txt"
            file.write_text(" 今日は散歩の話をして \n", encoding="utf-8")
            self.assertEqual(_prompt(Namespace(prompt=None, prompt_file=file)), "今日は散歩の話をして")
            file.write_text("   ", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                _prompt(Namespace(prompt=None, prompt_file=file))


if __name__ == "__main__":
    unittest.main()

