"""HTTP endpoint that serves the in-progress login QR image.

Why an endpoint instead of a ``data:`` URI in the description
------------------------------------------------------------
Home Assistant's ``SHOW_PROGRESS`` UI does not render the step description at
all (observed live: only the title and spinner appeared), and markdown image
sanitisers commonly strip ``data:`` URIs anyway. A real URL is the reliable
option.

Security
--------
The URL carries an unguessable capability token (24 random bytes URL-safe) and
expires with the QR itself, so an unauthenticated LAN observer cannot obtain a
usable login QR by guessing. The QR is only stored while a login is running.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass

from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView

from .core.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

#: Slightly longer than the QR token's own 300 s lifetime.
QR_TTL_SECONDS = 330


@dataclass(slots=True)
class _QrEntry:
    nonce: str
    png: bytes
    expires_at: float


class QrStore:
    """Holds the single most recent login QR, keyed by an unguessable nonce."""

    def __init__(self) -> None:
        self._entry: _QrEntry | None = None

    def put(self, png: bytes) -> str:
        """Store a QR image and return the nonce that addresses it."""
        nonce = secrets.token_urlsafe(24)
        self._entry = _QrEntry(
            nonce=nonce,
            png=png,
            expires_at=time.monotonic() + QR_TTL_SECONDS,
        )
        _LOGGER.debug("QR image stored (%d bytes, nonce=%s...)", len(nonce) * 0 + len(png), nonce[:6])
        return nonce

    def get(self, nonce: str) -> bytes | None:
        """Return the PNG for *nonce*, or None if absent/expired."""
        entry = self._entry
        if entry is None or entry.nonce != nonce:
            return None
        if time.monotonic() > entry.expires_at:
            self._entry = None
            return None
        return entry.png

    def clear(self) -> None:
        self._entry = None


class QrImageView(HomeAssistantView):
    """Serve the current login QR without authentication.

    The request must present the nonce, which is only ever embedded in the
    config-flow description shown to the person performing the login.
    """

    url = "/api/ezykam/qr/{nonce}"
    name = "api:ezykam:qr"
    requires_auth = False

    async def get(self, request: web.Request, nonce: str) -> web.Response:
        hass = request.app[KEY_HASS]
        store: QrStore | None = hass.data.get(DOMAIN, {}).get("qr_store")
        png = store.get(nonce) if store else None
        if png is None:
            # Deliberately the same response for "never existed" and "expired"
            # so the endpoint cannot be used to probe for valid nonces.
            return web.Response(status=404, text="not found")
        return web.Response(body=png, content_type="image/png")


def get_qr_store(hass) -> QrStore:  # noqa: ANN001 - HomeAssistant, typed loosely
    """Return (creating once) the QR store attached to hass.data."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get("qr_store")
    if store is None:
        store = QrStore()
        domain_data["qr_store"] = store
        hass.http.register_view(QrImageView())
        _LOGGER.debug("Registered QR image endpoint")
    return store
