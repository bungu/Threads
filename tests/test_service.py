import tempfile
import unittest
from pathlib import Path

from threads_bot.run_store import RunStore, RunStoreError
from threads_bot.service import post_once
from threads_bot.threads_client import PublicationUnknown, ThreadsError


class FakeThreads:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def create_container(self, text):
        self.calls.append(("create", text))
        if self.fail == "create":
            raise ThreadsError("create failed")
        return "111"

    def wait_until_ready(self, container_id):
        self.calls.append(("wait", container_id))
        if self.fail == "wait":
            raise ThreadsError("wait failed")

    def publish(self, container_id):
        self.calls.append(("publish", container_id))
        if self.fail == "publish":
            raise PublicationUnknown("unknown")
        return "222"


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.temp.name) / "runs.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _post(self, api, generator=lambda p, f, prompt: "本文"):
        return post_once("指示", object(), object(), api, self.store, generator)

    def test_success_posts_once_and_stores_id(self):
        api = FakeThreads()
        run_id, text, post_id = self._post(api)
        self.assertEqual((text, post_id), ("本文", "222"))
        self.assertEqual(api.calls, [("create", "本文"), ("wait", "111"), ("publish", "111")])
        self.assertEqual(self.store.get(run_id)["status"], "published")

    def test_generation_failure_never_calls_threads(self):
        api = FakeThreads()

        def failing_generator(*args):
            raise RuntimeError("gemini failure")

        with self.assertRaises(RuntimeError):
            self._post(api, failing_generator)
        self.assertEqual(api.calls, [])
        self.assertEqual(self.store.get()["status"], "failed")

    def test_create_failure_never_publishes(self):
        api = FakeThreads("create")
        with self.assertRaises(ThreadsError):
            self._post(api)
        self.assertEqual(api.calls, [("create", "本文")])
        self.assertEqual(self.store.get()["status"], "failed")

    def test_unknown_blocks_second_post(self):
        api = FakeThreads("publish")
        with self.assertRaises(PublicationUnknown):
            self._post(api)
        self.assertEqual(self.store.get()["status"], "unknown")
        with self.assertRaises(RunStoreError):
            self._post(FakeThreads())


if __name__ == "__main__":
    unittest.main()

