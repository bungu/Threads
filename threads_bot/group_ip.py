"""IP policy for management requests; worker OIDC authorization is separate."""

import ipaddress

from fastapi import HTTPException

from .client_ip import client_ip

DENIED_MESSAGE = "このIPからは、このキー群のThreads操作を実行できません。"


def normalize_ip(value):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def parse_allowed_ips(value):
    # Missing/empty policies and a wildcard mixed with addresses are mistakes.
    if not isinstance(value, list) or not value or any(not isinstance(ip, str) for ip in value):
        raise ValueError()
    if value == ["*"]:
        return ("*",)
    return tuple(sorted({normalize_ip(ip) for ip in value}))


def permitted(group, request, *, local=None):
    if local is None:
        local = getattr(request.app.state, "local_mode", False)
    if group.allowed_ips == ("*",):
        return True
    source = request.client.host if local and request.client else (None if local else client_ip(request))
    try:
        return normalize_ip(source) in group.allowed_ips
    except (ValueError, TypeError):
        return False


def require_group_ip(group, request, *, local=None):
    if not permitted(group, request, local=local):
        raise HTTPException(403, DENIED_MESSAGE, headers={"X-Threads-IP": "denied"})


def public_group(group, request, *, local=None):
    return {"id": group.id, "label": group.label, "device_required": group.device_required,
            "threads_allowed": permitted(group, request, local=local)}
