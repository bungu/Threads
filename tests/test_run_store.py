import tempfile
import unittest
from pathlib import Path

from threads_bot.run_store import RunStore, RunStoreError


class RunStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.temp.name) / "runs.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_unknown_blocks_until_resolved(self):
        run_id = self.store.start()
        self.store.update(run_id, "prepared", text="本文")
        self.store.update(run_id, "publishing", container_id="111")
        with self.assertRaises(RunStoreError):
            self.store.ensure_publishable()
        self.store.resolve(run_id, "published", "222")
        self.store.ensure_publishable()
        self.assertEqual(self.store.get(run_id)["post_id"], "222")

    def test_resolve_requires_unknown_and_valid_post_id(self):
        run_id = self.store.start()
        with self.assertRaises(RunStoreError):
            self.store.resolve(run_id, "failed")
        self.store.update(run_id, "unknown")
        with self.assertRaises(RunStoreError):
            self.store.resolve(run_id, "published", "bad")
        self.store.resolve(run_id, "failed")
        self.store.ensure_publishable()

    def test_two_stores_cannot_publish_concurrently(self):
        other = RunStore(self.store.path)
        try:
            with self.store.exclusive():
                with self.assertRaises(RunStoreError):
                    with other.exclusive():
                        pass
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()

