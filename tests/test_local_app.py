import os
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import setting
from threads_bot.local_app import create_app
from threads_bot.threads_client import PublicationUnknown


class FakeThreads:
    unknown = False
    calls = []

    def __init__(self, credentials):
        self.credentials = credentials

    def create_container(self, text):
        self.calls.append((self.credentials.user_id, text))
        return "111"

    def wait_until_ready(self, container_id):
        pass

    def publish(self, container_id):
        if self.unknown:
            raise PublicationUnknown("結果不明")
        return "222"

    def close(self):
        pass


class LocalAppTest(unittest.TestCase):
    def setUp(self):
        self.password = patch.object(setting, "LOGIN_PASSWORD", "test-local-password")
        self.password.start()
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "THREADS_LOCAL_DB": str(Path(self.temp.name) / "studio.sqlite3"),
            "THREADS_APP_ID": "111", "THREADS_APP_SECRET": "secret",
            "META_KEY_GROUPS_JSON": json.dumps({"fallback": "default", "groups": [{
                "id": "default", "app_id_env": "THREADS_APP_ID", "app_secret_env": "THREADS_APP_SECRET",
                "user_id_env": "THREADS_USER_ID", "access_token_env": "THREADS_ACCESS_TOKEN",
                "access": "signed_in", "allowed_ips": ["*"], "login_password_env": "LOGIN_PASSWORD"}]}),
            "GEMINI_PRO_API_KEY": "pro", "GEMINI_API_KEY": "flash"})
        self.env.start()
        self.app = create_app()
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765",
                                 client=("127.0.0.1", 50000))
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.post("/api/session", headers={"Origin": "http://127.0.0.1:8765"},
                          json={"password": "test-local-password"}).status_code, 200)
        self.csrf = self.client.get("/api/me").json()["csrf_token"]
        self.headers = {"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": self.csrf}
        FakeThreads.unknown = False
        FakeThreads.calls = []

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()
        self.password.stop()

    def add_account(self):
        profile = {"id": "123", "name": "本人", "username": "self"}
        expiry = datetime.now(timezone.utc) + timedelta(days=30)
        with patch("threads_bot.local_app.prepare_token", return_value=(profile, "token", expiry)):
            result = self.client.post("/api/accounts", headers=self.headers,
                json={"user_id": "123", "access_token": "long-token"})
        self.assertEqual(result.status_code, 200, result.text)

    def test_local_post_and_unknown_block(self):
        self.add_account()
        pro = MagicMock()
        flash = MagicMock()
        pro.interactions.create.return_value.output_text = "投稿本文"
        with patch("threads_bot.local_app.create_clients", return_value=(pro, flash)), patch(
                "threads_bot.local_app.ThreadsClient", FakeThreads):
            response = self.client.post("/api/accounts/123/post-now", headers=self.headers,
                                        json={"prompt": "入力したプロンプト"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "published")
            self.assertEqual(FakeThreads.calls, [("123", "投稿本文")])
            self.assertEqual(self.client.get("/api/accounts/123").json()["latest_run"]["post_id"], "222")
            FakeThreads.unknown = True
            response = self.client.post("/api/accounts/123/post-now", headers=self.headers,
                                        json={"prompt": "次のプロンプト"})
            self.assertEqual(response.json()["status"], "unknown")
            self.assertEqual(self.client.post("/api/accounts/123/post-now", headers=self.headers,
                         json={"prompt": "重複"}).status_code, 409)

    def test_imports_only_first_account_from_env(self):
        profile = {"id": "123", "name": "本人", "username": "self"}
        expiry = datetime.now(timezone.utc) + timedelta(days=30)
        values = {"THREADS_USER_ID": "123", "THREADS_ACCESS_TOKEN": "long-token",
                  "THREADS_APP_ID": "111", "THREADS_APP_SECRET": "secret"}
        with patch.dict(os.environ, values), patch("threads_bot.local_app.dotenv_values", return_value=values), patch(
                "threads_bot.local_app.prepare_token", return_value=(profile, "verified-token", expiry)) as prepare:
            self.assertEqual(len(self.client.get("/api/accounts").json()), 1)
            self.assertEqual(len(self.client.get("/api/accounts").json()), 1)
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(self.client.delete("/api/accounts/123/connection", headers=self.headers).status_code, 200)
            self.assertEqual(self.client.get("/api/accounts").json(), [])
            self.assertEqual(prepare.call_count, 1)

    def test_loopback_and_csrf(self):
        self.assertEqual(self.client.post("/api/accounts", json={"user_id": "123",
                         "access_token": "long-token"}).status_code, 403)
        self.assertEqual(self.client.get("/api/me", headers={"Host": "other.example"}).status_code, 403)
        remote = TestClient(self.app, base_url="http://127.0.0.1:8765",
                            client=("192.0.2.1", 50000))
        try:
            self.assertEqual(remote.get("/api/me").status_code, 403)
        finally:
            remote.close()

    def test_login_rejects_wrong_password_and_tampered_cookie(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/accounts").status_code, 401)
        self.assertEqual(self.client.post("/api/session", json={"password": "test-local-password"}).status_code, 403)
        self.assertEqual(self.client.post("/api/session", headers={"Origin": "http://127.0.0.1:8765"},
                         json={"password": "wrong"}).status_code, 401)
        self.client.cookies.set("orbit_session", "forged")
        self.assertEqual(self.client.get("/api/accounts").status_code, 401)
        self.assertEqual(self.client.post("/api/session", headers={"Origin": "http://127.0.0.1:8765"},
                         json={"password": "test-local-password"}).status_code, 200)
        headers = {"Origin": "http://127.0.0.1:8765",
                   "X-CSRF-Token": self.client.get("/api/me").json()["csrf_token"]}
        self.assertEqual(self.client.post("/api/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
