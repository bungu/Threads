"""Shared, persistent attempt budget for the cloud administrator login."""

import threading
from datetime import datetime, timedelta, timezone

from google.cloud import firestore

MAX_ATTEMPTS = 5
WINDOW = timedelta(minutes=15)
LOCK_TIME = timedelta(minutes=15)


def reserve_state(current, now):
    """Shared login policy; storage adapters only supply atomic persistence."""
    blocked = current.get("blocked_until")
    if blocked and blocked > now:
        return current, blocked
    window_start = current.get("window_start")
    count = int(current.get("count", 0)) if window_start and now - window_start < WINDOW else 0
    if count >= MAX_ATTEMPTS:
        blocked = now + LOCK_TIME
        return {"count": count, "window_start": window_start, "blocked_until": blocked}, blocked
    return {"count": count + 1, "window_start": window_start if count else now,
            "blocked_until": None}, None


class LoginLimiter:
    def __init__(self, db=None):
        self.db = db or firestore.Client()
        self.ref = self.db.collection("threads_auth").document("login_global")
        self._cache_lock = threading.Lock()
        self._blocked_until = None

    def reserve(self) -> int:
        """Reserve one check atomically; return seconds to wait when blocked."""
        now = datetime.now(timezone.utc)
        with self._cache_lock:
            if self._blocked_until and self._blocked_until > now:
                return max(1, int((self._blocked_until - now).total_seconds()) + 1)

        transaction = self.db.transaction()

        @firestore.transactional
        def reserve_one(tx):
            current = self.ref.get(transaction=tx).to_dict() or {}
            updated, blocked = reserve_state(current, now)
            if updated != current:
                tx.set(self.ref, updated)
            return blocked

        blocked = reserve_one(transaction)
        if blocked:
            with self._cache_lock:
                self._blocked_until = blocked
            return max(1, int((blocked - now).total_seconds()) + 1)
        return 0

    def reset(self):
        """A successful login starts a fresh attempt window."""
        self.ref.delete()
        with self._cache_lock:
            self._blocked_until = None


class LocalLoginLimiter:
    def __init__(self, storage=None):
        from .local_auth_store import LocalAuthStore
        self.storage = storage or LocalAuthStore()

    def reserve(self):
        now = datetime.now(timezone.utc)
        with self.storage.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM login_state WHERE id='global'").fetchone()
            current = self.storage.decode(row[0]) if row else {}
            updated, blocked = reserve_state(current, now)
            db.execute("INSERT OR REPLACE INTO login_state VALUES ('global', ?)",
                       (self.storage.encode(updated),))
            db.commit()
        return max(1, int((blocked - now).total_seconds()) + 1) if blocked else 0

    def reset(self):
        with self.storage.connection() as db:
            db.execute("DELETE FROM login_state WHERE id='global'")
