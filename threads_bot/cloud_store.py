"""Firestore persistence for the cloud service. No Threads token is stored here."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from google.cloud import firestore

from .schedule import next_slot, validate_settings


class ConflictError(ValueError):
    pass


class CloudStore:
    def __init__(self, db=None, *, namespace="threads_"):
        self.db = db or firestore.Client()
        self.namespace = namespace

    def collection(self, name):
        return self.db.collection(self.namespace + name)

    @staticmethod
    def _public_account(data):
        return {k: data.get(k) for k in ("id", "name", "handle", "connection",
                                          "token_expires_at", "last_error")}

    def accounts(self, owner):
        docs = self.collection("accounts").where("owner", "==", owner).stream()
        rows = []
        for doc in docs:
            data = doc.to_dict()
            if data.get("connection") == "disconnected":
                continue
            row = self._public_account(data)
            setting = self.collection("settings").document(data["id"]).get().to_dict() or {}
            row["enabled"] = bool(setting.get("enabled"))
            rows.append(row)
        return sorted(rows, key=lambda row: row["handle"] or "")

    def account(self, owner, account_id):
        doc = self.collection("accounts").document(account_id).get()
        data = doc.to_dict() if doc.exists else None
        if not data or data.get("owner") != owner or data.get("connection") == "disconnected":
            raise KeyError("アカウントが見つかりません。")
        return data

    def account_record(self, account_id):
        return self.collection("accounts").document(account_id).get().to_dict()

    def create_account(self, owner, profile, token_ref, expires_at, publishable):
        account_id = profile["id"]
        ref = self.collection("accounts").document(account_id)
        setting_ref = self.collection("settings").document(account_id)
        data = {"id": account_id, "owner": owner, "name": profile["name"][:24],
                "handle": profile["username"], "connection": "connected" if publishable else "permission_missing",
                "token_ref": token_ref, "token_expires_at": expires_at,
                "last_error": None, "updated_at": datetime.now(timezone.utc)}
        settings = {
            "prompt": "", "days": [0, 1, 2, 3, 4], "times": ["09:00"],
            "enabled": False, "timezone": "Asia/Tokyo", "version": 1,
            "updated_at": datetime.now(timezone.utc)}
        tx = self.db.transaction()

        @firestore.transactional
        def commit(transaction):
            if ref.get(transaction=transaction).exists:
                raise ConflictError("このアカウントは登録済みです。")
            transaction.create(ref, data)
            transaction.create(setting_ref, settings)

        commit(tx)
        return self._public_account(data)

    def update_account(self, account_id, **fields):
        self.collection("accounts").document(account_id).update(fields)

    def settings(self, owner, account_id):
        self.account(owner, account_id)
        data = self.collection("settings").document(account_id).get().to_dict()
        return data or {"prompt": "", "days": [], "times": [], "enabled": False,
                        "timezone": "Asia/Tokyo", "version": 0}

    def save_settings(self, owner, account_id, value, version):
        account = self.account(owner, account_id)
        clean = validate_settings(value)
        if clean["enabled"] and account["connection"] != "connected":
            raise ValueError("接続と投稿権限を確認してから定期投稿をONにしてください。")
        ref = self.collection("settings").document(account_id)
        account_ref = self.collection("accounts").document(account_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def commit(tx):
            current = ref.get(transaction=tx).to_dict()
            current_account = account_ref.get(transaction=tx).to_dict()
            if not current or current["version"] != version:
                raise ConflictError("別の画面で設定が変更されました。再読み込みしてください。")
            if not current_account or current_account.get("owner") != owner or (
                    clean["enabled"] and current_account.get("connection") != "connected"):
                raise ConflictError("接続状態が変更されました。再読み込みしてください。")
            saved = {**clean, "version": version + 1,
                     "updated_at": datetime.now(timezone.utc)}
            tx.set(ref, saved)
            return saved

        return commit(transaction)

    def runtime_enabled(self):
        doc = self.collection("runtime").document("global").get()
        return bool(doc.exists and doc.to_dict().get("enabled") is True)

    def last_tick_at(self):
        doc = self.collection("runtime").document("global").get()
        return doc.to_dict().get("last_tick_at") if doc.exists else None

    def set_runtime(self, enabled):
        now = datetime.now(timezone.utc)
        fields = {"enabled": bool(enabled), "updated_at": now}
        if enabled:
            # Activation starts at this instant; slots from a paused period stay skipped.
            fields["last_tick_at"] = now
        self.collection("runtime").document("global").set(fields, merge=True)

    def due_accounts(self, now):
        for doc in self.collection("settings").where("enabled", "==", True).stream():
            settings = doc.to_dict()
            account_doc = self.collection("accounts").document(doc.id).get()
            account = account_doc.to_dict() if account_doc.exists else None
            if account and account.get("connection") == "connected":
                yield account, settings

    def latest_run(self, run_id):
        if not run_id:
            return None
        return self.collection("runs").document(run_id).get().to_dict()

    def account_view(self, owner, account_id, now=None):
        account = self.account(owner, account_id)
        settings = self.settings(owner, account_id)
        latest = self.latest_run(account.get("last_run_id"))
        now = now or datetime.now(timezone.utc)
        running = self.runtime_enabled()
        next_at = next_slot(settings, now) if running else None
        return {"account": self._public_account(account), "settings": settings,
                "next_at": next_at, "runtime_enabled": running,
                "latest_run": self._public_run(latest),
                "last_generated_text": account.get("last_generated_text")}

    @staticmethod
    def _public_run(run):
        if not run:
            return None
        return {k: run.get(k) for k in ("id", "scheduled_at", "status", "text",
                                          "post_id", "error_class", "error_message")}

    def claim(self, account, settings, slot, now, *, manual=False, prompt=None):
        account_id = account["id"]
        run_id = (f"manual_{account_id}_{uuid4().hex}" if manual else
                  f"{account_id}_{slot.strftime('%Y%m%dT%H%MZ')}")
        ref = self.collection("runs").document(run_id)
        lock = self.collection("account_locks").document(account_id)
        token = uuid4().hex
        transaction = self.db.transaction()

        @firestore.transactional
        def commit(tx):
            existing = ref.get(transaction=tx)
            active = lock.get(transaction=tx)
            if existing.exists:
                return None
            lock_data = active.to_dict() if active.exists else {}
            if lock_data.get("expires_at") and lock_data["expires_at"] > now:
                return None
            # An expired owner could have reached publication. Never replace it.
            if lock_data.get("status") in {"publishing", "unknown"}:
                return None
            run = {"id": run_id, "account_id": account_id, "owner": account["owner"],
                   "scheduled_at": slot, "status": "generating", "manual": manual,
                   "prompt": prompt if manual else settings["prompt"],
                   "settings_version": settings["version"], "text": None, "container_id": None,
                   "post_id": None, "error_class": None, "error_message": None,
                   "lease_owner": token, "lease_expires_at": now + timedelta(minutes=4),
                   "created_at": now}
            tx.create(ref, run)
            tx.update(self.collection("accounts").document(account_id), {"last_run_id": run_id})
            tx.set(lock, {"run_id": run_id, "lease_owner": token,
                          "expires_at": run["lease_expires_at"], "status": "generating"})
            return run

        return commit(transaction)

    def skip_stale(self, account, settings, slot, now):
        run_id = f"{account['id']}_{slot.strftime('%Y%m%dT%H%MZ')}"
        try:
            self.collection("runs").document(run_id).create({
                "id": run_id, "account_id": account["id"], "owner": account["owner"],
                "scheduled_at": slot, "status": "skipped", "prompt": settings["prompt"],
                "settings_version": settings["version"], "text": None, "post_id": None,
                "error_class": "stale", "error_message": "予定時刻から5分を超えたためスキップしました。",
                "created_at": now})
        except Exception as exc:
            from google.api_core.exceptions import Conflict
            if not isinstance(exc, Conflict):
                raise

    def transition(self, run, status, *, require_current=False, **fields):
        ref = self.collection("runs").document(run["id"])
        lock = self.collection("account_locks").document(run["account_id"])
        setting_ref = self.collection("settings").document(run["account_id"])
        account_ref = self.collection("accounts").document(run["account_id"])
        runtime_ref = self.collection("runtime").document("global")
        tx = self.db.transaction()

        @firestore.transactional
        def commit(transaction):
            current = ref.get(transaction=transaction).to_dict()
            held = lock.get(transaction=transaction).to_dict()
            if not current or not held or held.get("lease_owner") != run["lease_owner"]:
                raise ConflictError("処理権を失いました。")
            if current["status"] in {"published", "unknown", "failed", "cancelled", "skipped"}:
                raise ConflictError("実行は既に終了しています。")
            if require_current:
                account = account_ref.get(transaction=transaction).to_dict()
                manual = bool(current.get("manual"))
                settings = setting_ref.get(transaction=transaction).to_dict() if not manual else None
                runtime = runtime_ref.get(transaction=transaction).to_dict() if not manual else None
                if (not account or account.get("connection") != "connected" or
                        held.get("expires_at") <= datetime.now(timezone.utc) or
                        not manual and
                        (not settings or not settings["enabled"] or
                         settings["version"] != run["settings_version"] or
                         not runtime or runtime.get("enabled") is not True)):
                    raise ConflictError("設定または接続が変更されました。")
            now = datetime.now(timezone.utc)
            transaction.update(ref, {"status": status, "updated_at": now, **fields})
            if status in {"published", "failed", "cancelled", "skipped"}:
                transaction.delete(lock)
            else:
                transaction.update(lock, {"status": status})
            return {**current, "status": status, **fields}

        return commit(tx)

    def resolve_unknown(self, owner, run_id, post_id=None, confirmed_not_published=False):
        ref = self.collection("runs").document(run_id)
        tx = self.db.transaction()

        @firestore.transactional
        def commit(transaction):
            run = ref.get(transaction=transaction).to_dict()
            if not run or run["owner"] != owner or run["status"] not in {"publishing", "unknown"}:
                raise ValueError("結果不明の実行が見つかりません。")
            if bool(post_id) == bool(confirmed_not_published):
                raise ValueError("投稿IDまたは未公開の確認を指定してください。")
            if post_id and (not post_id.isascii() or not post_id.isdecimal()):
                raise ValueError("投稿IDが不正です。")
            status = "published" if post_id else "failed"
            transaction.update(ref, {"status": status, "post_id": post_id,
                                     "updated_at": datetime.now(timezone.utc)})
            transaction.delete(self.collection("account_locks").document(run["account_id"]))
            return {**run, "status": status, "post_id": post_id}

        return commit(tx)
