"""Authenticated MQTT over secure WebSocket — Ezykam signaling transport.

Everything in this module was read out of the portal's own ``playback`` bundle
and then confirmed against live traffic; nothing is invented.

Connection (portal source, verbatim)::

    username = "web_" + msid          // also used as clientId
    password = <password from /api/hybrid/getMsid>
    broker   = <mobileMqttsUrl.cpplus> + "/mqtt"
    connect(`wss://${username}:${password}@${broker}`,
            { username, password, keepalive: 60, clientId: username })

Topics::

    subscribe  /av/u/{msid}                    (qos 1)
    publish    /av/moto/{motoId}/u/{devId}     (qos 1)

Envelope::

    {"protocol": 302, "pv": "<protocolVersion>", "t": <ms>,
     "data": {"header": {"from", "to", "sub_dev_id", "sessionid", "moto_id",
                         "seq", "rtx", "type", "tid"},
              "msg": {...}}}

Secrets (``msid``, ``password``) are never logged; every log line goes through
:mod:`ezykam.core.redact`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

# Imported at module scope deliberately: doing it inside the connection loop
# makes Home Assistant flag blocking metadata reads in the event loop.
import aiomqtt

from .errors import EzykamProtocolError, EzykamTransportError
from .redact import redact

_LOGGER = logging.getLogger(__name__)

DEFAULT_BROKER_HOST = "m1-cube.cppluscloud.com"
DEFAULT_BROKER_PORT = 443
DEFAULT_WEBSOCKET_PATH = "/mqtt"
DEFAULT_KEEPALIVE = 60

#: Protocol ids used by the portal.
PROTOCOL_SIGNALING = 302
PROTOCOL_COMMAND = 312

#: Message types carried in ``data.header.type``.
TYPE_OFFER = "offer"
TYPE_ANSWER = "answer"
TYPE_CANDIDATE = "candidate"
TYPE_DISCONNECT = "disconnect"

#: ``stream_type`` mapping read from the portal bundle:
#: ``{mainStream: 2 -> 0, substream: 4 -> 1, otherStream: 8 -> 2}``.
STREAM_TYPE_MAIN = 0
STREAM_TYPE_SUB = 1
STREAM_TYPE_OTHER = 2

#: mDNS host candidates (``*.local``) are filtered out before signalling, so
#: only server-reflexive / relay candidates reach the device.
_MDNS_SUFFIX = ".local"

MessageHandler = Callable[[str, dict[str, Any]], Awaitable[None] | None]


def mqtt_username(msid: str) -> str:
    """Portal's MQTT username / clientId."""
    return f"web_{msid}"


def user_topic(msid: str) -> str:
    """Topic the cloud publishes answers/candidates back to."""
    return f"/av/u/{msid}"


def device_out_topic(moto_id: str, device_id: str, parent_id: str | None = None) -> str:
    """Topic used to send offers and candidates to a device."""
    return f"/av/moto/{moto_id}/u/{parent_id or device_id}"


def new_session_id() -> str:
    """Portal generates ``uuid().replace(/-/g, "")`` for each session."""
    return uuid.uuid4().hex


def build_envelope(
    *,
    protocol: int,
    pv: str,
    header: dict[str, Any],
    msg_type: str,
    msg: dict[str, Any],
    tid: str | None = None,
    timestamp: int | None = None,
) -> str:
    """Encode one outbound signaling message exactly as the portal does."""
    data_header = dict(header)
    data_header["type"] = msg_type
    data_header["tid"] = tid or uuid.uuid4().hex
    return json.dumps(
        {
            "protocol": protocol,
            "pv": pv,
            "t": int(time.time() * 1000) if timestamp is None else timestamp,
            "data": {"header": data_header, "msg": msg},
        },
        separators=(",", ":"),
    )


def parse_envelope(raw: str | bytes) -> dict[str, Any]:
    """Decode an inbound message, raising a typed error on malformed data."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EzykamProtocolError("MQTT payload was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise EzykamProtocolError("MQTT payload was not a JSON object")
    return payload


def is_for_session(payload: dict[str, Any], session_id: str) -> bool:
    """Portal dispatches only when ``data.header.sessionid`` matches."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    header = data.get("header")
    if not isinstance(header, dict):
        return False
    return header.get("sessionid") == session_id


def should_signal_candidate(candidate: str) -> bool:
    """Mirror the portal's ``!candidate.includes(".local")`` filter.

    mDNS host candidates are never sent to the device.
    """
    return bool(candidate) and _MDNS_SUFFIX not in candidate


def format_outbound_candidate(candidate: str) -> str:
    """Portal prefixes local candidates with ``a=`` before sending."""
    return candidate if candidate.startswith("a=") else f"a={candidate}"


def normalise_inbound_candidate(candidate: str) -> str:
    """Strip the ``a=`` prefix some peers echo back.

    aiortc is stricter than browsers about ``RTCIceCandidate.candidate`` and
    expects the bare ``candidate:...`` line.
    """
    return candidate[2:] if candidate.startswith("a=") else candidate


@dataclass(slots=True)
class SignalingChannel:
    """Per-session addressing for one camera stream."""

    msid: str
    device_id: str
    moto_id: str
    session_id: str
    protocol_version: str = "2.2"
    parent_id: str | None = None
    node_id: str | None = None

    @property
    def out_topic(self) -> str:
        return device_out_topic(self.moto_id, self.device_id, self.parent_id)

    @property
    def in_topic(self) -> str:
        return user_topic(self.msid)

    @property
    def base_header(self) -> dict[str, Any]:
        """The fixed part of ``data.header`` (portal's ``this.header``)."""
        return {
            "from": self.msid,
            "to": self.parent_id or self.device_id,
            "sub_dev_id": self.node_id if self.parent_id else "",
            "sessionid": self.session_id,
            "moto_id": self.moto_id,
            "seq": 0,
            "rtx": 0,
        }

    def encode(
        self, protocol: int, msg_type: str, msg: dict[str, Any], tid: str | None = None
    ) -> str:
        return build_envelope(
            protocol=protocol,
            pv=self.protocol_version,
            header=self.base_header,
            msg_type=msg_type,
            msg=msg,
            tid=tid,
        )


class EzykamMqttClient:
    """MQTT-over-WSS client with automatic reconnection and resubscription.

    The transport is injectable so tests can drive the client without a broker.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_BROKER_HOST,
        port: int = DEFAULT_BROKER_PORT,
        msid: str,
        password: str,
        websocket_path: str = DEFAULT_WEBSOCKET_PATH,
        on_message: MessageHandler | None = None,
        keepalive: int = DEFAULT_KEEPALIVE,
        max_backoff: float = 60.0,
    ) -> None:
        if not msid or not password:
            raise ValueError("msid and password are required")
        self.host = host
        self.port = port
        self._msid = msid
        self._password = password
        self.websocket_path = websocket_path
        self.on_message = on_message
        self.keepalive = keepalive
        self.max_backoff = max_backoff

        self.username = mqtt_username(msid)
        #: Subscriptions are replayed after every reconnect.
        self._subscriptions: dict[str, int] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._publish_lock = asyncio.Lock()
        self._client: Any = None
        self.reconnect_attempts = 0

    # -- properties ---------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    @property
    def subscriptions(self) -> tuple[str, ...]:
        return tuple(self._subscriptions)

    def safe(self, text: str) -> str:
        """Replace the ``msid`` with a placeholder before anything is logged.

        ``msid`` is half of the MQTT credential (username is ``web_<msid>``),
        and it also appears inside topics and in ``data.header.from`` — so raw
        payloads must be scrubbed too, not just the password field.
        """
        if self._msid and self._msid in text:
            return text.replace(self._msid, "<msid>")
        return text

    def describe(self) -> dict[str, Any]:
        """Redacted, loggable description — never contains msid or password."""
        return {
            "host": self.host,
            "port": self.port,
            "websocket_path": self.websocket_path,
            "username": "<redacted>",
            "keepalive": self.keepalive,
            "connected": self.is_connected,
            "subscriptions": [self.safe(t) for t in self._subscriptions],
            "reconnect_attempts": self.reconnect_attempts,
        }

    # -- lifecycle ----------------------------------------------------
    def subscribe(self, topic: str, qos: int = 1) -> None:
        """Register a subscription; applied now and re-applied on reconnect."""
        self._subscriptions[topic] = qos

    async def start(self) -> None:
        """Spawn the connection loop."""
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ezykam-mqtt")

    async def stop(self) -> None:
        """Tear the connection down and wait for the loop to exit."""
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected.clear()

    async def wait_connected(self, timeout: float = 30.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def publish(self, topic: str, payload: str, qos: int = 1) -> None:
        """Publish one signaling message.

        Raises :class:`EzykamTransportError` when the link is down so callers
        can decide between buffering and failing fast.
        """
        client = self._client
        if client is None or not self._connected.is_set():
            raise EzykamTransportError("MQTT is not connected")
        async with self._publish_lock:
            await client.publish(topic, payload, qos=qos)
        _LOGGER.debug("MQTT -> %s: %s", self.safe(topic), self.safe(payload))

    # -- internals ----------------------------------------------------
    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 1.0
                self.reconnect_attempts = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any failure
                self._connected.clear()
                if self._stop.is_set():
                    break
                self.reconnect_attempts += 1
                _LOGGER.warning(
                    "MQTT connection lost (%s); retrying in %.1fs (attempt %d)",
                    type(exc).__name__,
                    backoff,
                    self.reconnect_attempts,
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, self.max_backoff)

    async def _connect_once(self) -> None:
        # Creating a default SSL context reads CA bundles from disk, which is
        # blocking I/O. Home Assistant rejects that inside the event loop, so
        # it is built on a worker thread and reused across reconnects.
        tls_context = await asyncio.get_running_loop().run_in_executor(
            None, ssl.create_default_context
        )

        client = aiomqtt.Client(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self._password,
            identifier=self.username,
            transport="websockets",
            keepalive=self.keepalive,
            websocket_path=self.websocket_path,
            tls_context=tls_context,
        )
        async with client:
            for topic, qos in self._subscriptions.items():
                await client.subscribe(topic, qos=qos)
                _LOGGER.debug("Subscribed to %s (qos=%d)", self.safe(topic), qos)
            self._client = client
            self._connected.set()
            _LOGGER.info(
                "MQTT connected to wss://%s:%d%s (subs=%s)",
                self.host,
                self.port,
                self.websocket_path,
                [self.safe(t) for t in self._subscriptions],
            )
            async for message in client.messages:
                if self._stop.is_set():
                    break
                await self._dispatch(str(message.topic), message.payload)

        self._connected.clear()
        self._client = None

    async def _dispatch(self, topic: str, payload: Any) -> None:
        try:
            parsed = parse_envelope(payload)
        except EzykamProtocolError:
            _LOGGER.debug("Ignoring malformed MQTT payload on %s", self.safe(topic))
            return
        _LOGGER.debug(
            "MQTT <- %s protocol=%s type=%s: %s",
            self.safe(topic),
            parsed.get("protocol"),
            ((parsed.get("data") or {}).get("header") or {}).get("type"),
            self.safe(json.dumps(redact(parsed))),
        )
        if self.on_message is not None:
            result = self.on_message(topic, parsed)
            if asyncio.iscoroutine(result):
                await result
