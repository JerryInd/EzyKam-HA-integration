"""Data coordinator: discovery plus independent per-camera stream lifecycles.

Design notes

* One MQTT connection is shared by every camera (the protocol allows it); each
  camera gets its own ``WebRtcSession`` demultiplexed by ``sessionid``.
* Streams start **on demand** and are torn down after an idle period. A Pi 4
  cannot afford to decode several 1080p H.264 streams indefinitely, and Home
  Assistant only needs a frame when something actually asks for one.
* Session loss surfaces as ``ConfigEntryAuthFailed`` so HA raises its
  reauthentication flow rather than silently retrying forever.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .core import EzykamClient, SessionExpiredError
from .core.const import (
    CONF_REGION,
    CONF_SESSION,
    CONF_STREAM_PROFILE,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    STREAM_PROFILE_MAIN,
)
from .core.discovery import DiscoveryResult, discover, discover_with_retry
from .core.models import AuthSession
from .core.mqtt import EzykamMqttClient, user_topic
from .core.webrtc import SessionRouter, WebRtcSession

_LOGGER = logging.getLogger(__name__)

#: Idle seconds after which a stream is torn down.
STREAM_IDLE_TIMEOUT = 45.0
#: Ceiling on encoded frames per second while an MJPEG client is attached.
#: Set above the ~10 fps the cameras actually deliver, so the source rate is
#: what limits throughput rather than an arbitrary ceiling here — while still
#: bounding bandwidth/CPU on a Raspberry Pi if a faster camera appears.
MJPEG_FPS = 15.0
#: How long the first camera request waits for a decoded frame. Must stay
#: below Home Assistant's CAMERA_IMAGE_TIMEOUT (10s) so we time out first and
#: can log a useful message instead of HA raising unconditionally.
FIRST_FRAME_TIMEOUT = 8.0
#: Upper bound on the setup-time MQTT pre-connect.
WARM_UP_TIMEOUT = 15.0
#: Delay before re-attempting discovery after a session rejection.
RETRY_DELAY = 2.0
#: The cloud's observed session lifetime (measured: acquired 2026-11-22T06:37Z,
#: rejected 2026-11-25T06:48Z — 3 days, matching the s-sid cookie expiry).
SESSION_LIFETIME_DAYS = 3.0
#: Warn once when a session passes this age.
SESSION_EXPIRY_WARN_DAYS = 2.5
JPEG_QUALITY = 82


class EzykamDataUpdateCoordinator(DataUpdateCoordinator[DiscoveryResult]):
    """Polls the cloud for inventory and owns the live-stream pool."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        stored = entry.data.get(CONF_SESSION) or {}
        auth = AuthSession.from_dict(stored)
        interval = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL_SECONDS)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=int(interval)),
        )
        self.entry = entry
        self.client = EzykamClient(
            session=async_get_clientsession(hass),
            auth=auth,
            region=entry.data.get(CONF_REGION) or auth.region,
        )
        self.stream_profile: str = entry.options.get(
            CONF_STREAM_PROFILE, STREAM_PROFILE_MAIN
        )

        self.router = SessionRouter()
        self._mqtt: EzykamMqttClient | None = None
        #: Set while a broker connection is in flight, so concurrent stream
        #: starts share one connection attempt instead of racing.
        self._mqtt_connecting: asyncio.Event | None = None
        self._streams: dict[str, WebRtcSession] = {}
        self._busy: set[str] = set()
        #: Latest decoded frame per camera, plus a monotonically increasing
        #: sequence number. Frames are held decoded and encoded *on demand*:
        #: the old design encoded at a fixed 1 fps, which is what made the
        #: live view look choppy.
        self._latest_frame: dict[str, Any] = {}
        self._frame_seq: dict[str, int] = {}
        self._last_used: dict[str, float] = {}
        self._expiry_warned = False

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> DiscoveryResult:
        try:
            # discover_with_retry lives in core so it is unit-testable without
            # Home Assistant; it only re-raises after consecutive failures.
            result = await discover_with_retry(self.client, delay=RETRY_DELAY)
        except SessionExpiredError as err:
            # Drives HA's reauthentication dialog instead of silent retries.
            raise ConfigEntryAuthFailed("Ezykam session expired") from err
        except Exception as err:  # noqa: BLE001 - coordinator contract
            raise UpdateFailed(f"Ezykam discovery failed: {type(err).__name__}") from err

        # Runs on every scheduled refresh, which is where idle cleanup belongs.
        await self.async_reap_idle_streams()
        self._warn_if_session_nearing_expiry()
        return result

    def _warn_if_session_nearing_expiry(self) -> None:
        """Say so *before* the session dies, not after.

        The cloud issues sessions with a fixed ~3 day lifetime and offers no
        renewal endpoint, so expiry is inevitable rather than a fault. One
        warning at the halfway-ish mark turns a surprise outage into a heads-up.
        """
        if self._expiry_warned:
            return
        age_days = self.client.auth.age_seconds / 86400.0
        if age_days < SESSION_EXPIRY_WARN_DAYS:
            return
        self._expiry_warned = True
        remaining = SESSION_LIFETIME_DAYS - age_days
        _LOGGER.warning(
            "Ezykam session is %.1f days old; the cloud issues sessions with a "
            "~%.0f day lifetime and provides no renewal endpoint, so "
            "reauthentication will be required in about %.1f day(s)",
            age_days,
            SESSION_LIFETIME_DAYS,
            max(remaining, 0.0),
        )

    @property
    def inventory(self) -> DiscoveryResult | None:
        return self.data

    # ------------------------------------------------------------------
    # mqtt
    # ------------------------------------------------------------------
    async def _ensure_mqtt(self) -> EzykamMqttClient:
        if self._mqtt is not None and self._mqtt.is_connected:
            return self._mqtt
        if self._mqtt is not None:
            await self._mqtt.stop()

        # Concurrent warm-up/stream starts must not race to open two brokers.
        if self._mqtt_connecting is not None:
            await asyncio.shield(self._mqtt_connecting)
            if self._mqtt is not None and self._mqtt.is_connected:
                return self._mqtt

        self._mqtt_connecting = asyncio.Event()
        try:
            creds = await self.client.request("/api/hybrid/getMsid", {})
            if not isinstance(creds, dict) or not creds.get("msid"):
                raise UpdateFailed("Ezykam did not return MQTT credentials")

            mqtt = EzykamMqttClient(
                msid=str(creds["msid"]),
                password=str(creds.get("password") or ""),
                on_message=self.router.dispatch,
            )
            mqtt.subscribe(user_topic(str(creds["msid"])), qos=1)
            await mqtt.start()
            # Store the reference before waiting for the connection: if this
            # coroutine is cancelled (bounded warm-up), the client's own
            # reconnect loop keeps running instead of being orphaned.
            self._mqtt = mqtt
            await mqtt.wait_connected(timeout=30)
            _LOGGER.debug("MQTT link established: %s", mqtt.describe())
            return mqtt
        finally:
            self._mqtt_connecting.set()
            self._mqtt_connecting = None

    async def async_warm_up(self, timeout: float = WARM_UP_TIMEOUT) -> None:
        """Pre-connect MQTT during setup, bounded so setup cannot stall.

        Home Assistant gives a camera request only 10 seconds
        (CAMERA_IMAGE_TIMEOUT). Opening the broker connection up front is what
        keeps the *first* snapshot — the one taken seconds after the entry is
        created — inside that budget.
        """
        try:
            await asyncio.wait_for(self._ensure_mqtt(), timeout=timeout)
            _LOGGER.debug("MQTT pre-connected during setup")
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "MQTT pre-connect did not finish within %.0fs; it will be "
                "retried on first stream",
                timeout,
            )
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort
            _LOGGER.warning(
                "MQTT pre-connect failed (%s); it will be retried on first stream",
                type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # streams
    # ------------------------------------------------------------------
    async def async_ensure_stream(self, device_id: str) -> None:
        """Public entry point: start this camera's stream if not running.

        Used by the MJPEG view, which must not reach into the private helper.
        """
        await self._ensure_stream(device_id)

    async def async_get_image(self, device_id: str) -> bytes | None:
        """Return a JPEG of the live view, starting the stream if needed.

        Home Assistant's ``CameraImageView`` turns a ``None`` return into
        ``HTTPInternalServerError`` ("Camera has no image available"), so the
        first request must not bail out the instant the stream starts — it
        waits for the first decoded frame instead.

        Frames are held decoded and encoded on demand, so there is no fixed
        encode throttle limiting the live view to 1 fps.
        """
        try:
            await self._ensure_stream(device_id)
        except Exception as err:  # noqa: BLE001 - entity must not explode
            _LOGGER.warning(
                "Could not start stream for %s: %s", device_id, type(err).__name__
            )
            return self._encode_jpeg(device_id)

        self.touch(device_id)
        if self._frame_seq.get(device_id, 0) == 0:
            got = await self.async_wait_for_frame(
                device_id, after_seq=0, timeout=FIRST_FRAME_TIMEOUT
            )
            if not got:
                _LOGGER.warning(
                    "No frame decoded for %s within %.0fs",
                    device_id,
                    FIRST_FRAME_TIMEOUT,
                )
                return None
        return self._encode_jpeg(device_id)

    def touch(self, device_id: str) -> None:
        """Record that something still wants this camera's stream."""
        self._last_used[device_id] = time.monotonic()

    async def _ensure_stream(self, device_id: str) -> None:
        if device_id in self._streams or device_id in self._busy:
            self.touch(device_id)
            return
        if self.data is None:
            await self.async_request_refresh()

        camera = self.data.camera_by_id(device_id) if self.data else None
        if camera is None:
            raise LookupError(f"unknown camera {device_id}")
        if not camera.online:
            raise RuntimeError(f"camera {device_id} is offline")

        self._busy.add(device_id)
        try:
            mqtt = await self._ensure_mqtt()
            session = WebRtcSession(
                client=self.client,
                mqtt=mqtt,
                router=self.router,
                camera=camera,
                profile=self.stream_profile,
                on_video_frame=self._make_frame_handler(device_id),
            )
            await session.start()
            self._streams[device_id] = session
            self.touch(device_id)
            _LOGGER.info(
                "Stream started for %s (%s)",
                device_id,
                session.resolved_profile.as_dict() if session.resolved_profile else "?",
            )
        finally:
            self._busy.discard(device_id)

    def _make_frame_handler(self, device_id: str):
        # Per-camera window used to report the *decoded* rate, which is what
        # actually bounds the stream. Without it a low fps cannot be
        # attributed to the source versus the encoder.
        window = {"start": time.monotonic(), "count": 0}

        async def on_frame(frame: Any) -> None:
            # Keep the decoded frame and a sequence number; encode on demand.
            # The previous design encoded (or discarded) on a fixed 1 s
            # throttle, which is what capped the live view at 1 fps.
            self._latest_frame[device_id] = frame
            self._frame_seq[device_id] = self._frame_seq.get(device_id, 0) + 1
            window["count"] += 1
            now = time.monotonic()
            elapsed = now - window["start"]
            if elapsed >= 5.0:
                _LOGGER.info(
                    "decode rate %s: %.1f fps (%d frames in %.1fs)",
                    device_id,
                    window["count"] / elapsed,
                    window["count"],
                    elapsed,
                )
                window["start"] = now
                window["count"] = 0

        return on_frame

    def _encode_jpeg(self, device_id: str) -> bytes | None:
        """Encode the most recent decoded frame as JPEG (None if unavailable)."""
        frame = self._latest_frame.get(device_id)
        if frame is None:
            return None
        try:
            from PIL import Image

            array = frame.to_ndarray(format="rgb24")
            buffer = io.BytesIO()
            Image.fromarray(array).save(buffer, format="JPEG", quality=JPEG_QUALITY)
            return buffer.getvalue()
        except Exception as exc:  # noqa: BLE001 - never break the decoder loop
            _LOGGER.debug("JPEG encode failed for %s: %s", device_id, exc)
            return None

    async def async_wait_for_frame(
        self, device_id: str, *, after_seq: int, timeout: float
    ) -> int:
        """Return once a frame newer than *after_seq* exists (0 on timeout)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            seq = self._frame_seq.get(device_id, 0)
            if seq > after_seq:
                return seq
            await asyncio.sleep(0.05)
        return 0

    async def async_iter_jpegs(self, device_id: str, start_seq: int = 0):
        """Yield ``(jpeg, seq)`` for successive frames, capped at MJPEG_FPS.

        After a frame arrives we sleep out the remainder of the frame interval
        and then encode *that* frame. Skipping to the next frame instead (an
        earlier attempt) discarded up to a full interval of latency per write
        and held throughput below the cap even when the source was faster.

        Ends when no frame arrives for STREAM_IDLE_TIMEOUT, which is how a dead
        stream is detected rather than spinning forever.
        """
        interval = 1.0 / MJPEG_FPS
        seq = start_seq
        last_write = 0.0
        while True:
            new_seq = await self.async_wait_for_frame(
                device_id, after_seq=seq, timeout=STREAM_IDLE_TIMEOUT
            )
            if not new_seq:
                return
            seq = new_seq
            remaining = interval - (time.monotonic() - last_write)
            if remaining > 0:
                await asyncio.sleep(remaining)
            jpeg = self._encode_jpeg(device_id)
            if jpeg is None:
                continue
            last_write = time.monotonic()
            yield jpeg, seq

    async def async_reap_idle_streams(self) -> None:
        """Stop streams nothing has asked for recently."""
        now = time.monotonic()
        for device_id in list(self._streams):
            last = self._last_used.get(device_id, 0.0)
            if now - last < STREAM_IDLE_TIMEOUT:
                continue
            session = self._streams.pop(device_id, None)
            if session is None:
                continue
            try:
                await session.stop()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Stream stop failed for %s: %s", device_id, exc)
            self._latest_frame.pop(device_id, None)
            self._frame_seq.pop(device_id, None)
            _LOGGER.info("Idle stream stopped for %s", device_id)

    def stream_is_active(self, device_id: str) -> bool:
        return device_id in self._streams

    # ------------------------------------------------------------------
    async def async_shutdown(self) -> None:
        """Release every stream and the shared MQTT link."""
        for device_id, session in list(self._streams.items()):
            try:
                await session.stop()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Stream stop failed for %s: %s", device_id, exc)
        self._streams.clear()
        self._latest_frame.clear()
        self._frame_seq.clear()

        if self._mqtt is not None:
            try:
                await self._mqtt.stop()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("MQTT stop failed: %s", exc)
            self._mqtt = None
