"""Load the shared Secret Manager payload injected by Cloud Run."""

import json
import os
import re

SHARED_KEYS = {"SESSION_COOKIE_SECRET", "LOGIN_PASSWORD", "THREADS_APP_ID", "THREADS_APP_SECRET",
               "GEMINI_PRO_API_KEY", "GEMINI_API_KEY"}


def load_shared_secrets():
    raw = os.environ.get("APP_SECRETS_JSON")
    if not raw:
        return
    value = json.loads(raw)
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k]
                                               for k in SHARED_KEYS):
        raise RuntimeError("共有シークレットの形式が不正です。")
    for key in SHARED_KEYS:
        os.environ.setdefault(key, value[key])
    for key in ("META_KEY_GROUPS_JSON", "DEVICE_ENROLLMENT_TOKEN_SHA256",
                "THREADS_SHARED_APP_ID", "THREADS_SHARED_APP_SECRET"):
        if key in value:
            if not isinstance(value[key], str) or not value[key]:
                raise RuntimeError("共有シークレットの形式が不正です。")
            os.environ.setdefault(key, value[key])
    if "META_KEY_GROUPS_JSON" in value:
        policy = json.loads(value["META_KEY_GROUPS_JSON"])
        for group in policy.get("groups", []):
            for field, env_name in group.items():
                if field in {"app_id_env", "app_secret_env", "user_id_env", "access_token_env", "login_password_env"}:
                    if not isinstance(env_name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", env_name):
                        raise RuntimeError("共有シークレットの形式が不正です。")
                    if env_name in value:
                        if not isinstance(value[env_name], str) or not value[env_name]:
                            raise RuntimeError("共有シークレットの形式が不正です。")
                        os.environ.setdefault(env_name, value[env_name])
