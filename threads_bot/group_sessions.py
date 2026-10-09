"""Password-selected group sessions shared by local and Cloud Run UIs."""

import hashlib
import hmac

from fastapi import HTTPException

from .password_auth import COOKIE, LOCAL_COOKIE, csrf_token, new_session, session_key, valid_session
from .meta_groups import GroupConfigurationError, load_groups


def matching_group(candidate):
    groups = load_groups().groups.values()
    found = None
    encoded = candidate.encode() if isinstance(candidate, str) and len(candidate) <= 512 else b""
    for group in groups:
        if hmac.compare_digest(encoded, group.login_password.encode()):
            found = group
    return found


def group_key(group, *, cloud):
    base = session_key(cloud=cloud)
    payload = f"threads-group-session-v1\n{group.id}\n{group.login_password}".encode()
    return hmac.new(base, payload, hashlib.sha256).digest()


def issue_session(group, *, cloud):
    return group.id + "~" + new_session(group_key(group, cloud=cloud))


def authenticated_group(request, *, cloud):
    cookie_name = COOKIE if cloud else LOCAL_COOKIE
    session = request.cookies.get(cookie_name, "")
    try:
        if len(session) > 300 or session.count("~") != 1:
            raise ValueError()
        group_id, signed = session.split("~")
        group = load_groups().groups[group_id]
        key = group_key(group, cloud=cloud)
        if not valid_session(signed, key):
            raise ValueError()
        return group, key, csrf_token(session, key)
    except GroupConfigurationError:
        raise
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, "ログインしてください。") from None
