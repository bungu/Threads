"""Threads connection checks and Secret Manager token versions."""

import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from .settings import THREADS_API_VERSION

BASE = f"https://graph.threads.net/{THREADS_API_VERSION}"
AUTH_BASE = "https://graph.threads.net"


class ConnectionError(ValueError):
    pass


class TokenVault:
    def __init__(self, client=None, project=None, *, app_id=None):
        if client is None:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
        self.client = client
        if app_id is not None and not re.fullmatch(r"[0-9]+", app_id):
            raise ValueError("MetaアプリIDが不正です。")
        self.app_id = app_id
        self.project = project or os.environ["GOOGLE_CLOUD_PROJECT"]

    def name(self, account_id):
        if not re.fullmatch(r"[0-9]+", account_id):
            raise ValueError("ThreadsユーザーIDが不正です。")
        prefix = f"threads-{self.app_id}-" if self.app_id else "threads-"
        return f"projects/{self.project}/secrets/{prefix}{account_id}"

    def put(self, account_id, token):
        from google.cloud import secretmanager
        from google.api_core.exceptions import NotFound
        name = self.name(account_id)
        try:
            self.client.add_secret_version(request={"parent": name,
                                                    "payload": {"data": token.encode("utf-8")}})
        except NotFound as exc:
            raise ConnectionError("このアカウントのクラウド保存領域が未準備です。") from exc
        # Keep only the newest active version so rotations do not accumulate cost.
        versions = list(self.client.list_secret_versions(request={"parent": name}))
        newest = max(versions, key=lambda version: version.create_time)
        for version in versions:
            if version.name != newest.name and version.state != secretmanager.SecretVersion.State.DESTROYED:
                self.client.destroy_secret_version(request={"name": version.name})
        return name

    def get(self, ref):
        if self.app_id and not re.fullmatch(
                re.escape(f"projects/{self.project}/secrets/threads-{self.app_id}-") + r"[0-9]+", ref):
            raise ConnectionError("別のMetaキー群のトークンは使用できません。")
        result = self.client.access_secret_version(request={"name": ref + "/versions/latest"})
        return result.payload.data.decode("utf-8")


def _request(client, url, *, token, params=None):
    try:
        response = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectionError("Threadsへの接続を確認できません。トークンを確認してください。") from exc


def inspect_token(token, user_id, *, app_id=None, app_secret=None, client=None):
    """Verify identity and the publish scope without creating a post."""
    own_client = client is None
    client = client or httpx.Client(timeout=20)
    try:
        profile = _request(client, f"{BASE}/me", token=token,
                           params={"fields": "id,username,name"})
        if str(profile.get("id")) != str(user_id):
            raise ConnectionError("ユーザーIDとトークンの対象が一致しません。")
        if not profile.get("username"):
            raise ConnectionError("Threadsのユーザーネームを取得できません。")
        if not app_id or not app_secret:
            raise ConnectionError("投稿権限の検証にはTHREADS_APP_IDとTHREADS_APP_SECRETが必要です。")
        try:
            response = client.get(f"{AUTH_BASE}/oauth/access_token", params={
                "grant_type": "client_credentials", "client_id": app_id,
                "client_secret": app_secret})
            response.raise_for_status()
            app_token = response.json().get("access_token")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise ConnectionError("Threadsアプリの認証情報を確認できません。") from exc
        if not isinstance(app_token, str) or not app_token:
            raise ConnectionError("Threadsアプリの認証情報を確認できません。")
        data = _request(client, f"{AUTH_BASE}/debug_token", token=app_token,
                        params={"input_token": token}).get("data", {})
        # Some Threads responses omit app_id; reject a conflicting one when present.
        if data.get("app_id") is not None and str(data["app_id"]) != str(app_id):
            raise ConnectionError("トークンとMetaキー群のアプリが一致しません。")
        if not data.get("is_valid") or str(data.get("user_id")) != str(user_id):
            raise ConnectionError("トークンの対象アカウントを確認できません。")
        scopes = set(data.get("scopes") or [])
        if not {"threads_basic", "threads_content_publish"}.issubset(scopes):
            raise ConnectionError("threads_basicとthreads_content_publishの権限が必要です。")
        expires = data.get("expires_at")
        if not isinstance(expires, int) or expires <= 0:
            raise ConnectionError("トークンの有効期限を確認できません。")
        expiry = datetime.fromtimestamp(expires, timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            raise ConnectionError("Threadsトークンの有効期限が切れています。")
        return {"id": str(profile["id"]), "username": profile["username"],
                "name": profile.get("name") or profile["username"]}, expiry
    finally:
        if own_client:
            client.close()


def refresh_token(token, *, client=None):
    own_client = client is None
    client = client or httpx.Client(timeout=20)
    try:
        data = _request(client, f"{AUTH_BASE}/refresh_access_token", token=token,
                        params={"grant_type": "th_refresh_token"})
        new_token = data.get("access_token")
        seconds = data.get("expires_in")
        if not isinstance(new_token, str) or not new_token or not isinstance(seconds, int):
            raise ConnectionError("トークン更新の応答が不正です。")
        return new_token, datetime.now(timezone.utc) + timedelta(seconds=seconds)
    finally:
        if own_client:
            client.close()


def prepare_token(token, user_id, *, app_id, app_secret, require_app_binding=False):
    """Exchange short-lived tokens, then require a verified long-lived token."""
    profile, expiry = inspect_token(token, user_id, app_id=app_id, app_secret=app_secret)
    if not require_app_binding and expiry >= datetime.now(timezone.utc) + timedelta(days=7):
        return profile, token, expiry
    # Multi-group cloud connections must exchange with the selected App Secret.
    # Merely checking /me or accepting a client-supplied App ID is insufficient.
    # If Meta rejects re-exchange, require a fresh short-lived token from that app.
    with httpx.Client(timeout=20) as client:
        data = _request(client, f"{AUTH_BASE}/access_token", token=token,
                        params={"grant_type": "th_exchange_token", "client_secret": app_secret})
    long_token = data.get("access_token")
    if not isinstance(long_token, str) or not long_token:
        raise ConnectionError("長期トークンへの交換に失敗しました。")
    profile, expiry = inspect_token(long_token, user_id, app_id=app_id, app_secret=app_secret)
    if expiry < datetime.now(timezone.utc) + timedelta(days=7):
        raise ConnectionError("長期トークンの有効期限を確認できません。")
    return profile, long_token, expiry
