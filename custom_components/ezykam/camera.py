"""Camera entities — one per discovered Ezykam camera.

Each entity is independent: it reads its own frame from the coordinator's
stream pool, so cameras start and stop on their own lifecycle rather than as
one coupled group.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .core.const import DOMAIN
from .coordinator import EzykamDataUpdateCoordinator
from .core.models import Camera as CameraModel

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a camera entity for every camera found on the account."""
    coordinator: EzykamDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.data is None:
        _LOGGER.debug("No inventory yet; no camera entities created")
        return
    async_add_entities(
        EzykamCamera(coordinator, model) for model in coordinator.data.cameras
    )


class EzykamCamera(CoordinatorEntity[EzykamDataUpdateCoordinator], Camera):
    """A single cloud camera."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: EzykamDataUpdateCoordinator,
        model: CameraModel,
    ) -> None:
        super().__init__(coordinator)
        Camera.__init__(self)
        self._model = model
        self._attr_name = model.name or model.device_id
        self._attr_unique_id = model.device_id
        self._attr_is_streaming = False

    @property
    def device_info(self) -> DeviceInfo:
        """Group cameras under one account device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._model.device_id)},
            name=self._model.name or self._model.device_id,
            manufacturer="CP Plus",
            model="Ezykam",
            # No via_device: there is no parent account device registered, and
            # the `via_device` tuple form is deprecated (via_device_id in 2027.8).
        )

    @property
    def available(self) -> bool:
        """A camera is available when the coordinator knows it and it is online."""
        if not super().available:
            return False
        current = self._current_model()
        return current is not None and current.online

    def _current_model(self) -> CameraModel | None:
        """Re-read the camera from the latest inventory (names/online change)."""
        data = self.coordinator.data
        if data is None:
            return None
        found = data.camera_by_id(self._model.device_id)
        if found is not None:
            self._model = found
        return found

    @callback
    def _handle_coordinator_update(self) -> None:
        self._current_model()
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "device_id": self._model.device_id,
            "home": self._model.home_name,
            "shared": self._model.shared,
            "product_id": self._model.product_id,
            "stream_profile": self.coordinator.stream_profile,
            "stream_active": self.coordinator.stream_is_active(self._model.device_id),
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the latest live JPEG, starting this camera's stream if needed."""
        self.coordinator.touch(self._model.device_id)
        image = await self.coordinator.async_get_image(self._model.device_id)
        if image is None:
            _LOGGER.debug("No image yet for %s", self._model.device_id)
        return image

    async def handle_async_mjpeg_stream(self, request):  # noqa: ANN001 - aiohttp request
        """Serve a real-time multipart JPEG stream for this camera.

        Home Assistant's ``CameraMjpegStream`` view raises HTTPBadGateway when
        this returns ``None`` (the base implementation), which is why the live
        view produced no video. Frames come straight from the running WebRTC
        session, encoded on demand at MJPEG_FPS.
        """
        from aiohttp import web

        device_id = self._model.device_id
        self.coordinator.touch(device_id)

        try:
            await self.coordinator.async_ensure_stream(device_id)
        except Exception as exc:  # noqa: BLE001 - report, then let HA decide
            _LOGGER.warning(
                "Could not start stream for %s: %s", device_id, type(exc).__name__
            )
            return None

        response = web.StreamResponse(
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=frame",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Connection": "close",
            }
        )
        await response.prepare(request)
        _LOGGER.debug("MJPEG stream started for %s", device_id)

        prefix = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
        try:
            async for jpeg, _seq in self.coordinator.async_iter_jpegs(device_id):
                transport = request.transport
                if transport is None or transport.is_closing():
                    break
                await response.write(
                    prefix
                    + str(len(jpeg)).encode("ascii")
                    + b"\r\n\r\n"
                    + jpeg
                    + b"\r\n"
                )
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as exc:  # noqa: BLE001 - a dropped client is normal
            _LOGGER.debug("MJPEG stream ended for %s: %s", device_id, type(exc).__name__)
        finally:
            _LOGGER.debug("MJPEG stream closed for %s", device_id)

        return response

    async def async_will_remove_from_hass(self) -> None:
        """Stop this camera's stream when the entity goes away."""
        await self.coordinator.async_shutdown()
