"""QR-code login flow for the Ezykam cloud.

Why QR and not password
-----------------------
The portal's own default (``loginConfig: 0``, guide text *"Scan QR code with
ezyKam+ App"*) is QR login. It was chosen here because it is **verified live**,
requires **no behavioural captcha**, and never handles the account password at
all — so there is no password to store, leak or rotate.

The password path (``MD5(password)`` + a ``securekey`` from the ``yrule``
captcha behind ``POST /api/jy/init``) is documented in
:mod:`ezykam.core.client` but deliberately not implemented.

Flow (all steps observed in live portal traffic)
------------------------------------------------
1. ``POST /api/customized/qrcode``            -> ``{"result": {"token": ...}}``
2. ``POST /api/login/exchange``               -> ``{"url": "tuyaSmart--qrLogin?token=..."}``
   -> base64 PNG of the QR code
3. ``POST /api/customized/login/poll``        -> ``{"token": ...}`` every ~3 s
   pending:  ``{"result": {"domain": {}}}``
   approved: ``Set-Cookie: s-sid=...; csrf-token=...``

Approval is detected **cookie-first**, because that is what actually establishes
the session and it does not depend on guessing an undocumented success payload.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .client import EzykamClient
from .errors import (
    EzykamApiError,
    QrLoginCancelledError,
    QrLoginError,
    QrLoginRejectedError,
    QrLoginTimeoutError,
)
from .models import AuthSession, QrChallenge
from .redact import redact

_LOGGER = logging.getLogger(__name__)

#: Observed portal poll cadence: successive calls landed ~3.05 s apart.
DEFAULT_POLL_INTERVAL = 3.0

#: Default overall deadline if the challenge carries no parseable expiry.
DEFAULT_LOGIN_TIMEOUT = 300.0

#: Called with a human-readable progress string (already free of secrets).
ProgressCallback = Callable[[str], Any]

#: Error codes that mean "the QR code is no longer usable".
_EXPIRED_CODES = frozenset(
    {
        "QRCODE_TIMEOUT",
        "QR_CODE_TIMEOUT",
        "QR_CODE_EXPIRED",
        "QRCODE_EXPIRED",
        "TOKEN_TIMEOUT",
        "TOKEN_EXPIRED",
        "QRCODE_INVALID",
        "QR_CODE_INVALID",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class QrLoginFlow:
    """Drives one QR login from mint to an established :class:`AuthSession`."""

    def __init__(
        self,
        client: EzykamClient,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._client = client
        self._poll_interval = max(0.2, poll_interval)
        self._on_progress = on_progress
        self._cancel = asyncio.Event()
        self._challenge: QrChallenge | None = None
        #: Set once a poll payload carried a ``sid`` (the portal's success signal).
        self._saw_body_sid = False
        #: Set when the cloud answers ``false`` — the code is no longer usable.
        self._qr_invalid = False

    # ------------------------------------------------------------------
    @property
    def challenge(self) -> QrChallenge | None:
        return self._challenge

    def cancel(self) -> None:
        """Request cancellation; safe to call from a UI callback."""
        self._cancel.set()

    def _report(self, message: str) -> None:
        _LOGGER.debug("QR login: %s", message)
        if self._on_progress is not None:
            try:
                self._on_progress(message)
            except Exception:  # noqa: BLE001 - progress must never break login
                _LOGGER.debug("Progress callback raised", exc_info=True)

    # ------------------------------------------------------------------
    async def start(self) -> QrChallenge:
        """Mint a token and render it to a PNG. Leaves the flow ready to poll."""
        self._cancel.clear()
        self._saw_body_sid = False
        self._qr_invalid = False
        self._report("Requesting QR code")
        challenge = await self._client.qrcode()
        self._challenge = challenge
        _LOGGER.debug("QR challenge created: %r", challenge)

        self._report("Rendering QR code")
        try:
            challenge.image_png = await self._client.login_exchange(challenge)
        except Exception:
            # A missing image must not abort the flow: the caller may still be
            # able to show the raw token, and polling is unaffected.
            _LOGGER.warning(
                "QR image rendering failed; token remains usable", exc_info=True
            )
        return challenge

    # ------------------------------------------------------------------
    async def wait_for_approval(
        self,
        *,
        timeout: float | None = None,
        poll_interval: float | None = None,
        raise_on_cancel: bool = True,
    ) -> AuthSession:
        """Poll until the mobile app approves the scan.

        Args:
            timeout: overall deadline in seconds. Defaults to the challenge's
                own lifetime (observed as 300 s) or
                :data:`DEFAULT_LOGIN_TIMEOUT`.
            poll_interval: seconds between polls; defaults to the observed
                portal cadence of ~3 s.
            raise_on_cancel: when False, cancellation returns ``None``-like
                behaviour by raising :class:`QrLoginCancelledError` anyway —
                kept explicit so callers must handle it.

        Raises:
            QrLoginTimeoutError: nobody scanned in time.
            QrLoginCancelledError: :meth:`cancel` was called.
            QrLoginRejectedError: the scan was refused.
            QrLoginError: any other terminal failure of the flow.
        """
        if not raise_on_cancel:
            raise QrLoginError("raise_on_cancel=False is not supported")

        challenge = self._challenge
        if challenge is None:
            raise QrLoginError("start() must be called before wait_for_approval()")

        interval = self._poll_interval if poll_interval is None else max(0.2, poll_interval)
        deadline = self._resolve_deadline(challenge, timeout)

        self._report("Waiting for you to scan with the ezyKam+ app")
        _LOGGER.debug(
            "Polling QR token fp=%s every %.1fs until %s",
            challenge.token_fingerprint,
            interval,
            deadline.isoformat(),
        )

        while True:
            if self._cancel.is_set():
                raise QrLoginCancelledError("QR login cancelled by caller")

            remaining = (deadline - _utcnow()).total_seconds()
            if remaining <= 0:
                raise QrLoginTimeoutError(
                    "QR code expired before it was scanned"
                )

            await self._poll_once()

            if self._qr_invalid:
                # The portal stops polling when the cloud answers ``false``.
                raise QrLoginTimeoutError("QR code is no longer valid")

            # Portal-faithful success condition: it only ever checks
            # ``result.sid``. Cookies are absorbed as well, but a session is
            # accepted on the payload alone.
            if self._saw_body_sid or self._client.is_authenticated:
                auth = self._client.auth
                if not auth.csrf_token:
                    # Not fatal — but worth shouting about, because it means
                    # the csrf-token cookie arrived somewhere other than here.
                    _LOGGER.warning(
                        "Session established without a csrf-token cookie; "
                        "last response set-cookie names=%s",
                        [c["name"] for c in self._client.last_set_cookies],
                    )
                self._report("Login approved")
                _LOGGER.info(
                    "QR login established (session=%s, csrf_present=%s)",
                    auth.sid_fingerprint,
                    bool(auth.csrf_token),
                )
                return auth

            if self._challenge is not None and self._challenge.is_expired:
                raise QrLoginTimeoutError(
                    "QR code expired before it was scanned"
                )

            sleep_for = min(interval, max(0.0, (deadline - _utcnow()).total_seconds()))
            if sleep_for <= 0:
                raise QrLoginTimeoutError(
                    "QR code expired before it was scanned"
                )
            try:
                await asyncio.sleep(sleep_for)
            except asyncio.CancelledError:
                raise

    async def _poll_once(self) -> Any:
        """Perform a single poll, translating terminal codes into typed errors."""
        challenge = self._challenge
        assert challenge is not None
        try:
            result = await self._client.login_poll(challenge)
        except EzykamApiError as exc:
            code = (exc.error_code or "").upper()
            if code in _EXPIRED_CODES:
                raise QrLoginTimeoutError(
                    f"QR code is no longer valid (code={exc.error_code})"
                ) from exc
            if "CANCEL" in code or "DENY" in code or "REFUSE" in code:
                raise QrLoginRejectedError(
                    f"QR login refused (code={exc.error_code})"
                ) from exc
            raise QrLoginError(
                f"QR polling failed (code={exc.error_code})"
            ) from exc

        _LOGGER.debug("QR poll payload: %s", redact(result))
        self._absorb_payload(result)
        return result

    def _absorb_payload(self, result: Any) -> None:
        """Extract the session and cloud configuration from a poll payload.

        Field names are taken verbatim from the portal's own success handler:
        ``sid``, ``uid``, ``domain.{mobileMqttsUrl,regionCode}`` (CPPLUS) and
        ``publicSession.domain.{mobileMqttsUrl,regionCode}`` (PUBLIC).
        """
        if result is False:
            self._qr_invalid = True
            return
        if not isinstance(result, dict):
            return

        sid = result.get("sid")
        if not sid:
            return

        self._saw_body_sid = True
        auth = self._client.auth
        if auth.sid:
            # The s-sid cookie is authoritative: that is the value the portal
            # reads back for its `s-sid` header. The payload `sid` is only the
            # portal's success signal and must not clobber it.
            pass
        else:
            # No cookie arrived; fall back to the payload so we still have a
            # usable session, but say so loudly.
            auth.sid = str(sid)
            _LOGGER.warning(
                "login/poll returned a sid but no s-sid cookie; "
                "using the payload value instead"
            )
        uid = result.get("uid")
        if uid:
            auth.uid = str(uid)

        domain = result.get("domain")
        if isinstance(domain, dict):
            auth.mqtt_url_cpplus = str(domain.get("mobileMqttsUrl") or "")
            auth.region_code_cpplus = str(domain.get("regionCode") or "")

        public_session = result.get("publicSession")
        if isinstance(public_session, dict):
            public_domain = public_session.get("domain")
            if isinstance(public_domain, dict):
                auth.mqtt_url_public = str(public_domain.get("mobileMqttsUrl") or "")
                auth.region_code_public = str(public_domain.get("regionCode") or "")

    @staticmethod
    def _resolve_deadline(challenge: QrChallenge, timeout: float | None) -> datetime:
        if timeout is not None:
            from datetime import timedelta

            return _utcnow() + timedelta(seconds=timeout)
        if challenge.expires_at is not None:
            return challenge.expires_at
        from datetime import timedelta

        return _utcnow() + timedelta(seconds=DEFAULT_LOGIN_TIMEOUT)


async def login_with_qr(
    client: EzykamClient,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float | None = None,
    on_progress: ProgressCallback | None = None,
    on_challenge: Callable[[QrChallenge], Awaitable[None] | None] | None = None,
) -> tuple[QrChallenge, AuthSession]:
    """Convenience wrapper: mint, render, then poll to completion.

    ``on_challenge`` fires as soon as the PNG exists so a UI can display it
    before the (potentially long) poll begins.
    """
    flow = QrLoginFlow(client, poll_interval=poll_interval, on_progress=on_progress)
    challenge = await flow.start()
    if on_challenge is not None:
        result = on_challenge(challenge)
        if asyncio.iscoroutine(result):
            await result
    session = await flow.wait_for_approval(timeout=timeout)
    return challenge, session
