"""Online/offline status for each Ezykam camera."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .core.const import DOMAIN
from .coordinator import EzykamDataUpdateCoordinator
from .core.models import Camera as CameraModel


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create an online sensor for every discovered camera."""
    coordinator: EzykamDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.data is None:
        return
    async_add_entities(
        EzykamOnlineSensor(coordinator, model) for model in coordinator.data.cameras
    )


class EzykamOnlineSensor(
    CoordinatorEntity[EzykamDataUpdateCoordinator], BinarySensorEntity
):
    """Whether the cloud reports this camera as reachable."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: EzykamDataUpdateCoordinator,
        model: CameraModel,
    ) -> None:
        super().__init__(coordinator)
        self._model = model
        base = model.name or model.device_id
        self._attr_name = f"{base} online"
        self._attr_unique_id = f"{model.device_id}_online"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._model.device_id)},
            name=self._model.name or self._model.device_id,
            manufacturer="CP Plus",
            model="Ezykam",
        )

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        if data is None:
            return False
        current = data.camera_by_id(self._model.device_id)
        if current is not None:
            self._model = current
        return current.online if current else False

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
