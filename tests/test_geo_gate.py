import os
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient

from threads_bot.client_ip import client_ip
from threads_bot.geo_gate import GeoGate
from threads_bot import cloud_app


class GeoGateTest(unittest.TestCase):
    def test_country_lookup_and_spoofed_xff_prefix(self):
        gate = GeoGate()
        self.assertEqual(gate.country("133.0.0.1"), "JP")
        self.assertEqual(gate.country("8.8.8.8"), "US")
        self.assertIsNone(gate.country("203.0.113.1"))
        request = SimpleNamespace(headers={"x-forwarded-for": "133.0.0.1, 8.8.8.8"})
        with patch.dict(os.environ, {"TRUSTED_XFF_HOPS": "1"}):
            self.assertEqual(client_ip(request), "8.8.8.8")


class CloudGeoTest(unittest.TestCase):
    def test_foreign_api_is_rejected_and_japan_is_allowed(self):
        groups = {"fallback": "shared", "groups": [{"id": "shared", "app_id": "222",
                   "app_secret": "secret", "access": "signed_in", "allowed_ips": ["*"], "login_password": "test-cloud-password"}]}
        with patch.dict(os.environ, {"GEO_ENFORCE": "1", "TRUSTED_XFF_HOPS": "1",
                                  "PUBLIC_ORIGIN": "https://example.test",
                                  "LOGIN_PASSWORD": "test-cloud-password",
                                  "SESSION_COOKIE_SECRET": "x" * 32,
                                  "META_KEY_GROUPS_JSON": json.dumps(groups)}):
            with TestClient(cloud_app.create_app("web"), base_url="https://example.test") as client:
                foreign = client.get("/api/me", headers={"X-Forwarded-For": "133.0.0.1, 8.8.8.8"})
                self.assertEqual(foreign.status_code, 403)
                self.assertIn("日本国外", foreign.json()["detail"])
                self.assertEqual(client.get("/api/me", headers={"X-Forwarded-For": "133.0.0.1"}).status_code, 401)
                self.assertEqual(client.get("/api/me", headers={"X-Forwarded-For": "203.0.113.1"}).status_code, 401)
                page = client.get("/", headers={"X-Forwarded-For": "8.8.8.8"})
                self.assertEqual(page.status_code, 200)
                self.assertIn("IP Geolocation by DB-IP", page.text)


if __name__ == "__main__":
    unittest.main()
