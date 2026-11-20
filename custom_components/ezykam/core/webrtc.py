"""WebRTC live-view sessions for one camera at a time (Phase 5/6).

Protocol source: the portal's ``playback`` bundle. Nothing here is invented.

Pipeline::

    rtc/config ──► ice servers + auth + motoId + protocolVersion + skill
    hybrid/getMsid ──► msid + password ──► MQTT over WSS (shared)
    subscribe /av/u/{msid}
    publish   /av/moto/{motoId}/u/{devId}
        offer  {sdp, auth, mode:"webrtc", datachannel_enable,
                token:<ices>, stream_type, replay:{is_replay:0}}
        candidate {candidate:"a=...", mode:"webrtc"}
    inbound (protocol 302 + header.sessionid match)
        answer    {sdp}
        candidate {candidate}
        disconnect {sdk_code|info}

Codec note: the portal maps ``codecType 2 -> H.264`` and ``4 -> H.265/HEVC``,
and its HEVC path runs a WASM FMP4 pipeline over a data channel. aiortc/PyAV
has no H.265 decoder here, so sessions **prefer an H.264 stream** and fall back
with a warning rather than silently producing no video.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from .client import EzykamClient
from .errors import EzykamProtocolError, EzykamTransportError
from .models import Camera
from .mqtt import (
    PROTOCOL_SIGNALING,
    TYPE_ANSWER,
    TYPE_CANDIDATE,
    TYPE_DISCONNECT,
    TYPE_OFFER,
    EzykamMqttClient,
    SignalingChannel,
    format_outbound_candidate,
    new_session_id,
    normalise_inbound_candidate,
    should_signal_candidate,
)

_LOGGER = logging.getLogger(__name__)

PROFILE_MAIN = "main"
PROFILE_SUB = "sub"

#: ``skill.videos[].streamType`` values (portal ``STREAM_TYPE`` enum).
_SKILL_STREAM_MAIN = 2
_SKILL_STREAM_SUB = 4

#: ``skill.videos[].codecType`` values, read from portal source.
CODEC_H264 = 2
CODEC_HEVC = 4

#: Offer ``stream_type`` mapping: mainStream(2)->0, substream(4)->1.
_OFFER_STREAM_TYPE = {_SKILL_STREAM_MAIN: 0, _SKILL_STREAM_SUB: 1}

DEFAULT_CONNECT_TIMEOUT = 30.0
_ANSWER_TIMEOUT = 20.0

_CANDIDATE_TYPE_RE = re.compile(r"\btyp\s+(\w+)")


def _count_candidate_types(candidates: list[str]) -> dict[str, int]:
    """Tally ``host`` / ``srflx`` / ``relay`` entries in SDP candidate lines."""
    counts: dict[str, int] = {}
    for line in candidates:
        match = _CANDIDATE_TYPE_RE.search(line)
        key = match.group(1) if match else "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


@dataclass(slots=True)
class StreamProfile:
    """One resolved entry from the camera's ``skill`` JSON."""

    name: str
    stream_type: int
    codec_type: int
    width: int
    height: int

    @property
    def codec(self) -> str:
        return "H264" if self.codec_type == CODEC_H264 else "HEVC"

    @property
    def is_h264(self) -> bool:
        return self.codec_type == CODEC_H264

    @property
    def offer_stream_type(self) -> int:
        return _OFFER_STREAM_TYPE.get(self.stream_type, 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "codec": self.codec,
        }


def parse_skill(skill: Any) -> dict[str, Any]:
    """``skill`` arrives as a JSON *string*; tolerate it already being an object."""
    if isinstance(skill, dict):
        return skill
    if isinstance(skill, str) and skill:
        try:
            parsed = json.loads(skill)
        except json.JSONDecodeError as exc:
            raise EzykamProtocolError("rtc config 'skill' was not valid JSON") from exc
        if isinstance(parsed, dict):
            return parsed
    raise EzykamProtocolError("rtc config did not contain a usable 'skill'")


def list_profiles(rtc_config: Mapping[str, Any] | Any) -> list[StreamProfile]:
    """Extract every stream profile advertised by the camera."""
    skill = parse_skill(rtc_config.get("skill") if isinstance(rtc_config, dict) else None)
    videos = skill.get("videos") or []
    profiles: list[StreamProfile] = []
    for entry in videos:
        if not isinstance(entry, dict):
            continue
        stream_type = entry.get("streamType")
        if stream_type == _SKILL_STREAM_MAIN:
            name = PROFILE_MAIN
        elif stream_type == _SKILL_STREAM_SUB:
            name = PROFILE_SUB
        else:
            continue
        profiles.append(
            StreamProfile(
                name=name,
                stream_type=int(stream_type),
                codec_type=int(entry.get("codecType", CODEC_H264)),
                width=int(entry.get("width", 0)),
                height=int(entry.get("height", 0)),
            )
        )
    return profiles


def resolve_profile(
    rtc_config: Any, requested: str = PROFILE_MAIN, *, require_h264: bool = True
) -> StreamProfile:
    """Pick the profile to stream, preferring an H.264 one.

    ``requested`` is honoured when it can actually be decoded; otherwise we
    fall back to an H.264 profile and log why. Raises if nothing is usable, so
    a mis-selected profile fails loudly instead of showing a black image.
    """
    profiles = list_profiles(rtc_config)
    if not profiles:
        raise EzykamProtocolError("camera advertised no stream profiles")

    by_name = {p.name: p for p in profiles}
    # `requested` wins when present, then main, then whatever is advertised.
    chosen = by_name.get(requested) or by_name.get(PROFILE_MAIN) or profiles[0]

    if require_h264 and not chosen.is_h264:
        fallback = next((p for p in profiles if p.is_h264), None)
        if fallback is None:
            raise EzykamProtocolError(
                f"camera offers no H.264 stream (profiles="
                f"{[p.as_dict() for p in profiles]})"
            )
        _LOGGER.warning(
            "Requested %s stream is %s (%dx%d) which this player cannot decode; "
            "falling back to %s (%dx%d)",
            chosen.name,
            chosen.codec,
            chosen.width,
            chosen.height,
            fallback.name,
            fallback.width,
            fallback.height,
        )
        chosen = fallback
    return chosen


def build_ice_servers(p2p_config: Any) -> list[Any]:
    """Convert ``p2pConfig.ices`` into aiortc ``RTCIceServer`` objects.

    TURN ``username``/``credential`` are read here (they are required to
    connect) but are never logged — see :mod:`ezykam.core.redact`.
    """
    from aiortc import RTCIceServer

    servers: list[RTCIceServer] = []
    if not isinstance(p2p_config, dict):
        return servers
    for entry in p2p_config.get("ices") or []:
        if not isinstance(entry, dict):
            continue
        urls = entry.get("urls") or entry.get("url")
        if not urls:
            continue
        servers.append(
            RTCIceServer(
                urls=urls,
                username=entry.get("username"),
                credential=entry.get("credential"),
            )
        )
    return servers


def extract_candidates(sdp: str) -> list[str]:
    """Pull ``a=candidate:`` lines out of a local SDP.

    aiortc is non-trickle (it gathers everything during ``setLocalDescription``)
    and emits no ``icecandidate`` events, so candidates are recovered from the
    SDP and then signalled separately — mirroring what the portal does.
    """
    out: list[str] = []
    for line in sdp.splitlines():
        line = line.strip()
        if line.startswith("a=candidate:"):
            out.append(line)
    return out


@dataclass(slots=True)
class StreamStats:
    """Observable outcome of one session — used for diagnostics and tests."""

    profile: dict[str, Any] = field(default_factory=dict)
    ice_connection_state: str = "new"
    connection_state: str = "new"
    video_frames: int = 0
    audio_frames: int = 0
    last_width: int = 0
    last_height: int = 0
    offer_sent_at: float = 0.0
    answer_received_at: float = 0.0
    connected_at: float = 0.0
    candidates_sent: int = 0
    candidates_received: int = 0
    local_candidates: list[str] = field(default_factory=list)
    remote_candidates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_connected(self) -> bool:
        return self.connection_state == "connected"

    @property
    def elapsed_to_answer_ms(self) -> float:
        if not self.offer_sent_at or not self.answer_received_at:
            return 0.0
        return (self.answer_received_at - self.offer_sent_at) * 1000

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "ice_connection_state": self.ice_connection_state,
            "connection_state": self.connection_state,
            "video_frames": self.video_frames,
            "audio_frames": self.audio_frames,
            "last_width": self.last_width,
            "last_height": self.last_height,
            "answer_ms": round(self.elapsed_to_answer_ms, 1),
            "candidates_sent": self.candidates_sent,
            "candidates_received": self.candidates_received,
            "errors": list(self.errors),
        }

    def describe_ice_path(self, *, forced_relay: bool = False) -> dict[str, Any]:
        """Report what the ICE exchange actually offered, honestly.

        Candidate *presence* is observable; the candidate pair ICE ultimately
        *selects* is not exposed by aiortc. So this reports the inventory and
        only claims "relay" when the session was deliberately restricted to
        relay — in which case every candidate is a relay candidate by
        construction and the claim is sound.
        """
        local = _count_candidate_types(self.local_candidates)
        remote = _count_candidate_types(self.remote_candidates)

        if forced_relay:
            path = "turn-relay (forced)"
        elif local.get("relay") or remote.get("relay"):
            path = "relay-available (selection not observable)"
        elif local or remote:
            path = "direct-or-stun"
        else:
            path = "unknown"

        return {
            "path": path,
            "forced_relay": forced_relay,
            "local_candidate_types": local,
            "remote_candidate_types": remote,
            "local_candidates": list(self.local_candidates),
            "remote_candidates": list(self.remote_candidates),
        }


class SessionRouter:
    """Demultiplex inbound MQTT messages to per-camera sessions.

    One MQTT connection serves every camera: each session registers its
    ``sessionid`` and only receives messages addressed to it, exactly as the
    portal filters on ``data.header.sessionid``.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}

    def register(self, session_id: str, handler: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._sessions[session_id] = handler

    def unregister(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    @property
    def session_ids(self) -> tuple[str, ...]:
        return tuple(self._sessions)

    async def dispatch(self, topic: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        data = payload.get("data")
        if not isinstance(data, dict):
            return
        header = data.get("header")
        if not isinstance(header, dict):
            return
        session_id = header.get("sessionid")
        handler = self._sessions.get(str(session_id)) if session_id else None
        if handler is None:
            return
        await handler(data)


class WebRtcSession:
    """One live view of one camera."""

    def __init__(
        self,
        *,
        client: EzykamClient,
        mqtt: EzykamMqttClient,
        router: SessionRouter,
        camera: Camera,
        profile: str = PROFILE_MAIN,
        on_video_frame: Callable[[Any], Awaitable[None] | None] | None = None,
        ice_transport_policy: str | None = None,
    ) -> None:
        self.client = client
        self.mqtt = mqtt
        self.router = router
        self.camera = camera
        self.requested_profile = profile
        self.on_video_frame = on_video_frame
        #: ``"relay"`` forces traffic through TURN — used to prove that the
        #: cloud relay path works rather than assuming it does.
        self.ice_transport_policy = ice_transport_policy

        self.session_id = new_session_id()
        self.stats = StreamStats()
        self._pc: Any = None
        self._channel: SignalingChannel | None = None
        self._resolved: StreamProfile | None = None
        self._answer_event = asyncio.Event()
        self._answer_sdp: str | None = None
        self._tracks: list[Any] = []
        #: Remote candidates that arrived before the answer did. aiortc refuses
        #: addIceCandidate without a remote description, so they are buffered.
        self._pending_remote: list[str] = []
        self._started = False

    # ------------------------------------------------------------------
    @property
    def resolved_profile(self) -> StreamProfile | None:
        return self._resolved

    # ------------------------------------------------------------------
    async def start(self, *, connect_timeout: float = DEFAULT_CONNECT_TIMEOUT) -> StreamStats:
        """Fetch config, connect signaling, negotiate and wait for media."""
        from .compat import ensure_lenient_dtls_identity

        # Must be in place before the peer connection performs its DTLS
        # handshake; see core.compat for why this is needed.
        ensure_lenient_dtls_identity()

        from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription

        rtc_config = await self.client.rtc_config(self.camera.device_id)
        if not isinstance(rtc_config, dict):
            raise EzykamProtocolError("rtc/config did not return an object")

        self._resolved = resolve_profile(rtc_config, self.requested_profile)
        self.stats.profile = self._resolved.as_dict()

        p2p = rtc_config.get("p2pConfig") or {}
        moto_id = str(p2p.get("motoId") or "")
        if not moto_id:
            raise EzykamProtocolError("rtc/config did not include p2pConfig.motoId")
        auth = p2p.get("auth") or rtc_config.get("auth")
        if not auth:
            raise EzykamProtocolError("rtc/config did not include an auth token")

        msid = self.mqtt.username.removeprefix("web_")
        self._channel = SignalingChannel(
            msid=msid,
            device_id=self.camera.device_id,
            moto_id=moto_id,
            session_id=self.session_id,
            protocol_version=str(rtc_config.get("protocolVersion") or "2.2"),
        )
        self.router.register(self.session_id, self._handle_signaling)

        from aiortc.rtcconfiguration import RTCIceTransportPolicy

        config_kwargs: dict[str, Any] = {}
        if self.ice_transport_policy:
            # aiortc expects the enum member, not the bare string.
            config_kwargs["iceTransportPolicy"] = RTCIceTransportPolicy(
                self.ice_transport_policy
            )
        pc = RTCPeerConnection(
            configuration=RTCConfiguration(
                iceServers=build_ice_servers(p2p), **config_kwargs
            )
        )
        self._pc = pc

        @pc.on("connectionstatechange")
        async def _on_conn_state() -> None:  # pragma: no cover - event driven
            self.stats.connection_state = pc.connectionState
            _LOGGER.debug("pc connectionState=%s", pc.connectionState)

        @pc.on("iceconnectionstatechange")
        async def _on_ice_state() -> None:  # pragma: no cover - event driven
            self.stats.ice_connection_state = pc.iceConnectionState
            _LOGGER.debug("pc iceConnectionState=%s", pc.iceConnectionState)

        @pc.on("track")
        async def _on_track(track: Any) -> None:  # pragma: no cover - event driven
            self._tracks.append(track)
            kind = getattr(track, "kind", "?")
            _LOGGER.info("Track received: %s (%s)", kind, getattr(track, "codec", None))
            if kind == "video":
                self._consume_video(track)
            else:
                self._consume_audio(track)

        # Portal's H.264 transceiver layout: audio sendrecv, video recvonly.
        pc.addTransceiver("audio", direction="sendrecv")
        pc.addTransceiver("video", direction="recvonly")

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        sdp = pc.localDescription.sdp

        await self._send_offer(sdp, auth=str(auth), p2p=p2p)
        self._send_candidates_from_sdp(sdp)

        self._started = True
        try:
            await asyncio.wait_for(self._answer_event.wait(), timeout=_ANSWER_TIMEOUT)
        except asyncio.TimeoutError as exc:
            self.stats.errors.append("no answer within timeout")
            raise EzykamTransportError(
                f"No SDP answer from {self.camera.device_id} within {_ANSWER_TIMEOUT}s"
            ) from exc

        assert self._answer_sdp is not None
        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=self._answer_sdp, type="answer")
        )
        await self._flush_pending_remote()

        await self._wait_connected(connect_timeout)
        return self.stats

    async def _send_offer(self, sdp: str, *, auth: str, p2p: Mapping[str, Any]) -> None:
        assert self._channel is not None
        profile = self._resolved
        assert profile is not None
        msg = {
            "sdp": sdp,
            "auth": auth,
            "mode": "webrtc",
            "datachannel_enable": profile.codec == "HEVC",
            "token": p2p.get("ices") or [],
            "stream_type": profile.offer_stream_type,
            "replay": {"is_replay": 0},
        }
        payload = self._channel.encode(PROTOCOL_SIGNALING, TYPE_OFFER, msg)
        await self.mqtt.publish(self._channel.out_topic, payload, qos=1)
        self.stats.offer_sent_at = time.monotonic()
        _LOGGER.info(
            "Offer sent for %s (%s %dx%d, stream_type=%d)",
            self.camera.device_id,
            profile.codec,
            profile.width,
            profile.height,
            profile.offer_stream_type,
        )

    def _send_candidates_from_sdp(self, sdp: str) -> None:
        """Mirror the portal's trickle behaviour from our gathered SDP."""
        if self._channel is None:
            return
        self.stats.local_candidates = extract_candidates(sdp)

        async def _send_all() -> None:
            for line in self.stats.local_candidates:
                if not should_signal_candidate(line):
                    continue
                msg = {"candidate": format_outbound_candidate(line), "mode": "webrtc"}
                payload = self._channel.encode(  # type: ignore[union-attr]
                    PROTOCOL_SIGNALING, TYPE_CANDIDATE, msg
                )
                try:
                    await self.mqtt.publish(self._channel.out_topic, payload, qos=1)  # type: ignore[union-attr]
                    self.stats.candidates_sent += 1
                except EzykamTransportError:
                    return

        asyncio.create_task(_send_all())

    # ------------------------------------------------------------------
    async def _handle_signaling(self, data: dict[str, Any]) -> None:
        header = data.get("header") or {}
        msg = data.get("msg") or {}
        msg_type = header.get("type")

        if msg_type == TYPE_ANSWER:
            sdp = msg.get("sdp") if isinstance(msg, dict) else None
            if not isinstance(sdp, str) or not sdp:
                _LOGGER.warning("Answer carried no sdp")
                return
            self._answer_sdp = sdp
            self.stats.answer_received_at = time.monotonic()
            self._answer_event.set()
            _LOGGER.debug("SDP answer received (%d bytes)", len(sdp))
            return

        if msg_type == TYPE_CANDIDATE:
            raw = msg.get("candidate") if isinstance(msg, dict) else None
            if isinstance(raw, str) and raw:
                await self._add_remote_candidate(raw)
            return

        if msg_type == TYPE_DISCONNECT:
            info = msg.get("info") if isinstance(msg, dict) else None
            code = msg.get("sdk_code") if isinstance(msg, dict) else None
            if code == 12 or info == "refused":
                self.stats.errors.append("session overflow (refused by cloud)")
                _LOGGER.warning("Cloud refused the session (session overflow)")
            else:
                self.stats.errors.append(f"device disconnect code={code}")
                _LOGGER.info("Device sent disconnect (code=%s)", code)
            return

        _LOGGER.debug("Ignoring signaling message type=%s", msg_type)

    async def _add_remote_candidate(self, raw: str) -> None:
        if self._pc is None:
            return
        self.stats.candidates_received += 1
        self.stats.remote_candidates.append(normalise_inbound_candidate(raw))

        # The device may trickle candidates before the answer lands; aiortc
        # logs a warning (and drops them) until a remote description exists.
        if self._pc.remoteDescription is None:
            self._pending_remote.append(raw)
            _LOGGER.debug(
                "Buffering remote candidate until the answer is applied (%d pending)",
                len(self._pending_remote),
            )
            return

        await self._apply_remote_candidate(raw)

    async def _apply_remote_candidate(self, raw: str) -> None:
        if self._pc is None:
            return
        from aiortc.sdp import candidate_from_sdp

        try:
            sdp_line = normalise_inbound_candidate(raw)
            candidate = candidate_from_sdp(sdp_line)
            candidate.sdpMid = "0"
            candidate.sdpMLineIndex = 0
            await self._pc.addIceCandidate(candidate)
        except Exception as exc:  # noqa: BLE001 - one bad candidate must not kill the stream
            _LOGGER.debug("Dropping unusable remote candidate: %s", type(exc).__name__)

    async def _flush_pending_remote(self) -> None:
        """Apply candidates that arrived before the remote description."""
        pending, self._pending_remote = self._pending_remote, []
        for raw in pending:
            await self._apply_remote_candidate(raw)
        if pending:
            _LOGGER.debug("Applied %d buffered remote candidate(s)", len(pending))

    # ------------------------------------------------------------------
    async def _wait_connected(self, timeout: float) -> None:
        assert self._pc is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._pc.connectionState
            if state == "connected":
                self.stats.connected_at = time.monotonic()
                self.stats.connection_state = state
                return
            if state == "failed":
                raise EzykamTransportError(
                    f"WebRTC connection to {self.camera.device_id} failed"
                )
            await asyncio.sleep(0.1)
        raise EzykamTransportError(
            f"WebRTC did not connect within {timeout:.0f}s "
            f"(state={self._pc.connectionState}, "
            f"ice={self._pc.iceConnectionState})"
        )

    def _consume_video(self, track: Any) -> None:
        async def _run() -> None:
            try:
                # aiortc tracks are NOT async iterators: they expose recv().
                while True:
                    frame = await track.recv()
                    self.stats.video_frames += 1
                    self.stats.last_width = frame.width
                    self.stats.last_height = frame.height
                    if self.on_video_frame is not None:
                        result = self.on_video_frame(frame)
                        if asyncio.iscoroutine(result):
                            await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - end-of-stream etc.
                self.stats.errors.append(
                    f"video track ended: {type(exc).__name__}: {exc}"
                )
                _LOGGER.debug("Video track ended: %s: %s", type(exc).__name__, exc)

        asyncio.create_task(_run())

    def _consume_audio(self, track: Any) -> None:
        async def _run() -> None:
            try:
                while True:
                    await track.recv()
                    self.stats.audio_frames += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Audio track ended: %s: %s", type(exc).__name__, exc)
                return

        asyncio.create_task(_run())

    # ------------------------------------------------------------------
    async def stop(self) -> None:
        """Tear down signaling and the peer connection."""
        if self._channel is not None:
            self.router.unregister(self.session_id)
            try:
                msg = {"type": "disconnect", "reason": 4}
                payload = self._channel.encode(
                    PROTOCOL_SIGNALING, TYPE_DISCONNECT, msg
                )
                await self.mqtt.publish(self._channel.out_topic, payload, qos=1)
            except Exception:  # noqa: BLE001 - best effort on the way out
                pass
            self._channel = None

        pc = self._pc
        self._pc = None
        if pc is not None:
            try:
                await pc.close()
            except Exception:  # noqa: BLE001
                pass
        self._started = False
        _LOGGER.debug("Session %s stopped", self.session_id)
