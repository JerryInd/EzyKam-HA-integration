"""Shared constants for the Ezykam integration.

Kept inside ``core`` so both the Home Assistant layer and the standalone
bridge can import it without pulling in Home Assistant.
"""

from __future__ import annotations

DOMAIN = "ezykam"
NAME = "CP Plus Ezykam"

#: Home Assistant platforms this integration provides.
PLATFORMS: list[str] = ["camera", "binary_sensor"]

#: Config-entry keys.
CONF_SESSION = "session"
CONF_REGION = "region"
CONF_STREAM_PROFILE = "stream_profile"

#: Stream profiles offered per camera. Values come from the cloud's RTC config;
#: these are only the labels shown in the UI.
STREAM_PROFILE_MAIN = "main"
STREAM_PROFILE_SUB = "sub"
STREAM_PROFILES: list[str] = [STREAM_PROFILE_MAIN, STREAM_PROFILE_SUB]

#: Observed profile geometry (from captured RTC responses).
MAIN_STREAM_RESOLUTION = (1920, 1080)
SUB_STREAM_RESOLUTION = (640, 360)

#: Coordinator refresh cadence for online/offline state.
DEFAULT_SCAN_INTERVAL_SECONDS = 30

#: Reauthentication signal used by the coordinator when the session expires.
SIGNAL_SESSION_EXPIRED = f"{DOMAIN}_session_expired"
