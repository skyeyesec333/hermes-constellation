"""URL safety: SSRF prevention, validation, and normalisation for Constellation.

Every URL that any adapter fetches must pass through validate_http_url().
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    """Raised when a URL fails safety validation."""


# Hostnames that resolve to local/private addresses
_BLOCKED_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "local",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
})


def is_loopback_or_private(host: str) -> bool:
    """Return True if the host string represents a non-routable address.

    Covers IPv4 loopback/private/link-local/multicast/unspecified and
    IPv6 loopback/unspecified/multicast/link-local/unique-local.
    """
    # Strip brackets from IPv6 literal
    stripped = host.strip("[]")

    # Check blocked hostnames before attempting IP parse
    if stripped.lower() in _BLOCKED_HOSTNAMES:
        return True

    try:
        addr = ipaddress.ip_address(stripped)
    except ValueError:
        return False

    if addr.is_loopback:
        return True
    if addr.is_private:
        return True
    if addr.is_link_local:
        return True
    if addr.is_multicast:
        return True
    if addr.is_unspecified:
        return True

    return False


def validate_http_url(raw: str, *, allow_localhost: bool = False) -> str:
    """Validate and normalise an HTTP/HTTPS URL for safe fetching.

    Returns the normalised URL string (scheme://netloc/path?query, no fragment).
    Raises UnsafeUrlError on any safety violation.

    Rejects: non-HTTP schemes, credentials in URL, loopback/private/multicast/
    link-local/unspecified IPs, blocked hostnames, empty hosts.
    """
    if not raw or not isinstance(raw, str):
        raise UnsafeUrlError("URL must be a non-empty string")

    parsed = urlsplit(raw)

    # Scheme
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError(f"unsupported URL scheme: {parsed.scheme or '(none)'}")

    # Credentials
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URL must not contain credentials")

    # Host
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL must have a non-empty host")

    if not allow_localhost and is_loopback_or_private(host):
        category = _classify_unsafe_host(host)
        raise UnsafeUrlError(f"URL targets {category} address: {host}")

    # Rebuild without fragment
    return _rebuild_url(parsed)


def _classify_unsafe_host(host: str) -> str:
    """Return a human-readable category for an unsafe host."""
    stripped = host.strip("[]")
    if stripped.lower() in _BLOCKED_HOSTNAMES:
        return "blocked hostname"
    try:
        addr = ipaddress.ip_address(stripped)
    except ValueError:
        return "unsafe"
    if addr.is_loopback:
        return "loopback"
    if addr.is_private:
        return "private"
    if addr.is_link_local:
        return "link-local"
    if addr.is_multicast:
        return "multicast"
    if addr.is_unspecified:
        return "unspecified"
    return "unsafe"


def _rebuild_url(parsed) -> str:
    """Rebuild a URL from parsed components, dropping fragment."""
    result = f"{parsed.scheme}://{parsed.netloc}"
    if parsed.path:
        result += parsed.path
    if parsed.query:
        result += f"?{parsed.query}"
    return result
