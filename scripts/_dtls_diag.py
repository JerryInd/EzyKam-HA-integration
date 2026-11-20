"""Is DTLS failure environmental or device-specific?

If aiortc cannot parse a certificate it generated itself, the problem is the
pyOpenSSL/cryptography pairing in this environment. If it can, the camera is
sending a certificate that strict parsers reject.
"""

from __future__ import annotations

import cryptography
import OpenSSL

print("cryptography:", cryptography.__version__)
print("pyOpenSSL   :", OpenSSL.__version__)

try:
    from aiortc.rtcdtlstransport import RTCCertificate
except ImportError:  # pragma: no cover - module moved between versions
    from aiortc import RTCCertificate  # type: ignore[attr-defined]

cert = RTCCertificate.generateCertificate()
print("generated aiortc certificate OK")
print("fingerprints:", [f.algorithm for f in cert.getFingerprints()])

# Round-trip through the exact call aiortc makes at DTLS identity validation.
try:
    converted = cert._cert.to_cryptography()
    print("pyOpenSSL -> cryptography conversion: OK")
    print("subject:", converted.subject.rfc4514_string()[:120])
except Exception as exc:  # noqa: BLE001
    print("pyOpenSSL -> cryptography conversion FAILED:", type(exc).__name__, exc)

# And parse the DER directly with cryptography, bypassing pyOpenSSL.
try:
    der = OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_ASN1, cert._cert)
    from cryptography import x509

    x509.load_der_x509_certificate(der)
    print(f"direct DER parse: OK  (der length {len(der)})")
except Exception as exc:  # noqa: BLE001
    print("direct DER parse FAILED:", type(exc).__name__, exc)
