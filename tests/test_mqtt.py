"""MQTT signaling primitives: envelope, topics, candidate filtering."""

from __future__ import annotations

import asyncio
import json

import pytest

from custom_components.ezykam.core.errors import (
    EzykamProtocolError,
    EzykamTransportError,
)
from custom_components.ezykam.core.mqtt import (
    PROTOCOL_SIGNALING,
    STREAM_TYPE_MAIN,
    STREAM_TYPE_SUB,
    TYPE_ANSWER,
    TYPE_OFFER,
    EzykamMqttClient,
    SignalingChannel,
    build_envelope,
    device_out_topic,
    format_outbound_candidate,
    is_for_session,
    mqtt_username,
    new_session_id,
    normalise_inbound_candidate,
    parse_envelope,
    should_signal_candidate,
    user_topic,
)

MSID = "msid-value-0123456789"
PASSWORD = "super-secret-password"
DEV_ID = "beaaaaaaaaaaaaaaaaaa01"
MOTO_ID = "signalingmjdql"
SESSION = "a" * 32


def run(coro):  # noqa: ANN201
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Topics and identifiers
# ---------------------------------------------------------------------------


def test_username_and_topics_match_portal_format() -> None:
    assert mqtt_username(MSID) == f"web_{MSID}"
    assert user_topic(MSID) == f"/av/u/{MSID}"
    assert device_out_topic(MOTO_ID, DEV_ID) == f"/av/moto/{MOTO_ID}/u/{DEV_ID}"


def test_device_out_topic_prefers_parent_id() -> None:
    assert device_out_topic(MOTO_ID, DEV_ID, parent_id="gateway1") == (
        f"/av/moto/{MOTO_ID}/u/gateway1"
    )


def test_session_id_is_32_hex_chars_without_dashes() -> None:
    sid = new_session_id()
    assert len(sid) == 32
    assert "-" not in sid
    assert all(c in "0123456789abcdef" for c in sid)
    assert new_session_id() != sid


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def test_build_envelope_shape() -> None:
    header = SignalingChannel(
        msid=MSID, device_id=DEV_ID, moto_id=MOTO_ID, session_id=SESSION
    ).base_header

    raw = build_envelope(
        protocol=PROTOCOL_SIGNALING,
        pv="2.3",
        header=header,
        msg_type=TYPE_OFFER,
        msg={"sdp": "v=0", "mode": "webrtc", "stream_type": STREAM_TYPE_SUB},
        tid="tid-1",
        timestamp=1795149219663,
    )
    payload = json.loads(raw)

    assert payload["protocol"] == 302
    assert payload["pv"] == "2.3"
    assert payload["t"] == 1795149219663

    hdr = payload["data"]["header"]
    assert hdr["type"] == "offer"
    assert hdr["tid"] == "tid-1"
    assert hdr["from"] == MSID
    assert hdr["to"] == DEV_ID
    assert hdr["sessionid"] == SESSION
    assert hdr["moto_id"] == MOTO_ID
    assert hdr["seq"] == 0 and hdr["rtx"] == 0
    assert hdr["sub_dev_id"] == ""

    assert payload["data"]["msg"]["stream_type"] == STREAM_TYPE_SUB


def test_stream_type_constants() -> None:
    # mainStream(2) -> 0, substream(4) -> 1
    assert STREAM_TYPE_MAIN == 0
    assert STREAM_TYPE_SUB == 1


def test_parse_envelope_rejects_garbage() -> None:
    with pytest.raises(EzykamProtocolError):
        parse_envelope("not json")
    with pytest.raises(EzykamProtocolError):
        parse_envelope(b"\xff\xfe")
    with pytest.raises(EzykamProtocolError):
        parse_envelope("[1,2,3]")
    with pytest.raises(EzykamProtocolError):
        parse_envelope('"just a string"')


def test_parse_envelope_accepts_bytes_and_str() -> None:
    raw = build_envelope(
        protocol=302, pv="2.2", header={}, msg_type=TYPE_ANSWER, msg={"sdp": "v=0"}
    )
    assert parse_envelope(raw)["protocol"] == 302
    assert parse_envelope(raw.encode())["protocol"] == 302


def test_envelope_round_trip_preserves_message() -> None:
    channel = SignalingChannel(
        msid=MSID, device_id=DEV_ID, moto_id=MOTO_ID, session_id=SESSION
    )
    msg = {"candidate": "a=candidate:1 1 UDP 2130706431 192.0.2.1 5000 typ host", "mode": "webrtc"}
    decoded = parse_envelope(channel.encode(PROTOCOL_SIGNALING, "candidate", msg))

    assert decoded["data"]["msg"] == msg
    assert decoded["data"]["header"]["sessionid"] == SESSION
    assert is_for_session(decoded, SESSION)
    assert not is_for_session(decoded, "b" * 32)


# ---------------------------------------------------------------------------
# Session routing
# ---------------------------------------------------------------------------


def test_is_for_session_requires_matching_header() -> None:
    base = {"protocol": 302, "data": {"header": {"sessionid": SESSION}, "msg": {}}}
    assert is_for_session(base, SESSION)

    assert not is_for_session({"protocol": 302}, SESSION)
    assert not is_for_session({"protocol": 302, "data": "nope"}, SESSION)
    assert not is_for_session({"protocol": 302, "data": {"header": {}}}, SESSION)


# ---------------------------------------------------------------------------
# Candidate filtering (mirrors the portal's .local check)
# ---------------------------------------------------------------------------


def test_mdns_candidates_are_not_signalled() -> None:
    # The portal filters only on ".local" (mDNS); a literal IP host candidate
    # is still signalled — which is what the original browser capture showed.
    assert not should_signal_candidate(
        "a=candidate:1 1 udp 1 5e7a9c3d-4f12-4a8b-9c11-0a1b2c3d4e5f.local 5000 typ host generation 0"
    )
    assert should_signal_candidate(
        "a=candidate:1 1 udp 1 192.0.2.5 5000 typ host generation 0"
    )


def test_srflx_and_relay_candidates_are_signalled() -> None:
    assert should_signal_candidate(
        "a=candidate:2 1 udp 2130706431 203.0.113.9 3478 typ srflx raddr 0.0.0.0 rport 0"
    )
    assert should_signal_candidate(
        "a=candidate:3 1 udp 16777215 192.0.2.1 3478 typ relay raddr 0.0.0.0 rport 0"
    )
    assert not should_signal_candidate("")


def test_candidate_prefix_handling() -> None:
    bare = "candidate:1 1 udp 1 192.0.2.1 5000 typ host"
    assert format_outbound_candidate(bare) == f"a={bare}"
    assert format_outbound_candidate(f"a={bare}") == f"a={bare}"
    # The device echoes back the a= prefixed form; aiortc needs it stripped.
    assert normalise_inbound_candidate(f"a={bare}") == bare
    assert normalise_inbound_candidate(bare) == bare


# ---------------------------------------------------------------------------
# SignalingChannel
# ---------------------------------------------------------------------------


def test_signaling_channel_topics_and_header() -> None:
    channel = SignalingChannel(
        msid=MSID, device_id=DEV_ID, moto_id=MOTO_ID, session_id=SESSION, protocol_version="2.3"
    )
    assert channel.out_topic == f"/av/moto/{MOTO_ID}/u/{DEV_ID}"
    assert channel.in_topic == f"/av/u/{MSID}"
    assert channel.base_header["from"] == MSID
    assert channel.base_header["to"] == DEV_ID
    assert "type" not in channel.base_header  # added per message, not baked in

    payload = json.loads(channel.encode(302, TYPE_OFFER, {"sdp": "v=0"}))
    assert payload["pv"] == "2.3"
    assert payload["data"]["header"]["type"] == "offer"


def test_signaling_channel_gateway_header() -> None:
    channel = SignalingChannel(
        msid=MSID, device_id=DEV_ID, moto_id=MOTO_ID, session_id=SESSION,
        parent_id="gateway1", node_id="node9",
    )
    header = channel.base_header
    assert header["to"] == "gateway1"
    assert header["sub_dev_id"] == "node9"
    assert channel.out_topic.endswith("/gateway1")


# ---------------------------------------------------------------------------
# Client behaviour
# ---------------------------------------------------------------------------


def test_client_requires_credentials() -> None:
    with pytest.raises(ValueError):
        EzykamMqttClient(msid="", password=PASSWORD)
    with pytest.raises(ValueError):
        EzykamMqttClient(msid=MSID, password="")


def test_client_describe_never_leaks_secrets() -> None:
    client = EzykamMqttClient(msid=MSID, password=PASSWORD, keepalive=60)
    client.subscribe("/av/u/" + MSID, qos=1)

    text = repr(client.describe())
    assert MSID not in text
    assert PASSWORD not in text
    assert "m1-cube.cppluscloud.com" in text
    assert "/mqtt" in text
    assert client.subscriptions == (f"/av/u/{MSID}",)


def test_publish_while_disconnected_raises() -> None:
    client = EzykamMqttClient(msid=MSID, password=PASSWORD)

    async def main() -> None:
        with pytest.raises(EzykamTransportError):
            await client.publish("/av/moto/x/u/y", "{}")

    run(main())


def test_subscriptions_are_replayed_after_reconnect() -> None:
    """Subscription management must survive a connection drop."""
    client = EzykamMqttClient(msid=MSID, password=PASSWORD)
    client.subscribe("/av/u/a", qos=1)
    client.subscribe("/av/u/b", qos=0)
    client.subscribe("/av/u/a", qos=1)  # idempotent

    assert client.subscriptions == ("/av/u/a", "/av/u/b")
    assert client.describe()["subscriptions"] == ["/av/u/a", "/av/u/b"]


def test_msid_is_scrubbed_from_topics_and_payloads() -> None:
    """msid is half the MQTT credential and appears in topics and headers."""
    client = EzykamMqttClient(msid=MSID, password=PASSWORD)

    assert client.safe(f"/av/u/{MSID}") == "/av/u/<msid>"
    assert MSID not in client.safe(f'/av/moto/m1/u/{DEV_ID} from={MSID}')
    # Non-matching text passes through untouched.
    assert client.safe("/av/moto/x/u/y") == "/av/moto/x/u/y"
    assert client.safe("") == ""


def test_credentials_constructor_round_trip() -> None:
    client = EzykamMqttClient(msid=MSID, password=PASSWORD)
    assert client.username == f"web_{MSID}"
    assert client.is_connected is False
