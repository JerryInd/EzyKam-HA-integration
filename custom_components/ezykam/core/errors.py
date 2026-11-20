"""Exception hierarchy for the Ezykam cloud library.

Design rules:

* Errors carry **structured** context (path, HTTP status, error code) so callers
  can branch without string matching.
* Error messages are built from structural fields only. They never embed raw
  response bodies, headers or tokens, because those can contain credential
  material. Use :mod:`ezykam.core.redact` when a body must be inspected.
"""

from __future__ import annotations


class EzykamError(Exception):
    """Base class for every Ezykam cloud error."""


class EzykamTransportError(EzykamError):
    """The cloud could not be reached (DNS, TLS, timeout, connection reset).

    Distinct from :class:`EzykamApiError`: here the request may or may not have
    reached the server, so callers should treat it as retryable.
    """


class EzykamApiError(EzykamError):
    """The cloud answered, but with an error envelope or a non-2xx status."""

    def __init__(
        self,
        message: str,
        *,
        path: str | None = None,
        status: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.status = status
        self.error_code = error_code

    @classmethod
    def from_envelope(
        cls,
        *,
        path: str,
        status: int,
        error_code: str | None,
    ) -> EzykamApiError:
        """Build an error from structural fields only (no body contents)."""
        code = error_code or "UNKNOWN"
        return cls(
            f"Ezykam API request failed: {path} -> HTTP {status}, code={code}",
            path=path,
            status=status,
            error_code=error_code,
        )


class EzykamRateLimitError(EzykamApiError):
    """The cloud throttled us. Retryable after a backoff."""


class EzykamAuthError(EzykamError):
    """Base class for authentication and authorization failures."""


class SessionExpiredError(EzykamAuthError):
    """The stored session is no longer accepted by the cloud.

    Raised on the ``USER_SESSION_LOSS`` / ``USER_SESSION_INVALID`` error codes.
    Home Assistant maps this to a reauthentication flow.

    Carries the same structural context as :class:`EzykamApiError` so callers
    can branch on ``error_code`` / ``status`` without string matching.
    """

    def __init__(
        self,
        message: str,
        *,
        path: str | None = None,
        status: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.status = status
        self.error_code = error_code


class InvalidCredentialsError(EzykamAuthError):
    """The cloud rejected supplied account credentials."""


class QrLoginError(EzykamError):
    """Base class for failures inside the QR login flow."""


class QrLoginTimeoutError(QrLoginError):
    """Nobody scanned the QR code before it expired."""


class QrLoginCancelledError(QrLoginError):
    """The login flow was cancelled by the caller (user closed the dialog)."""


class QrLoginRejectedError(QrLoginError):
    """The scan completed but was refused by the cloud or the mobile app."""


class EzykamProtocolError(EzykamError):
    """The cloud returned a shape we do not recognise.

    Raised instead of guessing, so that unverified response handling is never
    silently mistaken for working code.
    """
