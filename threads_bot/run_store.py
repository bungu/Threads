import fcntl
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import PROJECT_DIR


DEFAULT_DB = PROJECT_DIR / ".local" / "runs.sqlite3"


class RunStoreError(RuntimeError):
    pass


class RunStore:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS runs ("
            "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, "
            "text TEXT, container_id TEXT, post_id TEXT)"
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        lock_path = self.path.with_suffix(".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RunStoreError("別の投稿処理が進行中です。") from exc
            yield
        finally:
            os.close(fd)

    def ensure_publishable(self) -> None:
        row = self.db.execute(
            "SELECT id FROM runs WHERE status IN ('publishing', 'unknown') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row:
            raise RunStoreError(
                f"実行 {row[0]} の公開結果が未確認です。statusで確認してください。"
            )

    def start(self) -> str:
        run_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO runs (id, created_at, status) VALUES (?, ?, 'generating')",
            (run_id, datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()
        return run_id

    def update(self, run_id: str, status: str, **fields: str) -> None:
        allowed = {"text", "container_id", "post_id"}
        if set(fields) - allowed:
            raise ValueError("保存対象の項目が不正です。")
        values = [status, *fields.values(), run_id]
        columns = ", ".join(["status = ?", *(f"{key} = ?" for key in fields)])
        with self.db:
            cursor = self.db.execute(f"UPDATE runs SET {columns} WHERE id = ?", values)
        if cursor.rowcount != 1:
            raise RunStoreError("実行記録が見つかりません。")

    def get(self, run_id: str | None = None) -> dict[str, str | None] | None:
        self.db.row_factory = sqlite3.Row
        if run_id:
            row = self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        else:
            row = self.db.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def resolve(self, run_id: str, status: str, post_id: str | None = None) -> None:
        row = self.get(run_id)
        if not row or row["status"] not in {"publishing", "unknown"}:
            raise RunStoreError("結果不明の実行IDを指定してください。")
        if status == "published":
            if not post_id or not post_id.isascii() or not post_id.isdecimal():
                raise RunStoreError("公開済みの場合は数字の投稿IDを指定してください。")
            self.update(run_id, status, post_id=post_id)
        elif status == "failed":
            self.update(run_id, status)
        else:
            raise RunStoreError("指定された状態に更新できません。")

