"""WebRTC helpers: profile resolution, candidate extraction, session routing."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.ezykam.core.errors import EzykamProtocolError
from custom_components.ezykam.core.webrtc import (
    CODEC_H264,
    CODEC_HEVC,
    PROFILE_MAIN,
    PROFILE_SUB,
    SessionRouter,
    StreamProfile,
    StreamStats,
    WebRtcSession,
    build_ice_servers,
    extract_candidates,
    list_profiles,
    parse_skill,
    resolve_profile,
)

# Realistic `skill` JSON, shape taken from a live rtc/config response.
SKILL_H265_MAIN = (
    '{"webrtc":115,"audios":[{"channels":1,"dataBit":16,"codecType":101,'
    '"sampleRate":8000}],"videos":[{"streamType":2,"profileId":"","width":2304,'
    '"codecType":4,"sampleRate":90000,"height":1296},{"streamType":4,'
    '"width":640,"codecType":2,"sampleRate":90000,"height":360}]}'
)

SKILL_ALL_H264 = (
    '{"webrtc":115,"videos":[{"streamType":2,"width":1920,"codecType":2,'
    '"sampleRate":90000,"height":1080},{"streamType":4,"width":640,'
    '"codecType":2,"sampleRate":90000,"height":360}]}'
)

SKILL_H265_ONLY = (
    '{"videos":[{"streamType":2,"width":2304,"codecType":4,'
    '"sampleRate":90000,"height":1296}]}'
)


def run(coro):  # noqa: ANN201
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# skill parsing
# ---------------------------------------------------------------------------


def test_parse_skill_accepts_string_and_object() -> None:
    assert parse_skill(SKILL_ALL_H264)["webrtc"] == 115
    assert parse_skill({"videos": []}) == {"videos": []}


def test_parse_skill_rejects_garbage() -> None:
    with pytest.raises(EzykamProtocolError):
        parse_skill("not json")
    with pytest.raises(EzykamProtocolError):
        parse_skill("")
    with pytest.raises(EzykamProtocolError):
        parse_skill(None)
    with pytest.raises(EzykamProtocolError):
        parse_skill("[1,2]")


# ---------------------------------------------------------------------------
# profile listing / resolution
# ---------------------------------------------------------------------------


def test_list_profiles_decodes_both_streams() -> None:
    profiles = list_profiles({"skill": SKILL_H265_MAIN})
    assert [(p.name, p.codec, p.width, p.height) for p in profiles] == [
        ("main", "HEVC", 2304, 1296),
        ("sub", "H264", 640, 360),
    ]


def test_offer_stream_type_mapping() -> None:
    profiles = {p.name: p for p in list_profiles({"skill": SKILL_ALL_H264})}
    # mainStream(2) -> 0, substream(4) -> 1
    assert profiles["main"].offer_stream_type == 0
    assert profiles["sub"].offer_stream_type == 1


def test_resolve_profile_prefers_h264_over_requested_h265() -> None:
    """The H.265 main stream cannot be decoded by aiortc — must fall back."""
    chosen = resolve_profile({"skill": SKILL_H265_MAIN}, PROFILE_MAIN)
    assert chosen.name == PROFILE_SUB
    assert chosen.is_h264
    assert chosen.width == 640


def test_resolve_profile_honours_h264_main() -> None:
    chosen = resolve_profile({"skill": SKILL_ALL_H264}, PROFILE_MAIN)
    assert chosen.name == PROFILE_MAIN
    assert chosen.codec_type == CODEC_H264
    assert chosen.width == 1920


def test_resolve_profile_honours_explicit_sub() -> None:
    chosen = resolve_profile({"skill": SKILL_ALL_H264}, PROFILE_SUB)
    assert chosen.name == PROFILE_SUB
    assert chosen.height == 360


def test_resolve_profile_raises_when_no_h264_available() -> None:
    with pytest.raises(EzykamProtocolError, match="no H.264"):
        resolve_profile({"skill": SKILL_H265_ONLY}, PROFILE_MAIN)


def test_resolve_profile_raises_when_no_profiles() -> None:
    with pytest.raises(EzykamProtocolError, match="no stream profiles"):
        resolve_profile({"skill": '{"videos":[]}'}, PROFILE_MAIN)


def test_unknown_profile_name_falls_back_to_main() -> None:
    chosen = resolve_profile({"skill": SKILL_ALL_H264}, "bogus")
    assert chosen.name == PROFILE_MAIN


def test_codec_constants_match_portal_source() -> None:
    assert CODEC_H264 == 2
    assert CODEC_HEVC == 4


def test_stream_profile_as_dict() -> None:
    p = StreamProfile(name="sub", stream_type=4, codec_type=2, width=640, height=360)
    assert p.as_dict() == {"name": "sub", "width": 640, "height": 360, "codec": "H264"}


# ---------------------------------------------------------------------------
# SDP candidates
# ---------------------------------------------------------------------------


SDP_SAMPLE = """v=0
o=- 123 2 IN IP4 127.0.0.1
s=-
t=0 0
a=group:BUNDLE 0 1
m=video 9 UDP/TLS/RTP/SAVPF 96
c=IN IP4 0.0.0.0
a=mid:0
a=candidate:1 1 udp 2130706431 192.0.2.10 54321 typ host generation 0
a=candidate:2 1 udp 16777215 192.0.2.1 3478 typ relay raddr 0.0.0.0 rport 0
a=end-of-candidates
m=audio 9 UDP/TLS/RTP/SAVPF 0
a=mid:1
"""


def test_extract_candidates_finds_all_lines() -> None:
    found = extract_candidates(SDP_SAMPLE)
    assert len(found) == 2
    assert all(c.startswith("a=candidate:") for c in found)
    assert "192.0.2.10" in found[0]


def test_extract_candidates_ignores_non_candidate_lines() -> None:
    assert extract_candidates("v=0\na=sendrecv\nm=video 9\n") == []
    assert extract_candidates("") == []


# ---------------------------------------------------------------------------
# ICE servers
# ---------------------------------------------------------------------------


def test_build_ice_servers_from_p2p_config() -> None:
    p2p = {
        "ices": [
            {"urls": "stun:192.0.2.1:3478"},
            {
                "urls": "turn:192.0.2.1:3478",
                "username": "turn-user",
                "credential": "turn-pass",
                "ttl": 36000,
            },
        ]
    }
    servers = build_ice_servers(p2p)
    assert len(servers) == 2
    assert servers[0].urls == "stun:192.0.2.1:3478"
    assert servers[0].username is None
    assert servers[1].username == "turn-user"
    assert servers[1].credential == "turn-pass"


def test_build_ice_servers_tolerates_bad_input() -> None:
    assert build_ice_servers(None) == []
    assert build_ice_servers({}) == []
    assert build_ice_servers({"ices": [{"no_urls": True}, "junk"]}) == []


# ---------------------------------------------------------------------------
# Session router
# ---------------------------------------------------------------------------


def test_router_dispatches_only_to_matching_session() -> None:
    router = SessionRouter()
    seen: list[str] = []

    async def handler_a(_data: dict[str, Any]) -> None:
        seen.append("a")

    async def handler_b(_data: dict[str, Any]) -> None:
        seen.append("b")

    router.register("sess-a", handler_a)
    router.register("sess-b", handler_b)
    assert router.session_ids == ("sess-a", "sess-b")

    async def main() -> None:
        payload = {"protocol": 302, "data": {"header": {"sessionid": "sess-a"}, "msg": {}}}
        await router.dispatch("/av/u/x", payload)
        assert seen == ["a"]

        # A message for an unknown session is dropped, not misrouted.
        await router.dispatch(
            "/av/u/x",
            {"protocol": 302, "data": {"header": {"sessionid": "nope"}, "msg": {}}},
        )
        assert seen == ["a"]

        # Malformed envelopes are ignored rather than raising.
        await router.dispatch("/av/u/x", {"protocol": 302})
        await router.dispatch("/av/u/x", {"protocol": 302, "data": {"header": {}}})
        await router.dispatch("/av/u/x", "not a dict")  # type: ignore[arg-type]
        assert seen == ["a"]

    run(main())


def test_router_unregister_stops_delivery() -> None:
    router = SessionRouter()
    seen: list[str] = []

    async def handler(_data: dict[str, Any]) -> None:
        seen.append("x")

    router.register("s1", handler)
    router.unregister("s1")
    assert router.session_ids == ()

    async def main() -> None:
        await router.dispatch(
            "/t", {"protocol": 302, "data": {"header": {"sessionid": "s1"}}}
        )

    run(main())
    assert seen == []


def test_router_supports_concurrent_sessions() -> None:
    """Two cameras must be independently addressable on one MQTT link."""
    router = SessionRouter()
    hits: list[str] = []

    async def make(name: str):  # noqa: ANN201
        async def h(_d: dict[str, Any]) -> None:
            hits.append(name)

        return h

    async def main() -> None:
        router.register("cam1", await make("cam1"))
        router.register("cam2", await make("cam2"))
        for sid in ("cam1", "cam2", "cam1"):
            await router.dispatch(
                "/t", {"protocol": 302, "data": {"header": {"sessionid": sid}}}
            )

    run(main())
    assert hits == ["cam1", "cam2", "cam1"]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stream_stats_summary() -> None:
    stats = StreamStats(profile={"name": "sub"})
    assert stats.is_connected is False
    assert stats.elapsed_to_answer_ms == 0.0

    stats.offer_sent_at = 100.0
    stats.answer_received_at = 100.25
    stats.connection_state = "connected"
    assert stats.is_connected
    assert stats.elapsed_to_answer_ms == 250.0

    summary = stats.as_dict()
    assert summary["answer_ms"] == 250.0
    assert summary["profile"] == {"name": "sub"}
    assert summary["errors"] == []


# ---------------------------------------------------------------------------
# ICE path reporting (honesty about what is and is not observable)
# ---------------------------------------------------------------------------


def test_describe_ice_path_counts_candidate_types() -> None:
    stats = StreamStats()
    stats.local_candidates = [
        "a=candidate:1 1 udp 2130706431 192.0.2.5 5000 typ host generation 0",
        "a=candidate:2 1 udp 16777215 192.0.2.2 62405 typ relay raddr 0.0.0.0 rport 9",
    ]
    stats.remote_candidates = [
        "candidate:3 1 UDP 1694498815 203.0.113.9 3478 typ srflx\r\n"
    ]

    out = stats.describe_ice_path()
    assert out["local_candidate_types"] == {"host": 1, "relay": 1}
    assert out["remote_candidate_types"] == {"srflx": 1}
    assert out["forced_relay"] is False


def test_describe_ice_path_forced_relay_is_definitive() -> None:
    """Relay-only sessions prove relay by construction."""
    stats = StreamStats()
    stats.local_candidates = [
        "a=candidate:9 1 udp 16777215 192.0.2.2 62405 typ relay raddr 0.0.0.0 rport 9"
    ]
    stats.remote_candidates = [
        "candidate:10 1 UDP 16777215 192.0.2.2 60578 typ relay\r\n"
    ]

    out = stats.describe_ice_path(forced_relay=True)
    assert out["path"] == "turn-relay (forced)"
    assert out["forced_relay"] is True


def test_describe_ice_path_does_not_overclaim_selection() -> None:
    """Presence of relay candidates does not prove they were selected."""
    stats = StreamStats()
    stats.local_candidates = [
        "a=candidate:1 1 udp 2130706431 192.0.2.5 5000 typ host generation 0",
        "a=candidate:2 1 udp 16777215 192.0.2.2 62405 typ relay raddr 0.0.0.0 rport 9",
    ]
    out = stats.describe_ice_path()
    assert out["path"] == "relay-available (selection not observable)"


def test_describe_ice_path_direct_when_no_relay_present() -> None:
    stats = StreamStats()
    stats.local_candidates = [
        "a=candidate:1 1 udp 2130706431 192.0.2.5 5000 typ host generation 0"
    ]
    stats.remote_candidates = [
        "candidate:2 1 UDP 1694498815 203.0.113.9 3478 typ srflx\r\n"
    ]
    assert stats.describe_ice_path()["path"] == "direct-or-stun"


def test_describe_ice_path_unknown_when_no_candidates() -> None:
    assert StreamStats().describe_ice_path()["path"] == "unknown"


# ---------------------------------------------------------------------------
# Remote candidate ordering
# ---------------------------------------------------------------------------


class _FakePc:
    """Minimal stand-in for RTCPeerConnection's candidate surface."""

    def __init__(self, remote_description=None) -> None:
        self.remoteDescription = remote_description
        self.added: list = []

    async def addIceCandidate(self, candidate) -> None:  # noqa: ANN001
        self.added.append(candidate)


def _session() -> WebRtcSession:
    from custom_components.ezykam.core.models import Camera

    return WebRtcSession(
        client=None,  # type: ignore[arg-type]
        mqtt=None,  # type: ignore[arg-type]
        router=SessionRouter(),
        camera=Camera(device_id="be_test", name="Test"),
    )


def test_remote_candidates_buffered_until_remote_description() -> None:
    """aiortc drops/warns on candidates added before the answer exists."""
    session = _session()
    pc = _FakePc(remote_description=None)
    session._pc = pc  # noqa: SLF001 - exercising the buffering path

    async def main() -> None:
        await session._add_remote_candidate(  # noqa: SLF001
            "candidate:1 1 udp 2130706431 192.0.2.1 5000 typ host"
        )
        # Not applied yet, but it must be counted and remembered.
        assert pc.added == []
        assert len(session._pending_remote) == 1  # noqa: SLF001
        assert session.stats.candidates_received == 1

        pc.remoteDescription = object()
        await session._flush_pending_remote()  # noqa: SLF001

    run(main())
    assert len(pc.added) == 1, "buffered candidate was not applied after the answer"
    assert session._pending_remote == []  # noqa: SLF001


def test_candidates_after_answer_are_applied_immediately() -> None:
    session = _session()
    pc = _FakePc(remote_description=object())
    session._pc = pc  # noqa: SLF001

    async def main() -> None:
        await session._add_remote_candidate(  # noqa: SLF001
            "candidate:2 1 udp 1694498815 203.0.113.9 3478 typ srflx"
        )

    run(main())
    assert len(pc.added) == 1
    assert session._pending_remote == []  # noqa: SLF001
    assert session.stats.candidates_received == 1
    # Reaching here at all proves the a= prefix was stripped: candidate_from_sdp
    # asserts the bare "candidate:..." form and would otherwise drop it.
    assert pc.added[0].ip == "203.0.113.9"
    assert pc.added[0].type == "srflx"


def test_flush_with_no_pending_candidates_is_a_noop() -> None:
    session = _session()
    session._pc = _FakePc(remote_description=object())  # noqa: SLF001

    async def main() -> None:
        await session._flush_pending_remote()  # noqa: SLF001

    run(main())
    assert session._pending_remote == []  # noqa: SLF001
