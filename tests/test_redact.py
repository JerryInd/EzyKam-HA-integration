"""Redaction must be total: no secret may survive into a loggable structure."""

from __future__ import annotations

from custom_components.ezykam.core.models import AuthSession, QrChallenge
from custom_components.ezykam.core.redact import (
    REDACTED,
    is_sensitive_key,
    mask,
    redact,
    redact_headers,
    redact_url,
)
from support import SAMPLE_QR_TOKEN

SECRET = "SUPER-SECRET-VALUE-DO-NOT-LEAK"


def test_mask_hides_value_but_keeps_length() -> None:
    assert SECRET not in mask(SECRET)
    assert f"len={len(SECRET)}" in mask(SECRET)
    assert mask(None) == "<none>"


def test_sensitive_key_detection() -> None:
    for key in (
        "password",
        "s-sid",
        "csrf-token",
        "auth",
        "localKey",
        "token",
        "securekey",
        "credential",
        "userName",
        "set-cookie",
        "turnPassword",
    ):
        assert is_sensitive_key(key), key

    for key in (
        "name",
        "devId",
        "productId",
        "cloudOnline",
        "errorCode",
        "homeId",
        "deviceName",
        "success",
        "status",
    ):
        assert not is_sensitive_key(key), key


def test_redact_masks_nested_secrets_and_keeps_useful_fields() -> None:
    payload = {
        "name": "Front Door",
        "password": SECRET,
        "nested": {"s-sid": SECRET, "ok": 1},
        "list": [{"csrf-token": SECRET}, {"safe": "yes"}],
    }
    out = redact(payload)

    assert out["name"] == "Front Door"
    assert out["nested"]["ok"] == 1
    assert out["list"][1]["safe"] == "yes"
    assert SECRET not in repr(out)


def test_ice_container_keeps_urls_and_drops_credentials() -> None:
    cfg = {
        "ices": [
            {
                "urls": "turn:turn.example:3478",
                "username": SECRET,
                "credential": SECRET,
            }
        ],
        "skill": "webrtc",
    }
    out = redact(cfg)

    assert out["skill"] == "webrtc"
    assert out["ices"][0]["urls"] == "turn:turn.example:3478"
    assert SECRET not in repr(out)
    dropped = out["ices"][0]["_dropped_sensitive_fields"]
    assert "username" in dropped and "credential" in dropped


def test_redact_headers() -> None:
    out = redact_headers(
        {
            "cookie": f"s-sid={SECRET}",
            "set-cookie": f"csrf-token={SECRET}",
            "s-sid": SECRET,
            "csrf-token": SECRET,
            "cloud-type": "cpplus",
            "cpplus-region-code": "EU",
        }
    )
    assert SECRET not in repr(out)
    assert out["cloud-type"] == "cpplus"
    assert out["cpplus-region-code"] == "EU"


def test_redact_url_strips_query_secrets() -> None:
    url = f"https://web.ezykam.com/api/x?token={SECRET}&page=2"
    out = redact_url(url)
    assert SECRET not in out
    assert "page=2" in out
    assert out.startswith("https://web.ezykam.com/api/x")


def test_auth_session_repr_never_leaks() -> None:
    session = AuthSession(sid=SECRET, csrf_token=SECRET, uid=SECRET, client_id=SECRET)
    text = repr(session)
    assert SECRET not in text
    assert str(session) == text
    # ...but it must still be useful for debugging.
    assert "AuthSession(" in text
    assert "EU" in text
    assert session.sid_fingerprint in text


def test_qr_challenge_repr_never_leaks_token() -> None:
    challenge = QrChallenge.from_token(SAMPLE_QR_TOKEN)
    text = repr(challenge)
    assert SAMPLE_QR_TOKEN not in text
    assert "QrChallenge(" in text
    assert challenge.token_fingerprint in text


def test_signed_url_query_secrets_are_scrubbed() -> None:
    """Presigned CDN URLs carry signatures in the query string.

    Observed live: web/app/info returns an icon URL with an AWS SigV4
    signature. The path must survive (it is needed for display) but the
    signature must not reach a log.
    """
    url = (
        "https://bucket.s3.amazonaws.com/icon.png"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=EXAMPLEKEYFORTESTS00%2F20261120"
        "&X-Amz-Signature=FAKESIGNATUREVALUE00000000000000"
        "&X-Amz-Expires=3600"
    )
    out = redact({"icon": url, "name": "logo"})

    assert "FAKESIGNATUREVALUE00000000000000" not in repr(out)
    assert "EXAMPLEKEYFORTESTS00" not in repr(out)
    # Non-secret parts stay useful.
    assert "icon.png" in repr(out)
    assert out["name"] == "logo"


def test_plain_url_without_query_is_untouched() -> None:
    url = "https://cpplus-eks-prod-public-data.example/smart/icon/abc.png"
    assert redact({"icon": url})["icon"] == url


def test_redacted_constant_is_the_placeholder() -> None:
    assert REDACTED == "<redacted>"
