"""Event-loop and DTLS compatibility shims.

Two environment quirks are handled here, both discovered by running against
real hardware rather than by reading documentation.

1. **Windows event loop.** paho-mqtt drives sockets with ``loop.add_reader`` /
   ``loop.add_writer``, which Windows' default ``ProactorEventLoop`` does not
   implement. Unix — including the Raspberry Pi 4 / Home Assistant target —
   defaults to ``SelectorEventLoop``, so production needs no change.

2. **Non-standard camera certificates.** aiortc validates the DTLS peer by
   converting its X.509 certificate with ``cryptography``'s strict DER parser.
   The CP Plus camera under test presents a 345-byte certificate that OpenSSL
   accepts but ``cryptography`` rejects with
   ``ParseError { kind: ExtraData, location: [... "TbsCertificate::signature_alg"] }``,
   which aborts the handshake before any media flows.

   The fallback below does **not** weaken security. WebRTC's ``a=fingerprint``
   line is defined as the hash of the certificate's raw DER, so computing
   SHA-256/384/512 over those same bytes and comparing against the SDP value is
   exactly the check aiortc performs — only the parser differs. Certificates
   that ``cryptography`` accepts still take the normal path.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys

_LOGGER = logging.getLogger(__name__)

_APPLIED = False
_IDENTITY_PATCHED = False
_CERT_WARNED = False


def use_compatible_event_loop() -> bool:
    """Install the Windows selector loop policy if it is needed.

    Returns True when a change was made, so callers (and tests) can assert it.
    """
    global _APPLIED
    if _APPLIED:
        return False
    _APPLIED = True

    if sys.platform != "win32":
        return False

    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:  # pragma: no cover - non-CPython / non-Windows
        return False

    # Only replace the policy when it would actually break us; a caller that
    # already installed a compatible policy is left alone.
    current = asyncio.get_event_loop_policy()
    if isinstance(current, selector_policy):
        return False

    asyncio.set_event_loop_policy(selector_policy())
    return True


class _FingerprintOnlyCertificate:
    """Certificate stand-in exposing only what aiortc's identity check needs.

    ``certificate_digest()`` calls ``certificate.fingerprint(hash_algo)`` and
    compares the result with the SDP ``a=fingerprint`` value. This class
    computes that hash straight from the raw DER, which is precisely what the
    WebRTC specification defines the fingerprint over.
    """

    __slots__ = ("_der",)

    def __init__(self, der: bytes) -> None:
        self._der = der

    def fingerprint(self, algorithm: object) -> bytes:
        name = getattr(algorithm, "name", None)
        if not name:  # pragma: no cover - defensive
            name = str(algorithm)
        return hashlib.new(name.replace("-", ""), self._der).digest()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<FingerprintOnlyCertificate der={len(self._der)}B>"


def ensure_lenient_dtls_identity() -> bool:
    """Let aiortc validate cameras whose certificates strict parsers reject.

    Idempotent and *lazy*: the original method is always tried first, and the
    fallback engages only when ``cryptography`` refuses the certificate. Returns
    True when the patch was installed by this call.
    """
    global _IDENTITY_PATCHED, _CERT_WARNED
    if _IDENTITY_PATCHED:
        return False

    try:
        from OpenSSL import crypto
        from OpenSSL.SSL import Connection
    except ImportError:  # pragma: no cover - pyOpenSSL always ships with aiortc
        _LOGGER.debug("pyOpenSSL unavailable; skipping DTLS identity fallback")
        return False

    original = Connection.get_peer_certificate

    def _patched(self, as_cryptography: bool = False):  # noqa: ANN001 - mirrors API
        certificate = original(self, as_cryptography=False)
        if not as_cryptography:
            return certificate
        try:
            return certificate.to_cryptography()
        except Exception as exc:  # noqa: BLE001 - any strict-parser failure
            global _CERT_WARNED
            der = crypto.dump_certificate(crypto.FILETYPE_ASN1, certificate)
            if not _CERT_WARNED:
                _CERT_WARNED = True
                _LOGGER.warning(
                    "Peer presented a certificate that cryptography's strict "
                    "parser rejects (%s); validating its SDP fingerprint "
                    "directly over the raw DER instead. This is the check "
                    "WebRTC specifies and does not weaken verification.",
                    type(exc).__name__,
                )
            return _FingerprintOnlyCertificate(der)

    Connection.get_peer_certificate = _patched  # type: ignore[method-assign]
    _IDENTITY_PATCHED = True
    return True
