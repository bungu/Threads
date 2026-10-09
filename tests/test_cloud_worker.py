import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from threads_bot import cloud_worker
from threads_bot.cloud_store import ConflictError
from threads_bot.threads_client import PublicationUnknown


class FakeStore:
    def __init__(self, conflict=False):
        self.transitions = []
        self.updates = []
        self.claimed = False
        self.conflict = conflict

    def claim(self, account, settings, slot, now):
        if self.claimed:
            return None
        self.claimed = True
        return {"id": "run", "account_id": account["id"], "prompt": "指示",
                "settings_version": 1, "lease_owner": "lease"}

    def transition(self, run, status, **kwargs):
        if status == "prepared" and self.conflict:
            raise ConflictError("changed")
        self.transitions.append(status)

    def update_account(self, account_id, **kwargs):
        self.updates.append(kwargs)


class FakeVault:
    def get(self, ref):
        return "token"


class FakeGemini:
    def close(self):
        pass


class FakeThreads:
    def __init__(self, *args):
        self.calls = []

    def create_container(self, text):
        self.calls.append("create")
        return "111"

    def wait_until_ready(self, container_id):
        self.calls.append("wait")

    def publish(self, container_id):
        self.calls.append("publish")
        raise PublicationUnknown("unknown")

    def close(self):
        pass


class CloudWorkerTest(unittest.TestCase):
    def setUp(self):
        self.account = {"id": "123", "owner": "me", "token_ref": "ref"}
        self.settings = {"prompt": "指示", "version": 1}
        self.now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        self.patches = [
            patch.object(cloud_worker, "gemini_credentials", return_value=object()),
            patch.object(cloud_worker, "create_clients", return_value=(FakeGemini(), FakeGemini())),
            patch.object(cloud_worker, "generate_post", return_value="本文"),
            patch.object(cloud_worker, "ThreadsClient", FakeThreads),
        ]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()

    def test_unknown_publication_is_not_retried(self):
        store = FakeStore()
        result = cloud_worker._process(store, FakeVault(), self.account,
                                       self.settings, self.now, self.now)
        self.assertEqual(result, "unknown")
        self.assertEqual(store.transitions, ["prepared", "container_created", "publishing", "unknown"])
        self.assertTrue(any(row.get("connection") == "review_required" for row in store.updates))
        self.assertEqual(cloud_worker._process(store, FakeVault(), self.account,
                                               self.settings, self.now, self.now), "duplicate_or_busy")

    def test_settings_change_cancels_before_threads(self):
        store = FakeStore(conflict=True)
        result = cloud_worker._process(store, FakeVault(), self.account,
                                       self.settings, self.now, self.now)
        self.assertEqual(result, "cancelled")
        self.assertEqual(store.transitions, ["cancelled"])

    def test_activation_does_not_publish_earlier_slot(self):
        from datetime import timedelta

        class DueStore:
            def runtime_enabled(self): return True
            def last_tick_at(self): return self_now - timedelta(seconds=20)
            def due_accounts(self, now):
                yield self_account, {"prompt": "指示", "version": 1,
                                     "enabled": True, "days": [0], "times": ["09:00"],
                                     "updated_at": now - timedelta(hours=1)}
            def skip_stale(self, *args): raise AssertionError("paused slot was skipped twice")
            def collection(self, name):
                class Collection:
                    def document(self, _):
                        class Document:
                            def set(self, *_args, **_kwargs): pass
                        return Document()
                return Collection()

        self_now = datetime(2026, 10, 5, 0, 2, tzinfo=timezone.utc)
        self_account = self.account
        with patch.object(cloud_worker, "_process") as process:
            result = cloud_worker.run_tick(DueStore(), FakeVault(), self_now)
        self.assertEqual(result, {"status": "ok", "runs": []})
        process.assert_not_called()

    def test_five_minute_tick_catches_a_slot_between_deliveries(self):
        from datetime import timedelta

        now = datetime(2026, 10, 5, 0, 5, tzinfo=timezone.utc)
        slot = now - timedelta(minutes=3)

        class DueStore:
            def runtime_enabled(self): return True
            def last_tick_at(self): return now - timedelta(minutes=5)
            def due_accounts(self, _now):
                yield self_account, {"prompt": "指示", "version": 1,
                                     "enabled": True, "days": [0], "times": ["09:02"],
                                     "updated_at": now - timedelta(hours=1)}
            def skip_stale(self, *args): raise AssertionError("slot was skipped")
            def collection(self, _name):
                class Collection:
                    def document(self, _id):
                        class Document:
                            def set(self, *_args, **_kwargs): pass
                        return Document()
                return Collection()

        self_account = self.account
        with patch.object(cloud_worker, "_process", return_value="published") as process:
            result = cloud_worker.run_tick(DueStore(), FakeVault(), now)
        self.assertEqual(result, {"status": "ok", "runs": ["published"]})
        self.assertEqual(process.call_args.args[4], slot)


if __name__ == "__main__":
    unittest.main()
