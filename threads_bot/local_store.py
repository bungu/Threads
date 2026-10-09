"""Private SQLite settings for the loopback-only manual test server."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import PROJECT_DIR
from .run_store import RunStore
from .schedule import validate_settings

DEFAULT_DB = PROJECT_DIR / ".local" / "studio.sqlite3"


class LocalStore:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("THREADS_LOCAL_DB", DEFAULT_DB))
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, handle TEXT NOT NULL,
                token TEXT NOT NULL, token_expires_at TEXT NOT NULL,
                connection TEXT NOT NULL, last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                account_id TEXT PRIMARY KEY, prompt TEXT NOT NULL,
                days TEXT NOT NULL, times TEXT NOT NULL, enabled INTEGER NOT NULL,
                version INTEGER NOT NULL, updated_at TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def run_store(self, account_id):
        if not account_id.isascii() or not account_id.isdecimal():
            raise KeyError("アカウントが見つかりません。")
        return RunStore(self.path.parent / f"runs_{account_id}.sqlite3")

    @staticmethod
    def _public(row):
        return {key: row[key] for key in ("id", "name", "handle", "connection",
                                         "token_expires_at", "last_error")}

    def accounts(self):
        rows = self.db.execute("SELECT * FROM accounts WHERE connection != 'disconnected' ORDER BY handle").fetchall()
        result = []
        for row in rows:
            public = self._public(row)
            public["enabled"] = self.settings(row["id"])["enabled"]
            result.append(public)
        return result

    def has_any_account(self):
        """Keep a disconnected first account from being imported again."""
        return self.db.execute("SELECT 1 FROM accounts LIMIT 1").fetchone() is not None

    def account(self, account_id):
        row = self.db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row or row["connection"] == "disconnected":
            raise KeyError("アカウントが見つかりません。")
        return dict(row)

    def add(self, profile, token, expiry):
        account_id = profile["id"]
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            existing = self.db.execute("SELECT connection FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if existing and existing["connection"] != "disconnected":
                raise ValueError("このアカウントは登録済みです。")
            if not existing and len(self.accounts()) >= 5:
                raise ValueError("登録できるアカウントは5件までです。")
            if existing:
                self.db.execute("UPDATE accounts SET name=?, handle=?, token=?, token_expires_at=?, "
                                "connection='connected', last_error=NULL WHERE id=?",
                                (profile["name"][:24], profile["username"], token, expiry.isoformat(), account_id))
            else:
                self.db.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, 'connected', NULL)",
                                (account_id, profile["name"][:24], profile["username"], token, expiry.isoformat()))
                self.db.execute("INSERT INTO settings VALUES (?, '', '[0,1,2,3,4]', '[\"09:00\"]', 0, 1, ?)",
                                (account_id, now))
        return self._public(self.account(account_id))

    def settings(self, account_id):
        row = self.db.execute("SELECT * FROM settings WHERE account_id = ?", (account_id,)).fetchone()
        if not row:
            raise KeyError("設定が見つかりません。")
        return {"prompt": row["prompt"], "days": json.loads(row["days"]),
                "times": json.loads(row["times"]), "enabled": bool(row["enabled"]),
                "version": row["version"], "timezone": "Asia/Tokyo",
                "updated_at": row["updated_at"]}

    def save_settings(self, account_id, value, version):
        account = self.account(account_id)
        clean = validate_settings(value)
        if clean["enabled"] and account["connection"] != "connected":
            raise ValueError("Threadsの接続を確認してください。")
        with self.db:
            row = self.db.execute("SELECT version FROM settings WHERE account_id = ?", (account_id,)).fetchone()
            if not row or row["version"] != version:
                raise ValueError("別の画面で設定が変更されました。再読み込みしてください。")
            clean["version"] = version + 1
            clean["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.db.execute("UPDATE settings SET prompt=?, days=?, times=?, enabled=?, version=?, updated_at=? "
                            "WHERE account_id=?", (clean["prompt"], json.dumps(clean["days"]),
                            json.dumps(clean["times"]), int(clean["enabled"]), clean["version"],
                            clean["updated_at"], account_id))
        return clean

    def update_account(self, account_id, **fields):
        allowed = {"name", "handle", "token", "token_expires_at", "connection", "last_error"}
        if not fields or set(fields) - allowed:
            raise ValueError("更新項目が不正です。")
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self.db:
            self.db.execute(f"UPDATE accounts SET {assignments} WHERE id=?",
                            (*fields.values(), account_id))

    def view(self, account_id):
        account = self.account(account_id)
        runs = self.run_store(account_id)
        try:
            latest = runs.get()
        finally:
            runs.close()
        run = ({"id": latest["id"], "status": latest["status"],
                "text": latest["text"], "post_id": latest["post_id"],
                "scheduled_at": latest["created_at"], "error_class": None,
                "error_message": None} if latest else None)
        return {"account": self._public(account), "settings": self.settings(account_id),
                "runtime_enabled": False, "next_at": None, "latest_run": run,
                "last_generated_text": latest["text"] if latest else None}

    def disconnect(self, account_id):
        runs = self.run_store(account_id)
        try:
            latest = runs.get()
        finally:
            runs.close()
        if latest and latest["status"] in {"publishing", "unknown"}:
            raise ValueError("公開結果を確定してから接続を解除してください。")
        setting = self.settings(account_id)
        if setting["enabled"]:
            self.save_settings(account_id, {**setting, "enabled": False}, setting["version"])
        self.update_account(account_id, connection="disconnected", last_error=None)

    def resolve(self, run_id, post_id=None, confirmed_not_published=False):
        if bool(post_id) == bool(confirmed_not_published):
            raise ValueError("投稿IDまたは未公開の確認を指定してください。")
        for account in self.accounts():
            runs = self.run_store(account["id"])
            try:
                if not runs.get(run_id):
                    continue
                with runs.exclusive():
                    runs.resolve(run_id, "published" if post_id else "failed", post_id)
                self.update_account(account["id"], connection="connected", last_error=None)
                return runs.get(run_id)
            finally:
                runs.close()
        raise ValueError("結果不明の実行が見つかりません。")
