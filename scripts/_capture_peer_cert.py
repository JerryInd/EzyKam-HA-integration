"""Capture the camera's DTLS peer certificate and test how it parses.

Runs the real stream with ``get_peer_certificate`` wrapped so the raw DER is
written to disk before aiortc's identity check runs. Then tries three parsers:

1. pyOpenSSL -> cryptography (what aiortc does, and what failed)
2. cryptography directly from DER
3. manual ASN.1 walk to show *where* parsing stops

This distinguishes "the camera sends a non-standard certificate" from
"a pyOpenSSL/cryptography version mismatch in this environment".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import OpenSSL  # noqa: E402
from OpenSSL import crypto  # noqa: E402
from OpenSSL.SSL import Connection  # noqa: E402

OUT = REPO_ROOT / ".session" / "peer_cert.der"

_orig = Connection.get_peer_certificate


def _patched(self, as_cryptography: bool = False):  # noqa: ANN001
    cert = _orig(self, as_cryptography=False)
    try:
        der = crypto.dump_certificate(crypto.FILETYPE_ASN1, cert)
        OUT.write_bytes(der)
        print(f"\n[capture] peer certificate DER saved: {OUT} ({len(der)} bytes)")
    except Exception as exc:  # noqa: BLE001
        print(f"[capture] could not dump DER: {type(exc).__name__}: {exc}")
    if as_cryptography:
        try:
            return _orig(self, as_cryptography=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[capture] pyOpenSSL conversion failed again: {type(exc).__name__}: {exc}")
            raise
    return cert


def analyse(der: bytes) -> None:
    print("\n--- parser comparison ---")
    try:
        from cryptography import x509

        cert = x509.load_der_x509_certificate(der)
        print("cryptography direct parse : OK")
        print("  subject :", cert.subject.rfc4514_string()[:140])
        print("  issuer  :", cert.issuer.rfc4514_string()[:140])
        print("  sig alg :", cert.signature_hash_algorithm.name)
        print("  sha256  :", cert.fingerprint(__import__("cryptography.hazmat.primitives.hashes", fromlist=["SHA256"]).SHA256()).hex().upper())
    except Exception as exc:  # noqa: BLE001
        print(f"cryptography direct parse : FAILED {type(exc).__name__}: {exc}")

    try:
        cert = crypto.load_certificate(crypto.FILETYPE_ASN1, der)
        print("pyOpenSSL load            : OK")
        print("  sha256 (pyOpenSSL)      :", cert.digest("sha256").decode())
    except Exception as exc:  # noqa: BLE001
        print(f"pyOpenSSL load            : FAILED {type(exc).__name__}: {exc}")

    try:
        crypto.load_certificate(crypto.FILETYPE_ASN1, der).to_cryptography()
        print("pyOpenSSL -> cryptography : OK")
    except Exception as exc:  # noqa: BLE001
        print(f"pyOpenSSL -> cryptography : FAILED {type(exc).__name__}: {exc}")


def main() -> int:
    Connection.get_peer_certificate = _patched  # type: ignore[method-assign]

    # Reuse the real streaming script so the handshake is genuinely exercised.
    import scripts.stream_test as stream_test  # type: ignore[import-not-found]

    args = argparse.Namespace(camera=None, profile="main", frames=1, timeout=45)
    rc = stream_test.main_async(args)
    # main_async is a coroutine function; run it on our own loop.
    import asyncio

    rc = asyncio.run(rc) if asyncio.iscoroutine(rc) else rc

    if OUT.exists():
        analyse(OUT.read_bytes())
    else:
        print("\nNo peer certificate was captured (handshake never reached DTLS).")
    return int(rc or 0)


if __name__ == "__main__":
    raise SystemExit(main())
