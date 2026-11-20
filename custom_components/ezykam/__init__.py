"""CP Plus Ezykam Cloud — Home Assistant integration.

Cloud-only: no local RTSP, ONVIF, camera IP addresses or port forwarding.

Import structure note
---------------------
Home Assistant imports are deliberately **deferred** (``TYPE_CHECKING`` for
annotations, function-local imports for runtime use). This keeps
``custom_components.ezykam`` importable without Home Assistant installed, so
the protocol library in :mod:`custom_components.ezykam.core` can be unit-tested
standalone on a plain Python interpreter. Annotations remain correct because
``from __future__ import annotations`` is active.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .core.const import DOMAIN, PLATFORMS

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Ezykam account from a config entry."""
    from .coordinator import EzykamDataUpdateCoordinator

    coordinator = EzykamDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Pre-connect the shared MQTT link so the first camera snapshot does not
    # have to pay for it inside Home Assistant's 10s camera image budget.
    # Awaited (and bounded inside) rather than fire-and-forget: on first-ever
    # setup the entry can be ready for snapshots seconds later, and an
    # unawaited task would still be racing them.
    await coordinator.async_warm_up()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and tear down its streams."""
    from .core.const import DOMAIN as _DOMAIN  # local import keeps module import cheap

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[_DOMAIN].pop(entry.entry_id, None)
        if coordinator is not None:
            await coordinator.async_shutdown()
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_id: str
) -> bool:
    """Allow deleting a discovered camera from the device registry."""
    return True
