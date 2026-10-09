"""Owner-only management API and an IAM-protected scheduler worker."""

import hmac
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, Field

from .cloud_store import CloudStore, ConflictError
from .cloud_config import load_shared_secrets
from .cloud_tokens import ConnectionError, TokenVault, prepare_token
from .cloud_worker import _process, run_tick
from .password_auth import COOKIE, SESSION_AGE
from .meta_groups import load_groups, GroupConfigurationError
from .group_sessions import authenticated_group, group_key, issue_session, matching_group
from .login_limiter import LoginLimiter
from .security_headers import protect
from .client_ip import client_ip
from .geo_gate import GeoGate
from .group_ip import DENIED_MESSAGE, public_group
from .group_ip import require_group_ip

STATIC = Path(__file__).resolve().parents[1] / "static"
LOGGER = logging.getLogger(__name__)


class LoginInput(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class AccountInput(BaseModel):
    user_id: str = Field(pattern=r"^[0-9]+$")
    access_token: str = Field(min_length=10)


class SettingsInput(BaseModel):
    prompt: str
    days: list[int]
    times: list[str]
    enabled: bool
    version: int


class ResolveInput(BaseModel):
    post_id: str | None = None
    confirmed_not_published: bool = False


class RuntimeInput(BaseModel):
    enabled: bool


class ManualPostInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=2400)


def _origin(request):
    expected = os.environ.get("PUBLIC_ORIGIN", "").rstrip("/")
    if not expected or request.headers.get("origin") != expected:
        raise HTTPException(403, "許可されていない画面からの要求です。")


def _identity(request: Request):
    group, key, csrf = authenticated_group(request, cloud=True)
    request.state.meta_group = group
    request.state.csrf = csrf
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        _origin(request)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
            raise HTTPException(403, "画面を再読み込みしてください。")
    return "owner"


def _group_identity(request: Request, uid=Depends(_identity)):
    return uid


def _owner(request: Request, uid=Depends(_group_identity)):
    require_group_ip(request.state.meta_group, request)
    if request.state.meta_group.read_only and request.method not in {"GET", "HEAD", "OPTIONS"}:
        raise HTTPException(403, "このPWは閲覧専用です。")
    return uid


def _store(request: Request, owner=Depends(_owner)):
    group = request.state.meta_group
    if group.read_only:
        return None
    if not hasattr(request.app.state, "group_stores"):
        request.app.state.group_stores = {}
    if group.app_id not in request.app.state.group_stores:
        request.app.state.group_stores[group.app_id] = CloudStore(namespace=group.namespace)
    return request.app.state.group_stores[group.app_id]


def _vault(request: Request, owner=Depends(_owner)):
    group = request.state.meta_group
    if not hasattr(request.app.state, "group_vaults"):
        request.app.state.group_vaults = {}
    if group.app_id not in request.app.state.group_vaults:
        request.app.state.group_vaults[group.app_id] = TokenVault(app_id=group.app_id)
    return request.app.state.group_vaults[group.app_id]


def get_login_limiter(request: Request):
    if not hasattr(request.app.state, "login_limiter"):
        request.app.state.login_limiter = LoginLimiter()
    return request.app.state.login_limiter


def create_app(mode=None):
    load_shared_secrets()
    mode = mode or os.environ.get("APP_MODE", "web")
    geo_gate = GeoGate() if mode == "web" else None
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(GroupConfigurationError)
    async def invalid_group_config(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        if geo_gate:
            ip = client_ip(request)
            if os.environ.get("GEO_PROBE") == "1" and request.url.path == "/":
                LOGGER.warning("geo_probe source_ip=%s country=%s xff_parts=%s",
                               ip or "unavailable", geo_gate.country(ip) or "unknown",
                               len(request.headers.get("x-forwarded-for", "").split(",")))
            if os.environ.get("GEO_ENFORCE") == "1" and request.url.path.startswith("/api/"):
                country = geo_gate.country(ip)
                if country and country != "JP":
                    return protect(JSONResponse(status_code=403, content={
                        "detail": "日本国外からの管理画面利用はできません。",
                        "geolocation_source": "https://db-ip.com"}))
        return protect(await call_next(request))

    if mode == "worker":
        @app.post("/tasks/tick")
        def tick(request: Request):
            header = request.headers.get("authorization", "")
            audience = os.environ.get("WORKER_AUDIENCE")
            invoker = os.environ.get("SCHEDULER_SERVICE_ACCOUNT")
            if not header.startswith("Bearer ") or not audience or not invoker:
                raise HTTPException(401, "ワーカー認証が必要です。")
            try:
                claims = google_id_token.verify_oauth2_token(
                    header[7:], google_requests.Request(), audience=audience)
            except Exception as exc:
                raise HTTPException(401, "ワーカー認証に失敗しました。") from exc
            if claims.get("email") != invoker or not claims.get("email_verified"):
                raise HTTPException(403, "呼び出し元が許可されていません。")
            outcomes = {}
            for group in load_groups().groups.values():
                if group.read_only:
                    continue
                outcomes[group.id] = run_tick(CloudStore(namespace=group.namespace),
                    TokenVault(app_id=group.app_id), meta_group=group)
            return {"groups": outcomes}

        return app

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/app.js")
    def script():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.get("/lucide.js")
    def lucide_script():
        return FileResponse(STATIC / "lucide.js", media_type="text/javascript")

    @app.post("/api/session")
    def login(request: Request, body: LoginInput, response: Response, limiter=Depends(get_login_limiter)):
        _origin(request)
        group = matching_group(body.password)
        wait = limiter.reserve()
        if wait:
            raise HTTPException(429, "ログインをしばらく待ってから再試行してください。",
                                headers={"Retry-After": str(wait)})
        if group is None:
            raise HTTPException(401, "ログイン文字列が違います。")
        limiter.reset()
        response.set_cookie(COOKIE, issue_session(group, cloud=True), max_age=SESSION_AGE, httponly=True,
                            secure=True, samesite="strict", path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request, owner=Depends(_group_identity)):
        group = request.state.meta_group
        return {"uid": owner, "csrf_token": request.state.csrf,
                "meta_group": public_group(group, request),
                "read_only": group.read_only,
                "threads_denied_message": DENIED_MESSAGE,
                "can_manage_runtime": not group.read_only}

    @app.post("/api/logout")
    def logout(response: Response, owner=Depends(_identity)):
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/accounts")
    def accounts(request: Request, owner=Depends(_owner), store=Depends(_store)):
        group = request.state.meta_group
        if group.read_only:
            configured = load_groups().groups
            return [{**account, "id": f"{target_id}:{account['id']}", "group_label": configured[target_id].label}
                    for target_id in group.view_groups
                    for account in CloudStore(namespace=configured[target_id].namespace).accounts(owner)]
        return store.accounts(owner)

    @app.post("/api/accounts")
    def connect(request: Request, body: AccountInput, owner=Depends(_owner), store=Depends(_store),
                vault=Depends(_vault)):
        if len(store.accounts(owner)) >= 5:
            raise HTTPException(400, "登録できるアカウントは5件までです。")
        existing = store.account_record(body.user_id)
        if existing and (existing.get("owner") != owner or existing.get("connection") != "disconnected"):
            raise HTTPException(409, "このアカウントは登録済みです。")
        try:
            profile, token, expiry = prepare_token(body.access_token, body.user_id,
                app_id=request.state.meta_group.app_id, app_secret=request.state.meta_group.app_secret, require_app_binding=True)
            ref = vault.put(body.user_id, token)
            if existing:
                store.update_account(body.user_id, name=profile["name"][:24],
                                     handle=profile["username"], connection="connected",
                                     token_ref=ref, token_expires_at=expiry, last_error=None)
                return CloudStore._public_account(store.account(owner, body.user_id))
            return store.create_account(owner, profile, ref, expiry, True)
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/accounts/{account_id}")
    def account(request: Request, account_id: str, owner=Depends(_owner), store=Depends(_store)):
        try:
            group = request.state.meta_group
            if group.read_only:
                group_id, user_id = account_id.split(":", 1)
                if group_id not in group.view_groups or not user_id.isascii() or not user_id.isdecimal():
                    raise KeyError()
                target = load_groups().groups[group_id]
                view = CloudStore(namespace=target.namespace).account_view(owner, user_id)
                view["account"]["id"] = account_id
                return view
            return store.account_view(owner, account_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.put("/api/accounts/{account_id}/settings")
    def save(account_id: str, body: SettingsInput, owner=Depends(_owner), store=Depends(_store)):
        try:
            saved = store.save_settings(owner, account_id, body.model_dump(), body.version)
            return {"settings": saved, "view": store.account_view(owner, account_id)}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/accounts/{account_id}/post-now")
    def post_now(request: Request, account_id: str, body: ManualPostInput, owner=Depends(_owner),
                 store=Depends(_store), vault=Depends(_vault)):
        if not body.prompt.strip():
            raise HTTPException(400, "プロンプトを入力してください。")
        try:
            account = store.account(owner, account_id)
            if account.get("connection") != "connected":
                raise HTTPException(409, "Threadsの再接続が必要です。")
            settings = store.settings(owner, account_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        now = datetime.now(timezone.utc)
        outcome = _process(store, vault, account, settings, now, now,
                           manual_prompt=body.prompt.strip(), meta_group=request.state.meta_group)
        if outcome == "duplicate_or_busy":
            raise HTTPException(409, "このアカウントの投稿処理が進行中です。")
        return {"status": outcome, "view": store.account_view(owner, account_id)}

    @app.put("/api/accounts/{account_id}/connection")
    def reconnect(request: Request, account_id: str, body: AccountInput, owner=Depends(_owner),
                  store=Depends(_store), vault=Depends(_vault)):
        try:
            account = store.account(owner, account_id)
            if body.user_id != account_id:
                raise ValueError("ユーザーIDを確認してください。")
            latest = store.latest_run(account.get("last_run_id"))
            if latest and latest.get("status") in {"publishing", "unknown"}:
                raise ConflictError("公開結果を確定してからトークンを更新してください。")
            profile, token, expiry = prepare_token(body.access_token, body.user_id,
                app_id=request.state.meta_group.app_id, app_secret=request.state.meta_group.app_secret, require_app_binding=True)
            ref = vault.put(account_id, token)
            store.update_account(account_id, token_ref=ref, token_expires_at=expiry,
                                 name=profile["name"][:24], handle=profile["username"],
                                 connection="connected", last_error=None)
            return store.account_view(owner, account_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, ConnectionError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/accounts/{account_id}/connection")
    def disconnect(account_id: str, owner=Depends(_owner), store=Depends(_store)):
        try:
            account = store.account(owner, account_id)
            latest = store.latest_run(account.get("last_run_id"))
            if latest and latest.get("status") in {"publishing", "unknown"}:
                raise ConflictError("公開結果を確定してから接続を解除してください。")
            settings = store.settings(owner, account_id)
            if settings["enabled"]:
                store.save_settings(owner, account_id, {**settings, "enabled": False},
                                    settings["version"])
            store.update_account(account_id, connection="disconnected", last_error=None)
            return {"ok": True}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/runs/{run_id}/resolve")
    def resolve(run_id: str, body: ResolveInput, owner=Depends(_owner), store=Depends(_store)):
        try:
            run = store.resolve_unknown(owner, run_id, body.post_id, body.confirmed_not_published)
            store.update_account(run["account_id"], connection="connected", last_error=None)
            return CloudStore._public_run(run)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/runtime")
    def runtime(request: Request, owner=Depends(_owner), store=Depends(_store)):
        if request.state.meta_group.read_only:
            return {"enabled": False, "can_enable": False}
        return {"enabled": store.runtime_enabled(),
                "can_enable": owner == "owner"}

    @app.put("/api/runtime")
    def runtime_save(body: RuntimeInput, owner=Depends(_owner), store=Depends(_store)):
        if owner != "owner":
            raise HTTPException(403, "定期実行全体の変更は管理者だけが行えます。")
        store.set_runtime(body.enabled)
        return {"enabled": store.runtime_enabled(),
                "can_enable": owner == "owner"}

    return app


app = create_app()
