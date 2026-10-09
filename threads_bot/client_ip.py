"""Read a trusted suffix of X-Forwarded-For, never a caller-supplied prefix."""

import ipaddress
import os


def client_ip(request) -> str | None:
    # The direct run.app route normally appends one trusted hop. An external
    # Application Load Balancer appends two (client, forwarding-rule IP).
    try:
        hops = int(os.environ.get("TRUSTED_XFF_HOPS", "1"))
        if hops not in (1, 2):
            return None
        values = request.headers.get("x-forwarded-for", "").split(",")
        if len(values) < hops:
            return None
        return str(ipaddress.ip_address(values[-hops].strip()))
    except (ValueError, IndexError):
        return None
