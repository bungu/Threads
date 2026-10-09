import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from threads_bot.login_limiter import LoginLimiter, LocalLoginLimiter
from threads_bot.local_auth_store import LocalAuthStore


class Document:
    def __init__(self):
        self.data = None
        self.reads = 0

    def get(self, transaction=None):
        self.reads += 1
        return self

    def to_dict(self):
        return self.data

    def delete(self):
        self.data = None


class Transaction:
    def set(self, ref, data):
        ref.data = data


class Database:
    def __init__(self):
        self.doc = Document()

    def collection(self, name):
        assert name == "threads_auth"
        return self

    def document(self, name):
        assert name == "login_global"
        return self.doc

    def transaction(self):
        return Transaction()


class LoginLimiterTest(unittest.TestCase):
    def test_persistent_budget_and_cached_lock(self):
        db = Database()
        with patch("threads_bot.login_limiter.firestore.transactional", lambda fn: fn):
            first = LoginLimiter(db)
            for _ in range(5):
                self.assertEqual(first.reserve(), 0)
            other_instance = LoginLimiter(db)
            self.assertGreater(other_instance.reserve(), 0)
            reads = db.doc.reads
            self.assertGreater(other_instance.reserve(), 0)
            self.assertEqual(db.doc.reads, reads)
            first.reset()
            self.assertEqual(LoginLimiter(db).reserve(), 0)

    def test_local_budget_survives_new_instance(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "auth.sqlite3"
            limiter = LocalLoginLimiter(LocalAuthStore(path))
            for _ in range(5):
                self.assertEqual(limiter.reserve(), 0)
            other = LocalLoginLimiter(LocalAuthStore(path))
            self.assertGreater(other.reserve(), 0)
            limiter.reset()
            self.assertEqual(other.reserve(), 0)


if __name__ == "__main__":
    unittest.main()
