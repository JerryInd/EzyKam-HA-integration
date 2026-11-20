"""Session and QR challenge value objects."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.ezykam.core.models import (
    DEFAULT_QR_TOKEN_TTL_MS,
    AuthSession,
    QrChallenge,
    fingerprint,
)
from support import SAMPLE_QR_TOKEN

SECRET_SID = "s3cr3t-session-id-value"


def test_qr_token_parses_created_and_expiry() -> None:
    challenge = QrChallenge.from_token(SAMPLE_QR_TOKEN)

    assert challenge.token == SAMPLE_QR_TOKEN
    assert challenge.region == "EU"
    assert challenge.expires_at is not None
    # Two live samples showed exactly 300 000 ms between the embedded
    # timestamps; the parser must reproduce that.
    delta = challenge.expires_at - challenge.created_at
    assert delta.total_seconds() == DEFAULT_QR_TOKEN_TTL_MS / 1000
    assert challenge.ttl_seconds >= 0.0
    assert isinstance(challenge.created_at, datetime)


def test_qr_token_expiry_is_in_the_future_relative_to_creation() -> None:
    challenge = QrChallenge.from_token(SAMPLE_QR_TOKEN)
    assert challenge.created_at < challenge.expires_at  # type: ignore[operator]


def test_malformed_token_falls_back_to_default_ttl() -> None:
    before = datetime.now(timezone.utc) - timedelta(seconds=5)
    challenge = QrChallenge.from_token("definitely-not-a-token")

    assert challenge.expires_at is not None
    assert not challenge.is_expired
    assert (challenge.expires_at - challenge.created_at).total_seconds() == (
        DEFAULT_QR_TOKEN_TTL_MS / 1000
    )
    assert challenge.created_at >= before


def test_expired_challenge_is_detected() -> None:
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    challenge = QrChallenge(
        token="x", created_at=past, expires_at=past + timedelta(seconds=1)
    )
    assert challenge.is_expired
    assert challenge.ttl_seconds == 0.0


def test_auth_session_round_trip() -> None:
    original = AuthSession(
        sid=SECRET_SID,
        csrf_token="csrf-value",
        uid="user-1",
        client_id="client-1",
        region="EU",
        cloud_type="cpplus",
        acquired_at=datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc),
    )
    restored = AuthSession.from_dict(original.to_dict())

    assert restored.sid == original.sid
    assert restored.csrf_token == original.csrf_token
    assert restored.uid == "user-1"
    assert restored.client_id == "client-1"
    assert restored.region == "EU"
    assert restored.acquired_at == original.acquired_at
    assert restored.is_complete
    assert restored.sid_fingerprint == original.sid_fingerprint


def test_incomplete_session_is_detected() -> None:
    # No sid at all -> not established.
    assert not AuthSession().is_complete
    # csrf is optional: the live cloud never issues it and the portal sends
    # an empty csrf-token header, so sid alone is a usable session.
    assert AuthSession(sid="only-one").is_complete
    assert AuthSession(sid="a", csrf_token="b").is_complete
    assert not AuthSession(csrf_token="only-csrf").is_complete
    # ...but we still track whether it happened to be supplied.
    assert not AuthSession(sid="a").has_csrf
    assert AuthSession(sid="a", csrf_token="b").has_csrf


def test_from_dict_tolerates_garbage() -> None:
    restored = AuthSession.from_dict({"acquired_at": "not-a-date", "region": ""})
    assert restored.sid == ""
    assert restored.region == "EU"
    assert not restored.is_complete
    assert isinstance(restored.acquired_at, datetime)


def test_fingerprint_is_stable_and_reversible_free() -> None:
    assert fingerprint(SECRET_SID) == fingerprint(SECRET_SID)
    assert len(fingerprint(SECRET_SID)) == 12
    assert SECRET_SID not in fingerprint(SECRET_SID)
    assert fingerprint(None) == "<none>"


def test_age_seconds_grows() -> None:
    session = AuthSession(
        sid="a", csrf_token="b", acquired_at=datetime.now(timezone.utc) - timedelta(seconds=5)
    )
    assert session.age_seconds >= 4.0
