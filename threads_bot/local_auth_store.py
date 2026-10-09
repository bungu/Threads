"""SQLite storage for the local login attempt budget."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .local_store import DEFAULT_DB


class LocalAuthStore:
    def __init__(self, path=None):
        base = Path(os.environ.get("THREADS_LOCAL_DB", DEFAULT_DB))
        self.path = Path(path or base.parent / "auth.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS login_state (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def decode(raw):
        data = json.loads(raw)
        for key in ("window_start", "blocked_until"):
            if data.get(key):
                data[key] = datetime.fromisoformat(data[key])
        return data

    @staticmethod
    def encode(data):
        return json.dumps(data, default=lambda value: value.isoformat())
