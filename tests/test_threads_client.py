import json
import unittest

import httpx

from threads_bot.config import ThreadsCredentials
from threads_bot.threads_client import PublicationUnknown, ThreadsClient, ThreadsError


class ThreadsClientTest(unittest.TestCase):
    def _client(self, handler, sleep=lambda _: None):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        return ThreadsClient(ThreadsCredentials("secret-token", "123"), http, sleep)

    def test_create_wait_publish_flow(self):
        requests = []
        statuses = iter(("IN_PROGRESS", "FINISHED"))

        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer secret-token")
            if request.url.path.endswith("/threads"):
                self.assertEqual(request.method, "POST")
                self.assertIn(b"media_type=TEXT", request.content)
                self.assertIn(b"text=", request.content)
                self.assertNotIn(b"auto_publish_text", request.content)
                return httpx.Response(200, json={"id": "111"})
            if request.method == "GET":
                self.assertEqual(request.url.params["fields"], "id,status,error_message")
                return httpx.Response(200, json={"status": next(statuses)})
            self.assertEqual(request.url.path, "/v1.0/123/threads_publish")
            self.assertIn(b"creation_id=111", request.content)
            return httpx.Response(200, json={"id": "222"})

        api = self._client(handler)
        self.assertEqual(api.create_container("散歩の話"), "111")
        api.wait_until_ready("111")
        self.assertEqual(api.publish("111"), "222")
        self.assertEqual([r.method for r in requests], ["POST", "GET", "GET", "POST"])

    def test_create_http_error_does_not_expose_token(self):
        api = self._client(lambda request: httpx.Response(401, json={"error": {"code": 190, "message": "secret-token"}}))
        with self.assertRaises(ThreadsError) as caught:
            api.create_container("本文")
        self.assertIn("HTTP 401", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))

    def test_status_error_stops_before_publish(self):
        api = self._client(lambda request: httpx.Response(200, json={"status": "ERROR"}))
        with self.assertRaises(ThreadsError):
            api.wait_until_ready("111")

    def test_status_timeout_is_bounded(self):
        sleeps = []
        api = self._client(lambda request: httpx.Response(200, json={"status": "IN_PROGRESS"}), sleeps.append)
        with self.assertRaises(ThreadsError):
            api.wait_until_ready("111")
        self.assertEqual(len(sleeps), 5)

    def test_publish_response_missing_id_is_unknown(self):
        api = self._client(lambda request: httpx.Response(200, json={}))
        with self.assertRaises(PublicationUnknown):
            api.publish("111")

    def test_publish_transport_error_is_unknown(self):
        def handler(request):
            raise httpx.ReadTimeout("timeout", request=request)

        api = self._client(handler)
        with self.assertRaises(PublicationUnknown):
            api.publish("111")

    def test_publish_definite_4xx_error(self):
        api = self._client(lambda request: httpx.Response(403, json={"error": {"code": 10}}))
        with self.assertRaises(ThreadsError) as caught:
            api.publish("111")
        self.assertNotIsInstance(caught.exception, PublicationUnknown)


if __name__ == "__main__":
    unittest.main()

