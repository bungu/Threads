"""One Cloud Scheduler delivery. All work finishes inside the HTTP request."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from .cloud_store import CloudStore, ConflictError
from .cloud_tokens import TokenVault, inspect_token, refresh_token
from .config import ThreadsCredentials, gemini_credentials
from .gemini import create_clients, generate_post
from .schedule import slots_between
from .threads_client import PublicationUnknown, ThreadsClient, ThreadsError

log = logging.getLogger(__name__)


def _process(store, vault, account, settings, slot, now, *, manual_prompt=None, meta_group=None):
    run = (store.claim(account, settings, slot, now, manual=True, prompt=manual_prompt)
           if manual_prompt is not None else store.claim(account, settings, slot, now))
    if not run:
        return "duplicate_or_busy"
    run_id = run["id"]
    try:
        token = vault.get(account["token_ref"])
        if account.get("token_expires_at") and account["token_expires_at"] < now + timedelta(days=7):
            token, expiry = refresh_token(token)
            _, expiry = inspect_token(token, account["id"],
                                      app_id=meta_group.app_id if meta_group else os.environ.get("THREADS_APP_ID"),
                                      app_secret=meta_group.app_secret if meta_group else os.environ.get("THREADS_APP_SECRET"))
            vault.put(account["id"], token)
            store.update_account(account["id"], token_expires_at=expiry)
        pro, flash = create_clients(gemini_credentials())
        try:
            text = generate_post(pro, flash, run["prompt"])
        finally:
            pro.close()
            flash.close()
        store.transition(run, "prepared", text=text, require_current=True)
        store.update_account(account["id"], last_generated_text=text)
        client = ThreadsClient(ThreadsCredentials(token, account["id"]))
        try:
            container_id = client.create_container(text)
            store.transition(run, "container_created", container_id=container_id)
            client.wait_until_ready(container_id)
            # Commit the publishing state before sending the non-idempotent request.
            store.transition(run, "publishing", require_current=True)
            try:
                post_id = client.publish(container_id)
            except PublicationUnknown:
                store.transition(run, "unknown", error_class="publication_unknown",
                                 error_message="Threads上で投稿を照合してください。")
                store.update_account(account["id"], connection="review_required",
                                     last_error="公開結果を確認してください。")
                log.warning("run=%s status=unknown", run_id)
                return "unknown"
            except ThreadsError as exc:
                store.transition(run, "failed", error_class="threads_rejected",
                                 error_message="Threadsが公開を拒否しました。")
                if "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
                    store.update_account(account["id"], connection="reconnect_required",
                                         last_error="Threadsの再接続が必要です。")
                log.warning("run=%s status=threads_rejected", run_id)
                return "threads_rejected"
            store.transition(run, "published", post_id=post_id)
            log.info("run=%s status=published", run_id)
            return "published"
        finally:
            client.close()
    except ConflictError:
        try:
            store.transition(run, "cancelled", error_class="settings_changed",
                             error_message="設定または接続が変更されたため中止しました。")
        except ConflictError:
            pass
        log.info("run=%s status=cancelled", run_id)
        return "cancelled"
    except Exception as exc:
        from .cloud_tokens import ConnectionError
        category = ("auth" if isinstance(exc, ConnectionError) or
                    isinstance(exc, ThreadsError) and
                    ("HTTP 401" in str(exc) or "HTTP 403" in str(exc)) else
                    "threads" if isinstance(exc, ThreadsError) else "generation_or_internal")
        # A process failure after the publish marker may mean the post succeeded.
        current = store.collection("runs").document(run_id).get().to_dict()
        if current and current["status"] == "publishing":
            store.transition(run, "unknown", error_class="publication_unknown",
                             error_message="Threads上で投稿を照合してください。")
            store.update_account(account["id"], connection="review_required",
                                 last_error="公開結果を確認してください。")
            category = "publication_unknown"
        else:
            store.transition(run, "failed", error_class=category,
                             error_message="接続を確認してください。" if category == "auth" else
                                           "投稿処理に失敗しました。")
            if category == "auth":
                store.update_account(account["id"], connection="reconnect_required",
                                     last_error="Threadsの再接続が必要です。")
        log.error("run=%s status=%s", run_id, category)
        return category


def run_tick(store=None, vault=None, now=None, *, meta_group=None):
    store = store or CloudStore()
    vault = vault or TokenVault()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
    if not store.runtime_enabled():
        return {"status": "globally_paused", "runs": []}
    previous = store.last_tick_at()
    stale_start = max(previous or now - timedelta(minutes=6), now - timedelta(days=7))
    tasks = []
    for account, settings in store.due_accounts(now):
        changed_at = settings.get("updated_at") or stale_start
        for slot in slots_between(settings, max(stale_start, changed_at), now - timedelta(minutes=5)):
            store.skip_stale(account, settings, slot, now)
        for slot in slots_between(settings, max(now - timedelta(minutes=5), stale_start, changed_at), now):
            tasks.append((account, settings, slot))
    outcomes = []
    with ThreadPoolExecutor(max_workers=min(5, max(1, len(tasks)))) as pool:
        futures = [pool.submit(_process, store, vault, *task, now, meta_group=meta_group) for task in tasks]
        for future in as_completed(futures):
            outcomes.append(future.result())
    store.collection("runtime").document("global").set({"last_tick_at": now}, merge=True)
    return {"status": "ok", "runs": outcomes}
