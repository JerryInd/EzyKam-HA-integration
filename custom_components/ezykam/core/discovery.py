"""Camera discovery: homes -> devices -> shared devices -> normalised cameras.

Verified against a live account:

* ``customized/common/homeList`` returns homes keyed by ``gid``/``groupId``.
* ``customized/common/roomList`` (``{"homeId": <gid>}``) returns **device**
  records, not rooms. Passing the home's separate ``id`` field yields an empty
  list, so only ``gid``/``groupId`` may be used.
* ``customized/playback/shareList`` returns devices shared *with* this account;
  they carry a ``shareInfo`` field and have a different owner.

Discovery is per-home and failure-tolerant: one unreachable home must not
discard the rest. Session loss is *not* swallowed — it propagates so the caller
can trigger reauthentication.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .client import EzykamClient
from .errors import EzykamApiError, SessionExpiredError
from .models import Camera, Home

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DiscoveryResult:
    """The normalised inventory of one Ezykam account."""

    homes: list[Home] = field(default_factory=list)
    cameras: list[Camera] = field(default_factory=list)

    @property
    def camera_count(self) -> int:
        return len(self.cameras)

    @property
    def online_count(self) -> int:
        return sum(1 for cam in self.cameras if cam.online)

    def camera_by_id(self, device_id: str) -> Camera | None:
        for cam in self.cameras:
            if cam.device_id == device_id:
                return cam
        return None

    def as_dict(self) -> dict[str, Any]:
        """Redaction-safe summary for diagnostics."""
        return {
            "home_count": len(self.homes),
            "camera_count": self.camera_count,
            "online_count": self.online_count,
            "homes": [home.as_dict() for home in self.homes],
            "cameras": [cam.as_dict() for cam in self.cameras],
        }


async def discover(client: EzykamClient) -> DiscoveryResult:
    """Enumerate every camera reachable by *client*, owned and shared.

    Raises:
        SessionExpiredError: the session is no longer accepted (propagates so
            the caller can start reauthentication).
        EzykamApiError: the account-level home list itself failed, which makes
            any further discovery meaningless.
    """
    result = DiscoveryResult()

    # The portal fetches this before touching devices; it decides the
    # Cloud-Type header per device. Best-effort: discovery still works
    # without it because Cloud-Type then falls back to CPPLUS.
    try:
        await client.fetch_dev_id_prefix()
    except SessionExpiredError:
        raise
    except EzykamApiError as exc:
        _LOGGER.debug("devIdPrefix unavailable (%s); using default", exc.error_code)

    homes_raw = await client.homes()
    for payload in homes_raw:
        home = Home.from_payload(payload)
        if home is None:
            _LOGGER.debug("Skipping home payload without a usable gid")
            continue
        result.homes.append(home)

    seen: set[str] = set()

    for home in result.homes:
        try:
            devices_raw = await client.home_devices(home.home_id)
        except SessionExpiredError:
            raise
        except EzykamApiError as exc:
            # One bad home must not lose the whole inventory.
            _LOGGER.warning(
                "Could not list devices for home %s (%s)",
                home.home_id,
                exc.error_code or exc.status,
            )
            continue

        for payload in devices_raw:
            camera = Camera.from_payload(payload, home=home, shared=False)
            if camera is None:
                _LOGGER.debug("Skipping device payload without devId")
                continue
            if camera.device_id in seen:
                continue
            seen.add(camera.device_id)
            result.cameras.append(camera)

    # Shared devices live outside any home of this account.
    try:
        shared_raw = await client.shared_devices()
    except SessionExpiredError:
        raise
    except EzykamApiError as exc:
        _LOGGER.warning("Could not list shared devices (%s)", exc.error_code or exc.status)
        shared_raw = []

    for payload in shared_raw:
        camera = Camera.from_payload(payload, home=None, shared=True)
        if camera is None:
            continue
        if camera.device_id in seen:
            # Already owned by this account; ownership wins over sharing.
            continue
        seen.add(camera.device_id)
        result.cameras.append(camera)

    _LOGGER.debug(
        "Discovered %d camera(s) across %d home(s) (%d online)",
        result.camera_count,
        len(result.homes),
        result.online_count,
    )
    return result


async def discover_with_retry(
    client: EzykamClient, *, delay: float = 2.0, attempts: int = 2
) -> DiscoveryResult:
    """Discover the inventory, retrying on session rejection.

    A single ``USER_SESSION_INVALID`` can be a transient cloud hiccup, and
    treating it as fatal marks the whole config entry failed and demands a QR
    rescan. Requiring *consecutive* failures means only a genuinely dead
    session triggers reauthentication, while a one-off blip recovers quietly.
    """
    last_error: SessionExpiredError | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await discover(client)
        except SessionExpiredError as exc:
            last_error = exc
            if attempt < attempts:
                _LOGGER.warning(
                    "Session rejected during discovery (attempt %d/%d); "
                    "retrying in %.1fs before declaring it expired",
                    attempt,
                    attempts,
                    delay,
                )
                await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error
