"""Structured redaction of secrets for logging and diagnostics.

Everything that could be credential material passes through this module before
it reaches a log line, an exception message, a Home Assistant download, or an
API response.

Usage::

    from ezykam.core.redact import redact, redact_headers, mask

    _LOGGER.debug("rtc config: %s", redact(cfg))
    _LOGGER.debug("headers: %s", redact_headers(resp.request_info.headers))

The module is deliberately conservative: it redacts by *key name* using both an
explicit list and a regex, and it knows the handful of container keys (ICE
server lists) where most of the payload is safe but a couple of fields are not.
"""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "<redacted>"

#: Key names that are always secret, compared case-insensitively after
#: stripping ``-`` and ``_``.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "auth",
        "authorization",
        "cookie",
        "setcookie",
        "sid",
        "ssid",
        "ssid2",
        "csrf",
        "csrftoken",
        "localkey",
        "localid",
        "key",
        "apikey",
        "privatekey",
        "secretkey",
        "securekey",
        "msid",
        "clientsecret",
        "credential",
        "credentials",
        "signature",
        "sign",
        "ticket",
        "sessionid",
        "username",
        "turnusername",
        "turnpassword",
        "uid",
        "clientid",
        "useraccount",
        "account",
        "phonenumber",
        "mobile",
        "email",
    }
)

#: Additional substring patterns. Broader than the exact set, so it is applied
#: only when the normalised key is not already handled. Deliberately excludes
#: short stems like ``sig``/``sid`` which would over-match ordinary words
#: ("design", "inside").
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|credential|signature|token|apikey|accesskey|"
    r"refreshkey|privatekey|localkey|authkey|csrf|sessionid|turnpassword)",
    re.IGNORECASE,
)

#: Container keys whose *entries* are mostly public but contain a couple of
#: secret fields. We keep the safe fields and drop the rest. Only actual
#: server lists belong here — composite objects such as ``p2pConfig`` must
#: recurse normally so their nested lists are handled by their own key.
_PARTIAL_CONTAINER_KEYS = frozenset(
    {
        "ices",
        "iceserver",
        "iceservers",
        "turn",
        "turnservers",
        "stun",
        "stunservers",
        "rtcservers",
    }
)

#: The only fields worth keeping inside an ICE/TURN server entry.
#: ``username`` and ``credential`` are deliberately absent: those are the TURN
#: secrets this integration must never print.
_PARTIAL_CONTAINER_KEEP = frozenset({"urls", "url", "ttl", "credentialtype"})

#: HTTP headers that must never be printed in full.
SENSITIVE_HEADERS = frozenset(
    {
        "cookie",
        "set-cookie",
        "authorization",
        "proxy-authorization",
        "s-sid",
        "csrf-token",
        "x-auth-token",
        "x-api-key",
    }
)


def _normalise_key(key: Any) -> str:
    """Lower-case a key and drop ``-``/``_`` separators for matching."""
    return str(key).lower().replace("-", "").replace("_", "")


def is_sensitive_key(key: Any) -> bool:
    """Return True when *key* names something that must never be printed."""
    normalised = _normalise_key(key)
    if normalised in SENSITIVE_KEYS:
        return True
    return bool(_SENSITIVE_KEY_PATTERN.search(normalised))


def mask(value: Any) -> str:
    """Describe a secret without revealing it.

    Length is safe to log and is genuinely useful when debugging truncation or
    encoding problems, so it is intentionally preserved.
    """
    if value is None:
        return "<none>"
    if isinstance(value, (bytes, bytearray)):
        return f"<redacted len={len(value)}>"
    text = str(value)
    return f"<redacted len={len(text)}>"


def redact(data: Any, *, _depth: int = 0) -> Any:
    """Recursively redact *data*.

    Mappings are filtered by key; sequences are mapped element-wise. Depth is
    bounded so a self-referential or pathological structure cannot hang the
    caller.
    """
    if _depth > 12:
        return "<max-depth>"

    if isinstance(data, Mapping):
        out: dict[str, Any] = {}
        for key, value in data.items():
            name = str(key)
            if is_sensitive_key(name):
                out[name] = mask(value)
            elif _normalise_key(name) in _PARTIAL_CONTAINER_KEYS:
                out[name] = _redact_container(value, _depth + 1)
            else:
                out[name] = redact(value, _depth=_depth + 1)
        return out

    if isinstance(data, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in data]

    if isinstance(data, str):
        # Signed/CDN URLs carry credentials in the query string (presigned S3
        # signatures are a live example seen in this integration's own
        # responses), so query parameters are scrubbed too.
        if data.startswith(("http://", "https://")):
            return redact_url(data)
        return data

    if isinstance(data, (int, float, bool)) or data is None:
        return data

    # Unknown object type: describe rather than stringify, in case its repr
    # contains credentials.
    return f"<{type(data).__name__}>"


def _redact_container(value: Any, depth: int) -> Any:
    """Keep only public fields inside ICE/TURN style containers."""
    if isinstance(value, Mapping):
        kept = {
            str(k): redact(v, _depth=depth)
            for k, v in value.items()
            if _normalise_key(k) in _PARTIAL_CONTAINER_KEEP
        }
        dropped = [str(k) for k in value if _normalise_key(k) not in _PARTIAL_CONTAINER_KEEP]
        if dropped:
            kept["_dropped_sensitive_fields"] = dropped
        return kept
    if isinstance(value, (list, tuple)):
        return [_redact_container(item, depth + 1) for item in value]
    return redact(value, _depth=depth)


def redact_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Return a loggable copy of *headers* with credential headers masked."""
    if not headers:
        return {}
    out: dict[str, str] = {}
    for key, value in headers.items():
        if str(key).lower() in SENSITIVE_HEADERS or is_sensitive_key(key):
            out[str(key)] = mask(value)
        else:
            out[str(key)] = str(value)
    return out


def redact_url(url: str) -> str:
    """Return *url* with sensitive query-string parameters masked.

    Tokens are frequently passed as query parameters (the QR login token is a
    good example), so URLs must not be logged raw.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    if not parts.query:
        return url
    pairs = [
        (k, mask(v) if is_sensitive_key(k) else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment)
    )
