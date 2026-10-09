import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from threads_bot.cli import main
from threads_bot.run_store import RunStore


class CliTest(unittest.TestCase):
    def test_generate_requires_no_threads_credentials(self):
        out = io.StringIO()
        pro = MagicMock()
        flash = MagicMock()
        pro.interactions.create.return_value.output_text = "本文だけ"
        environment = {
            "GEMINI_PRO_API_KEY": "pro",
            "GEMINI_API_KEY": "flash",
            "THREADS_ACCESS_TOKEN": "",
            "THREADS_USER_ID": "",
        }
        with patch.dict(os.environ, environment), patch("threads_bot.cli.load_environment"), patch(
            "threads_bot.cli.create_clients", return_value=(pro, flash)
        ), redirect_stdout(out):
            code = main(["generate", "--prompt", "散歩について"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "本文だけ\n")
        flash.interactions.create.assert_not_called()

    def test_dry_run_avoids_threads_and_store(self):
        pro = MagicMock()
        flash = MagicMock()
        pro.interactions.create.return_value.output_text = "本文"
        environment = {
            "GEMINI_PRO_API_KEY": "pro",
            "GEMINI_API_KEY": "flash",
            "THREADS_ACCESS_TOKEN": "",
            "THREADS_USER_ID": "",
        }
        with patch.dict(os.environ, environment), patch("threads_bot.cli.load_environment"), patch(
            "threads_bot.cli.create_clients", return_value=(pro, flash)
        ), patch("threads_bot.cli.ThreadsClient") as threads, patch(
            "threads_bot.cli.RunStore"
        ) as store, redirect_stdout(io.StringIO()):
            code = main(["post", "--prompt", "指示", "--dry-run"])
        self.assertEqual(code, 0)
        threads.assert_not_called()
        store.assert_not_called()

    def test_post_fails_on_missing_threads_token_before_gemini_client(self):
        error = io.StringIO()
        environment = {
            "GEMINI_PRO_API_KEY": "pro",
            "GEMINI_API_KEY": "flash",
            "THREADS_ACCESS_TOKEN": "",
            "THREADS_USER_ID": "123",
        }
        with patch.dict(os.environ, environment), patch("threads_bot.cli.load_environment"), patch(
            "threads_bot.cli.create_clients"
        ) as clients, redirect_stderr(error):
            code = main(["post", "--prompt", "指示"])
        self.assertEqual(code, 2)
        self.assertIn("THREADS_ACCESS_TOKEN", error.getvalue())
        clients.assert_not_called()

    def test_status_reads_persisted_run_without_api_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            original = RunStore(Path(directory) / "runs.sqlite3")
            run_id = original.start()
            original.update(run_id, "published", text="本文", post_id="222")
            original.close()
            output = io.StringIO()
            with patch("threads_bot.cli.load_environment"), patch(
                "threads_bot.cli.RunStore", side_effect=lambda: RunStore(Path(directory) / "runs.sqlite3")
            ), redirect_stdout(output):
                code = main(["status", "--run-id", run_id])
            self.assertEqual(code, 0)
            self.assertIn('"post_id": "222"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
