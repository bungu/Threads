import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import setting
from threads_bot import cloud_app, local_app
from threads_bot.group_ip import DENIED_MESSAGE
from threads_bot.meta_groups import GroupConfigurationError, load_groups
from test_cloud_app import FakeLoginLimiter


POLICY = {"fallback": "first", "groups": [
    {"id": "first", "label": "1つ目", "access": "signed_in", "app_id": "111",
     "app_secret": "first-private-secret", "allowed_ips": ["127.0.0.1"], "login_password": "first-password"},
    {"id": "second", "label": "2つ目", "access": "signed_in", "app_id": "222",
     "app_secret": "second-private-secret", "allowed_ips": ["*"], "login_password": "second-password"},
    {"id": "viewer", "label": "閲覧用", "access": "view", "allowed_ips": ["*"],
     "login_password": "view-password", "view_groups": ["first", "second"]}]}


class GroupAuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"META_KEY_GROUPS_JSON": json.dumps(POLICY),
            "LOGIN_PASSWORD": "first-password", "SESSION_COOKIE_SECRET": "s" * 32,
            "PUBLIC_ORIGIN": "https://example.test", "GEO_ENFORCE": "0", "TRUSTED_XFF_HOPS": "1",
            "THREADS_LOCAL_DB": str(Path(self.temp.name) / "studio.sqlite3")})
        self.env.start(); self.addCleanup(self.env.stop)
        for target, value in (("threads_bot.local_app.dotenv_values", {}),
                              ("threads_bot.local_app.load_environment", None)):
            mock = patch(target, return_value=value)
            mock.start(); self.addCleanup(mock.stop)
        password = patch.object(setting, "LOGIN_PASSWORD", "first-password")
        password.start(); self.addCleanup(password.stop)
        self.apps = {"cloud": cloud_app.create_app("web"), "local": local_app.create_app()}
        self.apps["cloud"].dependency_overrides[cloud_app.get_login_limiter] = lambda: FakeLoginLimiter()
        self.apps["local"].dependency_overrides[local_app.get_login_limiter] = lambda: FakeLoginLimiter()
        self.cloud_store = Mock()
        self.cloud_store.accounts.return_value = [{"id": "123", "name": "Cloud本人", "handle": "cloud",
                                                 "connection": "connected", "enabled": False, "last_error": None}]
        self.cloud_store.account_view.return_value = {"account": {"id": "123", "name": "Cloud本人",
            "handle": "cloud", "connection": "connected"}, "settings": {"prompt": "cloud prompt",
            "days": [0], "times": ["09:00"], "enabled": False, "version": 1},
            "next_at": None, "runtime_enabled": False, "latest_run": None, "last_generated_text": None}
        self.apps["cloud"].dependency_overrides[cloud_app._store] = lambda: self.cloud_store
        cloud_store = patch.object(cloud_app, "CloudStore", return_value=self.cloud_store)
        cloud_store.start(); self.addCleanup(cloud_store.stop)
        self.clients = {}
        for mode, url in (("cloud", "https://example.test"), ("local", "http://127.0.0.1:8765")):
            client = TestClient(self.apps[mode], base_url=url, client=("127.0.0.1", 50000))
            client.headers["X-Forwarded-For"] = "127.0.0.1"
            self.addCleanup(client.close)
            self.clients[mode] = client

    def login(self, mode, password):
        client = self.clients[mode]
        client.cookies.clear()
        url = "https://example.test" if mode == "cloud" else "http://127.0.0.1:8765"
        result = client.post("/api/session", headers={"Origin": url}, json={"password": password})
        self.assertEqual(result.status_code, 200, result.text)
        me = client.get("/api/me")
        self.assertEqual(me.status_code, 200, me.text)
        client.headers.update({"Origin": url, "X-CSRF-Token": me.json()["csrf_token"]})
        return me.json()

    def test_password_selects_exactly_one_group_and_headers_cannot_switch(self):
        for mode, client in self.clients.items():
            with self.subTest(mode=mode):
                self.assertEqual(self.login(mode, "first-password")["meta_group"]["id"], "first")
                self.assertEqual(client.get("/api/me", headers={"X-Meta-Group": "second"}).json()["meta_group"]["id"], "first")
                self.assertEqual(client.get("/api/me", headers={"X-Device-Session": "forged"}).json()["meta_group"]["id"], "first")
                first_cookie = client.cookies.get(cloud_app.COOKIE if mode == "cloud" else local_app.COOKIE)
                self.assertEqual(self.login(mode, "second-password")["meta_group"]["id"], "second")
                client.cookies.set(cloud_app.COOKIE if mode == "cloud" else local_app.COOKIE, first_cookie)
                self.assertEqual(client.get("/api/me").json()["meta_group"]["id"], "first")
                self.assertEqual(self.login(mode, "view-password")["read_only"], True)
                self.assertEqual(client.get("/api/me").json()["meta_group"]["id"], "viewer")

    def test_ip_and_group_are_both_required(self):
        for mode, client in self.clients.items():
            self.login(mode, "first-password")
            if mode == "cloud":
                denied = client.get("/api/accounts", headers={"X-Forwarded-For": "127.0.0.1, 203.0.113.5"})
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(denied.json()["detail"], DENIED_MESSAGE)
                self.assertEqual(client.get("/api/accounts", headers={"X-Forwarded-For": "8.8.8.8, 127.0.0.1"}).status_code, 200)
            else:
                policy = copy.deepcopy(POLICY)
                policy["groups"][0]["allowed_ips"] = ["203.0.113.5"]
                with patch.dict(os.environ, {"META_KEY_GROUPS_JSON": json.dumps(policy)}):
                    denied = client.get("/api/accounts", headers={"X-Forwarded-For": "203.0.113.5"})
                    self.assertEqual(denied.status_code, 403)
                    self.assertEqual(denied.json()["detail"], DENIED_MESSAGE)
            self.assertEqual(self.login(mode, "second-password")["meta_group"]["id"], "second")
            self.assertEqual(client.get("/api/accounts").status_code, 200)

    def test_view_password_shows_accounts_but_mutations_are_denied(self):
        profile = {"id": "123", "name": "Local本人", "username": "local"}
        expiry = datetime.now(timezone.utc) + timedelta(days=30)
        self.login("local", "first-password")
        with patch.object(local_app, "prepare_token", return_value=(profile, "local-private-token", expiry)):
            response = self.clients["local"].post("/api/accounts", json={"user_id": "123",
                "access_token": "short-token"})
        self.assertEqual(response.status_code, 200, response.text)
        operations = [("POST", "/api/accounts", {"user_id": "123", "access_token": "short-token"}),
            ("PUT", "/api/accounts/first:123/settings", {"prompt": "bad", "days": [0],
                "times": ["09:00"], "enabled": True, "version": 1}),
            ("POST", "/api/accounts/first:123/post-now", {"prompt": "bad"}),
            ("DELETE", "/api/accounts/first:123/connection", None),
            ("PUT", "/api/runtime", {"enabled": True})]
        for mode, client in self.clients.items():
            with self.subTest(mode=mode):
                self.login(mode, "view-password")
                listing = client.get("/api/accounts")
                self.assertEqual(listing.status_code, 200, listing.text)
                self.assertTrue(listing.json())
                self.assertTrue(all(":" in item["id"] for item in listing.json()))
                self.assertNotIn("private-token", listing.text)
                account_id = listing.json()[0]["id"]
                view = client.get("/api/accounts/" + account_id)
                self.assertEqual(view.status_code, 200, view.text)
                self.assertEqual(view.json()["account"]["id"], account_id)
                self.assertNotIn("private-token", view.text)
                self.assertEqual(client.get("/api/accounts/unlisted:123").status_code, 404)
                self.assertEqual(client.get("/api/runtime").json()["can_enable"], False)
                for method, path, body in operations:
                    response = client.request(method, path, json=body)
                    self.assertEqual(response.status_code, 403, (mode, path, response.text))
                self.assertEqual(client.post("/api/logout").status_code, 200)
                self.assertEqual(client.get("/api/accounts").status_code, 401)

    def test_password_rotation_revokes_only_that_group_and_bad_policy_closes(self):
        for mode, client in self.clients.items():
            self.login(mode, "second-password")
            policy = copy.deepcopy(POLICY)
            policy["groups"][1]["login_password"] = "changed-second-password"
            with patch.dict(os.environ, {"META_KEY_GROUPS_JSON": json.dumps(policy)}):
                self.assertEqual(client.get("/api/me").status_code, 401)
                self.assertEqual(self.login(mode, "changed-second-password")["meta_group"]["id"], "second")
            self.login(mode, "first-password")
            duplicate = copy.deepcopy(POLICY)
            duplicate["groups"][1]["login_password"] = "first-password"
            with patch.dict(os.environ, {"META_KEY_GROUPS_JSON": json.dumps(duplicate)}):
                self.assertEqual(client.get("/api/me").status_code, 503)
            self.assertEqual(client.get("/api/me").status_code, 200)
        missing = copy.deepcopy(POLICY)
        del missing["groups"][0]["login_password"]
        with patch.dict(os.environ, {"META_KEY_GROUPS_JSON": json.dumps(missing)}):
            with self.assertRaises(GroupConfigurationError):
                load_groups()


if __name__ == "__main__":
    unittest.main()
