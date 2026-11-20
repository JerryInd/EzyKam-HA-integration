"""Diagnostics download for support, with every secret redacted.

The whole payload goes through :func:`ezykam.core.redact`, which masks by key
name *and* scrubs signed-URL query strings, so session cookies, MQTT
credentials, TURN credentials and presigned S3 signatures cannot appear.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .core.const import CONF_SESSION, DOMAIN
from .core.models import fingerprint
from .core.redact import redact
from .coordinator import EzykamDataUpdateCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return a redacted diagnostics dump for one Ezykam account."""
    coordinator: EzykamDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )

    stored_session = entry.data.get(CONF_SESSION) or {}

    payload: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "region": entry.data.get("region"),
            "options": dict(entry.options),
            # The unique_id IS the account uid, which this module treats as
            # sensitive everywhere else - expose a stable fingerprint instead
            # of the raw value so the two never disagree.
            "unique_id_fingerprint": fingerprint(entry.unique_id)
            if entry.unique_id
            else None,
            # Deliberately not dumping entry.data wholesale: it holds the live
            # session. Only its shape is reported.
            "session_present": bool(stored_session),
            "session_fields": sorted(stored_session),
        }
    }

    if coordinator is None:
        payload["coordinator"] = None
        return redact(payload)

    payload["coordinator"] = {
        "stream_profile": coordinator.stream_profile,
        "mqtt": coordinator._mqtt.describe() if coordinator._mqtt else None,  # noqa: SLF001
        "active_streams": sorted(
            device_id
            for device_id in getattr(coordinator, "_streams", {})
        ),
        "inventory": coordinator.data.as_dict() if coordinator.data else None,
        "session": repr(coordinator.client.auth),
        "stream_pool": {
            "idle_timeout_s": 45.0,
            "encode_interval_s": 1.0,
        },
    }
    return redact(payload)
