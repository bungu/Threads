"""Server-controlled Meta credentials and isolation boundaries (never client chosen)."""

import json
import os
import re
from dataclasses import dataclass, field

import setting

from .group_ip import parse_allowed_ips


class GroupConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class MetaGroup:
    id: str
    label: str
    app_id: str
    app_secret: str = field(repr=False)
    device_required: bool = True
    allowed_ips: tuple[str, ...] = ()
    user_id: str = ""
    access_token: str = field(default="", repr=False)
    login_password: str = field(default="", repr=False)
    view_groups: tuple[str, ...] = ()

    @property
    def read_only(self):
        return self.app_id == ""

    @property
    def namespace(self):
        # Changing a group's App ID never reuses its previous tokens or records.
        return f"threads_meta_{self.app_id}_"


@dataclass(frozen=True)
class MetaGroups:
    groups: dict[str, MetaGroup]
    fallback: str


def group_configuration():
    if "META_KEY_GROUPS_JSON" in os.environ:
        return json.loads(os.environ["META_KEY_GROUPS_JSON"])
    return getattr(setting, "META_KEY_GROUPS", None)


def load_groups():
    """Missing or invalid policy must not silently expose the legacy owner keys."""
    try:
        value = group_configuration()
        groups = {}
        app_ids, secrets, passwords = set(), set(), set()
        for item in value["groups"]:
            group_id = item["id"]
            app_id = item.get("app_id") or os.environ.get(item.get("app_id_env", ""), "")
            secret = item.get("app_secret") or os.environ.get(item.get("app_secret_env", ""), "")
            access = item["access"]
            login_password = item.get("login_password") or os.environ.get(item.get("login_password_env", ""), "")
            if not login_password and item.get("login_password_env") == "LOGIN_PASSWORD":
                login_password = getattr(setting, "LOGIN_PASSWORD", "")
            view_groups = item.get("view_groups", [])
            if (not isinstance(login_password, str) or not login_password or len(login_password) > 512
                    or login_password in passwords or access not in {"signed_in", "view"}):
                raise ValueError()
            if (not isinstance(group_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", group_id)
                    or group_id in groups or not isinstance(app_id, str) or not isinstance(secret, str)
                    or (access == "signed_in" and (not re.fullmatch(r"[0-9]+", app_id)
                        or not secret.strip() or app_id in app_ids or secret in secrets))
                    or (access == "view" and (app_id or secret))):
                raise ValueError()
            if (not isinstance(view_groups, list) or any(not isinstance(v, str) for v in view_groups)
                    or (access == "view" and not view_groups)
                    or (access != "view" and view_groups)):
                raise ValueError()
            label = item.get("label", group_id)
            if not isinstance(label, str) or not 1 <= len(label) <= 60:
                raise ValueError()
            allowed_ips = parse_allowed_ips(item.get("allowed_ips"))
            user_id = item.get("user_id") or os.environ.get(item.get("user_id_env", ""), "")
            token = item.get("access_token") or os.environ.get(item.get("access_token_env", ""), "")
            if (not isinstance(user_id, str) or not isinstance(token, str)
                    or (user_id and not re.fullmatch(r"[0-9]+", user_id))):
                raise ValueError()
            if access == "view" and (user_id or token):
                raise ValueError()
            groups[group_id] = MetaGroup(group_id, label, app_id, secret, False,
                                        allowed_ips, user_id, token, login_password, tuple(view_groups))
            if access != "view":
                app_ids.add(app_id)
                secrets.add(secret)
            passwords.add(login_password)
        fallback = value["fallback"]
        if not groups or fallback not in groups:
            raise ValueError()
        for group in groups.values():
            if group.read_only and (group.id in group.view_groups or any(
                    target not in groups or groups[target].read_only for target in group.view_groups)):
                raise ValueError()
        return MetaGroups(groups, fallback)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise GroupConfigurationError("Metaキー群のサーバー設定を確認してください。") from None

