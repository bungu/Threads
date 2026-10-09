import os
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import setting
from threads_bot import cloud_app


class FakeStore:
    def accounts(self, owner):
        return [{"id": "123", "name": "本人", "handle": "self", "enabled": False,
                 "connection": "connected", "token_expires_at": None, "last_error": None}]

    def account_view(self, owner, account_id):
        return {"account": self.accounts(owner)[0], "settings": {"prompt": "",
                "days": [], "times": [], "enabled": False, "version": 1},
                "next_at": None, "latest_run": None, "last_generated_text": None}

    def save_settings(self, owner, account_id, value, version):
        if version != 1:
            raise cloud_app.ConflictError("競合")
        return {"version": 2}

    def account(self, owner, account_id):
        return {"id": account_id, "owner": owner, "connection": "connected", "token_ref": "ref"}

    def settings(self, owner, account_id):
        return {"prompt": "", "days": [], "times": [], "enabled": False, "version": 1}


class FakeLoginLimiter:
    def __init__(self):
        self.attempts = 0

    def reserve(self):
        self.attempts += 1
        return 900 if self.attempts > 5 else 0

    def reset(self):
        self.attempts = 0


class CloudAppTest(unittest.TestCase):
    def setUp(self):
        self.password = patch.object(setting, "LOGIN_PASSWORD", "different-local-password")
        self.password.start()
        self.env = patch.dict(os.environ, {"PUBLIC_ORIGIN": "https://example.test",
                                        "LOGIN_PASSWORD": "test-cloud-password",
                                        "SESSION_COOKIE_SECRET": "x" * 32,
                                        "META_KEY_GROUPS_JSON": json.dumps({"fallback":"shared","groups":[
                                            {"id":"owner","app_id":"111","app_secret":"owner-secret","access":"signed_in","allowed_ips":["*"],"login_password_env":"LOGIN_PASSWORD","login_password":"owner-test-password"},
                                            {"id":"shared","app_id":"222","app_secret":"shared-secret","access":"signed_in","allowed_ips":["*"],"login_password_env":"LOGIN_PASSWORD"}]})})
        self.env.start()
        self.app = cloud_app.create_app("web")
        self.app.dependency_overrides[cloud_app._store] = lambda: FakeStore()
        self.limiter = FakeLoginLimiter()
        self.app.dependency_overrides[cloud_app.get_login_limiter] = lambda: self.limiter
        self.client = TestClient(self.app, base_url="https://example.test")

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.password.stop()

    def test_owner_and_csrf(self):
        self.assertEqual(self.client.get("/api/accounts").status_code, 401)
        self.assertEqual(self.client.post("/api/session", headers={"Origin": "https://example.test"},
                         json={"password": "wrong"}).status_code, 401)
        self.assertEqual(self.client.post("/api/session", json={"password": "test-cloud-password"}).status_code, 403)
        self.assertEqual(self.client.post("/api/session", headers={"Origin": "https://example.test"},
                         json={"password": "test-cloud-password"}).status_code, 200)
        result = self.client.get("/api/accounts")
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("access_token", result.text)
        body = {"prompt": "abc", "days": [0], "times": ["09:00"],
                "enabled": False, "version": 1}
        self.assertEqual(self.client.put("/api/accounts/123/settings", json=body).status_code, 403)
        headers = {"Origin": "https://example.test", "X-CSRF-Token": self.client.get("/api/me").json()["csrf_token"]}
        self.assertEqual(self.client.put("/api/accounts/123/settings", json=body,
                                         headers=headers).status_code, 200)
        body["version"] = 0
        self.assertEqual(self.client.put("/api/accounts/123/settings", json=body,
                                         headers=headers).status_code, 409)
        self.assertEqual(self.client.post("/api/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/accounts").status_code, 401)

    def test_worker_route_is_separate_and_requires_oidc(self):
        worker = cloud_app.create_app("worker")
        worker.dependency_overrides[cloud_app._store] = lambda: FakeStore()
        worker.dependency_overrides[cloud_app._vault] = lambda: object()
        with TestClient(worker) as client:
            self.assertEqual(client.post("/tasks/tick").status_code, 401)
            self.assertEqual(client.get("/api/accounts").status_code, 404)

    def test_password_change_invalidates_cloud_session(self):
        headers = {"Origin": "https://example.test"}
        self.assertEqual(self.client.post("/api/session", headers=headers,
                         json={"password": "test-cloud-password"}).status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 200)
        with patch.dict(os.environ, {"LOGIN_PASSWORD": "new-cloud-password"}):
            self.assertEqual(self.client.get("/api/me").status_code, 401)
            self.assertEqual(self.client.post("/api/session", headers=headers,
                             json={"password": "test-cloud-password"}).status_code, 401)

    def test_login_throttling_cookie_and_script_policy(self):
        headers = {"Origin": "https://example.test"}
        for _ in range(5):
            self.assertEqual(self.client.post("/api/session", headers=headers,
                             json={"password": "wrong"}).status_code, 401)
        blocked = self.client.post("/api/session", headers=headers,
                                   json={"password": "test-cloud-password"})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["Retry-After"], "900")
        self.limiter.reset()
        login = self.client.post("/api/session", headers=headers,
                                 json={"password": "test-cloud-password"})
        self.assertEqual(login.status_code, 200)
        self.assertIn("__Host-orbit_session=", login.headers["set-cookie"])
        self.assertIn("Secure", login.headers["set-cookie"])
        page = self.client.get("/")
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        self.assertIn('src="/lucide.js"', page.text)
        self.assertEqual(self.client.get("/lucide.js").status_code, 200)

    def test_manual_post_uses_current_prompt_while_schedule_is_off(self):
        self.app.dependency_overrides[cloud_app._vault] = lambda: object()
        self.client.post("/api/session", headers={"Origin": "https://example.test"},
                         json={"password": "test-cloud-password"})
        headers = {"Origin": "https://example.test", "X-CSRF-Token": self.client.get("/api/me").json()["csrf_token"]}
        with patch.object(cloud_app, "_process", return_value="published") as process:
            response = self.client.post("/api/accounts/123/post-now", headers=headers,
                                        json={"prompt": "今の入力"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "published")
        self.assertEqual(process.call_args.kwargs["manual_prompt"], "今の入力")


if __name__ == "__main__":
    unittest.main()
