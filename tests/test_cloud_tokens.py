import unittest
from datetime import datetime, timedelta, timezone

import httpx
from google.api_core.exceptions import NotFound

from threads_bot.cloud_tokens import ConnectionError, TokenVault, inspect_token


class CloudTokenTest(unittest.TestCase):
    def _client(self, user_id="123", scopes=None, debug_user_id=None):
        expiry = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
        def handler(request):
            if request.url.path.endswith('/me'):
                return httpx.Response(200, json={"id": user_id, "username": "me", "name": "本人"})
            if request.url.path.endswith('/oauth/access_token'):
                self.assertEqual(request.url.params["grant_type"], "client_credentials")
                return httpx.Response(200, json={"access_token": "TH|app|secret"})
            if request.url.path.endswith('/debug_token'):
                self.assertEqual(request.headers["Authorization"], "Bearer TH|app|secret")
                return httpx.Response(200, json={"data": {"is_valid": True,
                    "user_id": debug_user_id or user_id,
                    "scopes": scopes or ["threads_basic", "threads_content_publish"],
                    "expires_at": expiry}})
            return httpx.Response(404)
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_verified_identity_scope_and_expiry(self):
        with self._client() as client:
            profile, expiry = inspect_token("token", "123", app_id="app", app_secret="secret", client=client)
        self.assertEqual(profile["username"], "me")
        self.assertGreater(expiry, datetime.now(timezone.utc))

    def test_mismatched_identity_or_scope_is_rejected(self):
        with self._client(user_id="999") as client, self.assertRaises(ConnectionError):
            inspect_token("token", "123", app_id="app", app_secret="secret", client=client)
        with self._client(scopes=["threads_basic"]) as client, self.assertRaises(ConnectionError):
            inspect_token("token", "123", app_id="app", app_secret="secret", client=client)
        with self._client(debug_user_id="999") as client, self.assertRaises(ConnectionError):
            inspect_token("token", "123", app_id="app", app_secret="secret", client=client)

    def test_token_secret_must_be_provisioned_before_connection(self):
        class MissingSecret:
            def add_secret_version(self, request):
                raise NotFound("missing")

        vault = TokenVault(client=MissingSecret(), project="test-project")
        with self.assertRaisesRegex(ConnectionError, "未準備"):
            vault.put("123", "token")


if __name__ == "__main__":
    unittest.main()
