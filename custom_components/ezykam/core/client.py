"""Asynchronous HTTP client for the Ezykam cloud API.

This module owns every wire-level concern:

* attaching the ``cloud-type`` / region / session headers the portal sends;
* capturing ``s-sid`` and ``csrf-token`` cookies after a successful login;
* translating the cloud's ``{result, t, success, status}`` envelope into either
  a plain ``result`` value or a typed exception;
* mapping ``USER_SESSION_LOSS`` / ``USER_SESSION_INVALID`` to
  :class:`~ezykam.core.errors.SessionExpiredError`;
* emitting structured, redacted debug logs.

Endpoint paths are taken from captured portal traffic and from the portal's own
JavaScript bundle. Nothing here is invented.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any, Mapping

import aiohttp
from yarl import URL

from .errors import (
    EzykamApiError,
    EzykamProtocolError,
    EzykamRateLimitError,
    EzykamTransportError,
    InvalidCredentialsError,
    SessionExpiredError,
)
from .models import (
    COOKIE_CSRF_TOKEN,
    COOKIE_SESSION_ID,
    HEADER_CSRF_TOKEN,
    HEADER_SESSION_ID,
    SESSION_LOST_CODES,
    AuthSession,
    QrChallenge,
    fingerprint,
)
from .redact import mask, redact, redact_url

_LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://web.ezykam.com"
DEFAULT_REGION = "EU"
DEFAULT_CLOUD_TYPE = "cpplus"

#: Payload prefix the portal sends to ``/api/login/exchange``.
QR_LOGIN_URL_SCHEME = "tuyaSmart--qrLogin"

_REQUEST_TIMEOUT_TOTAL = 25.0
_REQUEST_TIMEOUT_CONNECT = 10.0


def _describe_set_cookies(response: aiohttp.ClientResponse) -> list[dict[str, Any]]:
    """Structured, redacted description of a response's ``Set-Cookie`` headers.

    Cookie *names* and attributes are safe and genuinely diagnostic; values are
    always masked because that is where credential material lives.
    """
    out: list[dict[str, Any]] = []
    try:
        raw_headers = response.headers.getall("Set-Cookie", [])
    except (AttributeError, KeyError):  # pragma: no cover - non-aiohttp response
        return out
    for raw in raw_headers:
        parts = [p.strip() for p in raw.split(";")]
        name, _, _value = parts[0].partition("=")
        entry: dict[str, Any] = {"name": name.strip(), "value": mask(_value)}
        for attr in parts[1:]:
            key, _, value = attr.partition("=")
            key = key.strip().lower()
            if not key:
                continue
            if key in {"domain", "path", "expires", "max-age", "samesite"}:
                entry[key] = value.strip()
            else:
                entry[key] = True
        out.append(entry)
    return out


class EzykamClient:
    """One authenticated connection to the Ezykam cloud.

    The client is safe to share across coroutines for a single account. It does
    not own the underlying :class:`aiohttp.ClientSession` unless it created it,
    so callers can pool connections across coordinators.
    """

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession | None = None,
        auth: AuthSession | None = None,
        base_url: str = DEFAULT_BASE_URL,
        region: str = DEFAULT_REGION,
        cloud_type: str = DEFAULT_CLOUD_TYPE,
        owns_session: bool | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.region = region
        self.cloud_type = cloud_type
        self.auth = auth or AuthSession(region=region, cloud_type=cloud_type)

        if session is None:
            timeout = aiohttp.ClientTimeout(
                total=_REQUEST_TIMEOUT_TOTAL, connect=_REQUEST_TIMEOUT_CONNECT
            )
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._owns_session = True
        else:
            self._session = session
            self._owns_session = bool(owns_session)

        # Re-seed the cookie jar so a restored session behaves like a fresh one.
        if self.auth.is_complete:
            self._seed_cookies()

        #: Set-Cookie description of the most recent response (redacted).
        #: Used by diagnostics to discover which endpoint issues which cookie.
        self.last_set_cookies: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @property
    def is_authenticated(self) -> bool:
        return self.auth.is_complete

    async def close(self) -> None:
        """Close the underlying session if this client owns it."""
        if self._owns_session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> EzykamClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # headers / cookies
    # ------------------------------------------------------------------
    def _seed_cookies(self) -> None:
        """Push the stored session into aiohttp's cookie jar, safely.

        aiohttp quotes cookie values containing RFC6265 reserved characters.
        If it re-encodes ours, the ``Cookie`` header would disagree with the
        ``s-sid`` header we *also* send on every request — a mismatch a server
        can reasonably reject. When that happens we drop the cookie entirely
        and rely on the header alone, which is exactly what the portal's own
        JavaScript sets explicitly via ``cookie.get("s-sid")``.
        """
        cookies: dict[str, str] = {}
        if self.auth.sid:
            cookies[COOKIE_SESSION_ID] = self.auth.sid
        if self.auth.csrf_token:
            cookies[COOKIE_CSRF_TOKEN] = self.auth.csrf_token
        if not cookies:
            return

        response_url = URL(self.base_url)
        self._session.cookie_jar.update_cookies(cookies, response_url=response_url)

        # Verify what would actually be *sent*, not what is stored: aiohttp
        # quotes at output time, so checking the stored value passes even when
        # the wire format differs.
        jar = self._session.cookie_jar.filter_cookies(response_url)
        for name, expected in cookies.items():
            if name not in jar:
                continue
            rendered = jar[name].OutputString()  # "s-sid=value" (quoted if needed)
            if rendered != f"{name}={expected}":
                _LOGGER.warning(
                    "Cookie %s would be sent re-encoded (%s vs %s); dropping it "
                    "and sending the header only, which is what the portal does",
                    name,
                    mask(rendered),
                    mask(f"{name}={expected}"),
                )
                # Any mismatched value is worse than omitting it: the explicit
                # s-sid header is the mechanism the portal itself relies on.
                self._session.cookie_jar.clear(
                    lambda morsel: morsel.key in cookies
                )
                return

    def _build_headers(self, *, auth: bool, cloud_type: str | None = None) -> dict[str, str]:
        """Reproduce the header set the portal sends on every API call.

        Region codes come from the login payload when available (the portal
        reads them back out of localStorage), falling back to the configured
        region before the first login. ``cloud_type`` lets a request override
        the default per device, which is how the portal routes requests whose
        payload carries a ``devId``.
        """
        cpplus_region = (self.auth.region_code_cpplus or self.region) if auth else ""
        public_region = self.auth.region_code_public if auth else ""
        headers: dict[str, str] = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json; charset=utf-8",
            "origin": self.base_url,
            "referer": self.base_url + "/login",
            "x-requested-with": "XMLHttpRequest",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "cloud-type": cloud_type or self.cloud_type,
            "cpplus-region-code": cpplus_region,
            "public-region-code": public_region,
        }
        if auth:
            if self.auth.sid:
                headers[HEADER_SESSION_ID] = self.auth.sid
            if self.auth.csrf_token:
                headers[HEADER_CSRF_TOKEN] = self.auth.csrf_token
        return headers

    def _absorb_cookies(self, response: aiohttp.ClientResponse) -> None:
        """Pull freshly-issued session cookies out of a login response."""
        jar = self._session.cookie_jar.filter_cookies(URL(self.base_url))
        sid = jar.get(COOKIE_SESSION_ID)
        csrf = jar.get(COOKIE_CSRF_TOKEN)
        changed = False
        if sid and sid.value and sid.value != self.auth.sid:
            self.auth.sid = sid.value
            changed = True
        if csrf and csrf.value and csrf.value != self.auth.csrf_token:
            self.auth.csrf_token = csrf.value
            changed = True
        if changed:
            _LOGGER.debug(
                "Session cookies updated: sid_fp=%s csrf_fp=%s",
                self.auth.sid_fingerprint,
                mask(self.auth.csrf_token),
            )

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    async def request(
        self,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        auth: bool = True,
        method: str = "POST",
        cloud_type: str | None = None,
    ) -> Any:
        """Perform a request and unwrap the response envelope.

        Returns the envelope's ``result`` value on success. Raises a typed
        :mod:`ezykam.core.errors` exception otherwise.
        """
        url = self.base_url + path
        headers = self._build_headers(auth=auth, cloud_type=cloud_type)
        started = time.monotonic()

        try:
            async with self._session.request(
                method,
                url,
                json=dict(payload) if payload is not None else None,
                headers=headers,
            ) as response:
                status = response.status
                raw = await response.text()
                self.last_set_cookies = _describe_set_cookies(response)
                self._absorb_cookies(response)
        except asyncio.TimeoutError as exc:
            _LOGGER.debug("Timeout on %s", path)
            raise EzykamTransportError(f"Timeout calling {path}") from exc
        except aiohttp.ClientError as exc:
            _LOGGER.debug("Transport error on %s: %s", path, type(exc).__name__)
            raise EzykamTransportError(
                f"Transport failure calling {path}: {type(exc).__name__}"
            ) from exc

        elapsed_ms = (time.monotonic() - started) * 1000
        body = self._parse_body(raw, path=path, status=status)

        _LOGGER.debug(
            "ezykam %s %s -> %s in %.0fms (session=%s) set-cookie=%s",
            method,
            path,
            status,
            elapsed_ms,
            self.auth.sid_fingerprint,
            [c["name"] for c in self.last_set_cookies],
        )

        # When the cloud rejects a session, show exactly what we transmitted.
        # A missing/blank s-sid header versus a present-but-rejected one are
        # completely different failures, and only this distinguishes them.
        error_code = body.get("errorCode") if body else None
        if status == 401 or error_code in SESSION_LOST_CODES:
            # Compare the value we send as a header with the one the cookie jar
            # would send. If they differ, the server sees two different session
            # ids for one request - a concrete, fixable cause of rejection.
            jar = self._session.cookie_jar.filter_cookies(URL(self.base_url))
            jar_sid = jar.get(HEADER_SESSION_ID)
            header_val = headers.get(HEADER_SESSION_ID, "")
            jar_val = jar_sid.value if jar_sid else None
            _LOGGER.warning(
                "Session rejected by %s (HTTP %s, code=%s); "
                "header s-sid len=%s fp=%s | jar s-sid %s fp=%s | match=%s",
                path,
                status,
                error_code,
                len(header_val),
                fingerprint(header_val),
                "present" if jar_val is not None else "absent",
                fingerprint(jar_val),
                jar_val == header_val if jar_val is not None else "n/a",
            )

        return self._unwrap(body, path=path, status=status)

    @staticmethod
    def _parse_body(raw: str, *, path: str, status: int) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            _LOGGER.debug(
                "Non-JSON body from %s (HTTP %s): %s", path, status, mask(raw[:200])
            )
            return None
        if not isinstance(parsed, dict):
            _LOGGER.debug("Unexpected JSON shape from %s: %s", path, type(parsed).__name__)
            return None
        return parsed

    @staticmethod
    def _unwrap(
        body: dict[str, Any] | None, *, path: str, status: int
    ) -> Any:
        """Turn a response envelope into a result, or raise a typed error."""
        error_code = body.get("errorCode") if body else None

        # Session loss takes priority over every other interpretation.
        if status == 401 or (error_code in SESSION_LOST_CODES):
            raise SessionExpiredError(
                f"Session rejected by {path} (HTTP {status}, code={error_code})",
                path=path,
                status=status,
                error_code=error_code,
            )

        if not (200 <= status < 300):
            if status == 429:
                raise EzykamRateLimitError(
                    f"Rate limited on {path}", path=path, status=status,
                    error_code=error_code,
                )
            raise EzykamApiError.from_envelope(
                path=path, status=status, error_code=error_code
            )

        if body is None:
            raise EzykamProtocolError(
                f"Expected JSON from {path} but got an unparseable body "
                f"(HTTP {status})"
            )

        if body.get("success") is not True:
            if error_code in SESSION_LOST_CODES:
                raise SessionExpiredError(
                    f"Session rejected by {path} (code={error_code})",
                    path=path,
                    status=status,
                    error_code=error_code,
                )
            if error_code in {"INVALID_PASSWORD", "USER_PASSWORD_ERROR",
                              "ACCOUNT_PASSWORD_ERROR", "PASSWORD_ERROR"}:
                raise InvalidCredentialsError(
                    f"Credentials rejected by {path} (code={error_code})"
                )
            raise EzykamApiError.from_envelope(
                path=path, status=status, error_code=error_code
            )

        return body.get("result")

    # ------------------------------------------------------------------
    # unauthenticated endpoints
    # ------------------------------------------------------------------
    async def config_info(self, domain: str = "web.ezykam.com") -> dict[str, Any]:
        """Fetch region / login configuration. Verified live (HTTP 200)."""
        result = await self.request(
            "/api/customized/config/info", {"domain": domain}, auth=False
        )
        return result or {}

    async def qrcode(self) -> QrChallenge:
        """Mint a QR login token. Verified live (HTTP 200)."""
        result = await self.request("/api/customized/qrcode", {}, auth=False)
        token = (result or {}).get("token")
        if not token:
            raise EzykamProtocolError("qrcode response did not contain a token")
        return QrChallenge.from_token(str(token), region=self.region)

    async def login_exchange(self, challenge: QrChallenge) -> bytes:
        """Turn a QR token into a PNG image. Verified from captured traffic."""
        payload = {"url": f"{QR_LOGIN_URL_SCHEME}?token={challenge.token}"}
        result = await self.request("/api/login/exchange", payload, auth=False)
        if not isinstance(result, str):
            raise EzykamProtocolError(
                "login/exchange did not return an image data-URL"
            )
        _, _, base64_part = result.partition(",")
        if not base64_part:
            raise EzykamProtocolError("login/exchange returned a malformed data-URL")
        import base64

        try:
            return base64.b64decode(base64_part)
        except (ValueError, TypeError) as exc:
            raise EzykamProtocolError(
                "login/exchange returned undecodable image data"
            ) from exc

    async def login_poll(self, challenge: QrChallenge) -> Any:
        """Ask whether the QR code has been approved yet.

        While pending the cloud answers ``{"result": {"domain": {}}}``.
        On approval the payload gains ``sid`` — the portal's own success
        signal — and the session cookies arrive on the same response.
        A boolean ``false`` means the code is no longer usable.
        """
        return await self.request(
            "/api/customized/login/poll", {"token": challenge.token}, auth=False
        )

    async def password_login(
        self, *, user_name: str, password: str, country_code: str = ""
    ) -> AuthSession:
        """Experimental account/password login.

        **Status: shape verified from the portal bundle, not verified live.**

        The portal computes ``password = MD5(strip_whitespace(password))`` and
        supplies a ``securekey`` produced by ``POST /api/jy/init`` behind the
        ``yrule`` behavioural captcha. That captcha SDK has not been reproduced,
        so this method exists for completeness and raises rather than guessing.
        """
        raise InvalidCredentialsError(
            "Password login requires the yrule behavioural captcha "
            "(POST /api/jy/init), which is not implemented. "
            "Use the QR login flow instead."
        )

    # ------------------------------------------------------------------
    # authenticated endpoints (verified from captured traffic)
    # ------------------------------------------------------------------
    async def logout(self) -> Any:
        """Invalidate the current session server-side."""
        return await self.request("/api/customized/logout", {})

    async def app_info(self) -> Any:
        """``POST /api/customized/app/info`` — the portal's own ``getAppInfo``.

        Returns ``devIdPrefix`` / ``uidPrefix`` / ``sceneIdPrefix`` and the
        API-version mapping table. Distinct from :meth:`web_app_info`.
        """
        return await self.request("/api/customized/app/info", {})

    async def web_app_info(self) -> Any:
        """``POST /api/customized/web/app/info`` — requires a session."""
        return await self.request("/api/customized/web/app/info", {})

    async def fetch_dev_id_prefix(self) -> str:
        """Learn which device-ID prefix belongs to the CPPLUS cloud.

        The portal stores ``devIdPrefix`` from ``/api/customized/app/info`` and
        later uses it to choose the ``Cloud-Type`` header per device: a device
        whose id starts with the prefix is CPPLUS, otherwise PUBLIC.
        Returns "" when unknown.
        """
        result = await self.request("/api/customized/app/info", {})
        if isinstance(result, dict):
            prefix = str(result.get("devIdPrefix") or "")
            self.auth.dev_id_prefix = prefix
            return prefix
        return ""

    def cloud_type_for_device(self, device_id: str) -> str:
        """Resolve the ``Cloud-Type`` header value for a specific device.

        Mirrors the portal's rule exactly: devices whose id starts with the
        account's ``devIdPrefix`` belong to CPPLUS, everything else is PUBLIC.
        Falls back to CPPLUS when the prefix is unknown, which matches this
        integration's cloud-only scope.
        """
        prefix = self.auth.dev_id_prefix
        if not prefix or not device_id:
            return self.cloud_type
        return self.cloud_type if device_id.startswith(prefix) else "public"

    async def homes(self) -> list[Any]:
        result = await self.request("/api/customized/common/homeList", {})
        return list(result or [])

    async def home_devices(self, home_id: str) -> list[Any]:
        """Devices belonging to a home.

        The endpoint is named ``roomList`` but returns **device** records, not
        rooms — verified live against a real account. ``home_id`` must be the
        home's ``gid``/``groupId``; the separate ``id`` field returns an empty
        list (also verified), so callers must not use it.
        """
        result = await self.request(
            "/api/customized/common/roomList", {"homeId": str(home_id)}
        )
        return list(result or [])

    async def shared_devices(self) -> list[Any]:
        result = await self.request("/api/customized/playback/shareList", {})
        return list(result or [])

    async def get_msid(self) -> Any:
        """Acquire MQTT session credentials. **Never log the result.**"""
        return await self.request("/api/hybrid/getMsid", {})

    async def rtc_config(self, device_id: str) -> Any:
        """Per-device RTC configuration (ICE servers, stream profiles).

        The portal computes ``Cloud-Type`` from the device id, so this request
        does the same rather than assuming the account-wide default.
        """
        return await self.request(
            "/api/customized/rtc/config",
            {"devId": str(device_id), "clientTraceId": secrets.token_hex(16)},
            cloud_type=self.cloud_type_for_device(str(device_id)),
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        """A redacted, loggable description of this client."""
        return {
            "base_url": redact_url(self.base_url),
            "region": self.region,
            "cloud_type": self.cloud_type,
            "authenticated": self.is_authenticated,
            "session": repr(self.auth),
        }
