"""Pure-Python cloud library for CP Plus Ezykam.

This package contains **all** protocol knowledge: authentication, discovery,
MQTT signalling and WebRTC. It deliberately imports nothing from Home
Assistant, so it can be imported, unit-tested and exercised by the standalone
bridge on any Python 3.12-compatible interpreter.

Home Assistant glue lives one level up in :mod:`custom_components.ezykam`.
"""

from __future__ import annotations

from .auth import DEFAULT_POLL_INTERVAL, QrLoginFlow, login_with_qr
from .client import DEFAULT_BASE_URL, DEFAULT_CLOUD_TYPE, DEFAULT_REGION, EzykamClient
from .discovery import DiscoveryResult, discover
from .errors import (
    EzykamApiError,
    EzykamError,
    EzykamProtocolError,
    EzykamRateLimitError,
    EzykamTransportError,
    InvalidCredentialsError,
    QrLoginCancelledError,
    QrLoginError,
    QrLoginRejectedError,
    QrLoginTimeoutError,
    SessionExpiredError,
)
from .models import AuthSession, Camera, Home, QrChallenge, fingerprint
from .redact import REDACTED, mask, redact, redact_headers, redact_url

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_CLOUD_TYPE",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_REGION",
    "REDACTED",
    "AuthSession",
    "Camera",
    "DiscoveryResult",
    "EzykamApiError",
    "EzykamClient",
    "EzykamError",
    "EzykamProtocolError",
    "EzykamRateLimitError",
    "EzykamTransportError",
    "Home",
    "InvalidCredentialsError",
    "QrChallenge",
    "QrLoginCancelledError",
    "QrLoginError",
    "QrLoginFlow",
    "QrLoginRejectedError",
    "QrLoginTimeoutError",
    "SessionExpiredError",
    "discover",
    "fingerprint",
    "login_with_qr",
    "mask",
    "redact",
    "redact_headers",
    "redact_url",
]
