"""Loopback-only management UI for manual local Threads posting."""

import hmac
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from dotenv import dotenv_values

from .cloud_tokens import ConnectionError, inspect_token, prepare_token, refresh_token
from .config import PROJECT_DIR, ConfigurationError, ThreadsCredentials, gemini_credentials, load_environment
from .gemini import GenerationError, create_clients
from .local_store import LocalStore
from .password_auth import LOCAL_COOKIE as COOKIE, SESSION_AGE
from .security_headers import protect
from .run_store import RunStoreError
from .service import post_once
from .threads_client import PublicationUnknown, ThreadsClient, ThreadsError
from .meta_groups import group_configuration, load_groups, GroupConfigurationError
from .group_ip import DENIED_MESSAGE, public_group
from .local_store import DEFAULT_DB
from .group_sessions import authenticated_group, issue_session, matching_group
from .group_ip import require_group_ip
from .login_limiter import LocalLoginLimiter

STATIC = Path(__file__).resolve().parents[1] / "static"
LOCAL_SECRET_KEYS = {"GEMINI_PRO_API_KEY", "GEMINI_API_KEY", "THREADS_APP_ID",
                     "THREADS_APP_SECRET", "THREADS_USER_ID", "THREADS_ACCESS_TOKEN"}


def reload_local_secrets():
    """Pick up newly filled local credentials without restarting the UI."""
    keys = set(LOCAL_SECRET_KEYS)
    try:
        config = group_configuration()
        for group in config["groups"] if config is not None else []:
            keys.update(value for name, value in group.items() if name.endswith("_env") and isinstance(value, str))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise GroupConfigurationError("Metaキー群のサーバー設定を確認してください。") from None
    for key, value in dotenv_values(PROJECT_DIR / ".env").items():
        if key in keys and value:
            os.environ[key] = value


class AccountInput(BaseModel):
    user_id: str = Field(pattern=r"^[0-9]+$")
    access_token: str = Field(min_length=10)


class SettingsInput(BaseModel):
    prompt: str
    days: list[int]
    times: list[str]
    enabled: bool
    version: int


class ManualPostInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=2400)


class ResolveInput(BaseModel):
    post_id: str | None = None
    confirmed_not_published: bool = False


class LoginInput(BaseModel):
    password: str = Field(min_length=1, max_length=512)


def get_login_limiter(request: Request):
    if not hasattr(request.app.state, "login_limiter"):
        request.app.state.login_limiter = LocalLoginLimiter()
    return request.app.state.login_limiter


def create_app(store_factory=LocalStore):
    load_environment()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.local_mode = True

    @app.exception_handler(GroupConfigurationError)
    async def invalid_group_config(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.middleware("http")
    async def headers(request: Request, call_next):
        return protect(await call_next(request))

    def loopback_only(request: Request):
        if (not request.client or request.client.host not in {"127.0.0.1", "::1"} or
                request.url.hostname not in {"127.0.0.1", "localhost"}):
            raise HTTPException(403, "ローカルから開いてください。")

    def same_origin(request: Request):
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if request.headers.get("origin") != expected:
            raise HTTPException(403, "許可されていない画面からの要求です。")

    def local_only(request: Request):
        loopback_only(request)
        group, key, csrf = authenticated_group(request, cloud=False)
        request.state.meta_group = group
        request.state.csrf = csrf
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            same_origin(request)
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
                raise HTTPException(403, "画面を再読み込みしてください。")
        return "owner"

    def local_group(request: Request, uid=Depends(local_only)):
        reload_local_secrets()
        return request.state.meta_group

    def threads_allowed(request: Request, group=Depends(local_group)):
        require_group_ip(group, request)
        if group.read_only and request.method not in {"GET", "HEAD", "OPTIONS"}:
            raise HTTPException(403, "このPWは閲覧専用です。")
        return group

    def app_credentials(group):
        return {"app_id": group.app_id, "app_secret": group.app_secret, "require_app_binding": True}

    def store(group=Depends(threads_allowed)):
        if group.read_only:
            yield None
            return
        base = Path(os.environ.get("THREADS_LOCAL_DB", DEFAULT_DB))
        instance = store_factory(path=base.parent / f"meta_{group.app_id}" / base.name)
        try:
            yield instance
        finally:
            instance.close()

    @app.get("/")
    def index(request: Request, _=Depends(loopback_only)):
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/app.js")
    def script(request: Request, _=Depends(loopback_only)):
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.get("/lucide.js")
    def lucide_script(request: Request, _=Depends(loopback_only)):
        return FileResponse(STATIC / "lucide.js", media_type="text/javascript")

    @app.post("/api/session")
    def login(request: Request, body: LoginInput, response: Response, _=Depends(loopback_only),
              limiter=Depends(get_login_limiter)):
        same_origin(request)
        reload_local_secrets()
        group = matching_group(body.password)
        wait = limiter.reserve()
        if wait:
            raise HTTPException(429, "ログインをしばらく待ってから再試行してください。",
                                headers={"Retry-After": str(wait)})
        if group is None:
            raise HTTPException(401, "ログイン文字列が違います。")
        limiter.reset()
        response.set_cookie(COOKIE, issue_session(group, cloud=False), max_age=SESSION_AGE,
                            httponly=True, secure=False, samesite="strict", path="/")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response, _=Depends(local_only)):
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request, group=Depends(local_group)):
        return {"uid": "owner", "local_mode": True,
                "csrf_token": request.state.csrf, "read_only": group.read_only,
                "meta_group": public_group(group, request),
                "threads_denied_message": DENIED_MESSAGE}

    @app.get("/api/accounts")
    def accounts(group=Depends(threads_allowed), data=Depends(store)):
        if group.read_only:
            configured = load_groups().groups
            base = Path(os.environ.get("THREADS_LOCAL_DB", DEFAULT_DB))
            result = []
            for group_id in group.view_groups:
                target = configured[group_id]
                path = base.parent / f"meta_{target.app_id}" / base.name
                if not path.exists():
                    continue
                with LocalStore(path=path) as database:
                    result.extend({**row, "id": f"{group_id}:{row['id']}", "group_label": target.label}
                                  for row in database.accounts())
            return result
        if not data.has_any_account():
            user_id = group.user_id.strip()
            token = group.access_token.strip()
            if user_id or token:
                if not user_id or not token:
                    raise HTTPException(400, "キー群のユーザーIDとアクセストークンの両方を設定してください。")
                try:
                    profile, verified_token, expiry = prepare_token(token, user_id,
                        **app_credentials(group))
                    data.add(profile, verified_token, expiry)
                except (ConnectionError, ValueError) as exc:
                    raise HTTPException(400, str(exc)) from exc
        return data.accounts()

    @app.post("/api/accounts")
    def add(body: AccountInput, group=Depends(threads_allowed), data=Depends(store)):
        try:
            reload_local_secrets()
            profile, token, expiry = prepare_token(body.access_token, body.user_id,
                **app_credentials(group))
            return data.add(profile, token, expiry)
        except (ConnectionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/accounts/{account_id}")
    def account(account_id: str, group=Depends(threads_allowed), data=Depends(store)):
        try:
            if group.read_only:
                group_id, user_id = account_id.split(":", 1)
                if group_id not in group.view_groups or not user_id.isascii() or not user_id.isdecimal():
                    raise KeyError()
                target = load_groups().groups[group_id]
                base = Path(os.environ.get("THREADS_LOCAL_DB", DEFAULT_DB))
                path = base.parent / f"meta_{target.app_id}" / base.name
                if not path.exists():
                    raise KeyError()
                with LocalStore(path=path) as database:
                    view = database.view(user_id)
                    view["account"]["id"] = account_id
                    return view
            return data.view(account_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.put("/api/accounts/{account_id}/settings")
    def save(account_id: str, body: SettingsInput, _=Depends(local_only), data=Depends(store)):
        try:
            saved = data.save_settings(account_id, body.model_dump(), body.version)
            return {"settings": saved, "view": data.view(account_id)}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409 if "別の画面" in str(exc) else 400, str(exc)) from exc

    @app.put("/api/accounts/{account_id}/connection")
    def reconnect(account_id: str, body: AccountInput, group=Depends(threads_allowed), data=Depends(store)):
        if body.user_id != account_id:
            raise HTTPException(400, "ユーザーIDを確認してください。")
        try:
            account = data.account(account_id)
            runs = data.run_store(account_id)
            try:
                latest = runs.get()
            finally:
                runs.close()
            if latest and latest["status"] in {"publishing", "unknown"}:
                raise HTTPException(409, "公開結果を確定してからトークンを更新してください。")
            reload_local_secrets()
            profile, token, expiry = prepare_token(body.access_token, account_id,
                **app_credentials(group))
            data.update_account(account_id, name=profile["name"][:24],
                                handle=profile["username"], token=token,
                                token_expires_at=expiry.isoformat(),
                                connection="connected", last_error=None)
            return data.view(account_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/accounts/{account_id}/connection")
    def disconnect(account_id: str, _=Depends(local_only), data=Depends(store)):
        try:
            data.disconnect(account_id)
            return {"ok": True}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/accounts/{account_id}/post-now")
    def post_now(account_id: str, body: ManualPostInput, group=Depends(threads_allowed), data=Depends(store)):
        if not body.prompt.strip():
            raise HTTPException(400, "プロンプトを入力してください。")
        try:
            reload_local_secrets()
            account = data.account(account_id)
            if account["connection"] != "connected":
                raise HTTPException(409, "Threadsの再接続が必要です。")
            gemini = gemini_credentials()
            if datetime.fromisoformat(account["token_expires_at"]) < datetime.now(timezone.utc) + timedelta(days=7):
                token, _ = refresh_token(account["token"])
                _, expiry = inspect_token(token, account_id,
                    app_id=app_credentials(group)["app_id"], app_secret=app_credentials(group)["app_secret"])
                data.update_account(account_id, token=token, token_expires_at=expiry.isoformat())
                account["token"] = token
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ConfigurationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ConnectionError as exc:
            data.update_account(account_id, connection="reconnect_required",
                                last_error="Threadsの再接続が必要です。")
            raise HTTPException(400, str(exc)) from exc
        runs = data.run_store(account_id)
        pro = flash = threads = None
        try:
            pro, flash = create_clients(gemini)
            threads = ThreadsClient(ThreadsCredentials(account["token"], account_id))
            run_id, text, post_id = post_once(body.prompt.strip(), pro, flash, threads, runs)
            outcome = "published"
        except RunStoreError as exc:
            raise HTTPException(409, str(exc)) from exc
        except PublicationUnknown:
            data.update_account(account_id, connection="review_required",
                                last_error="公開結果を確認してください。")
            outcome = "unknown"
        except ThreadsError as exc:
            if "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
                data.update_account(account_id, connection="reconnect_required",
                                    last_error="Threadsの再接続が必要です。")
            outcome = "threads_rejected"
        except GenerationError:
            outcome = "generation_failed"
        except Exception:
            outcome = "failed"
        finally:
            if threads: threads.close()
            if pro: pro.close()
            if flash: flash.close()
            runs.close()
        return {"status": outcome, "view": data.view(account_id)}

    @app.post("/api/runs/{run_id}/resolve")
    def resolve(run_id: str, body: ResolveInput, _=Depends(local_only), data=Depends(store)):
        try:
            result = data.resolve(run_id, body.post_id, body.confirmed_not_published)
            return result
        except (ValueError, RunStoreError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/runtime")
    def runtime(_=Depends(threads_allowed)):
        return {"enabled": False, "can_enable": False}

    @app.put("/api/runtime")
    def runtime_save(_=Depends(threads_allowed)):
        raise HTTPException(403, "ローカルでは定期実行を変更できません。")

    return app


app = create_app()
