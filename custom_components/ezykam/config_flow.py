"""Config flow for the CP Plus Ezykam integration.

Sign-in is a QR scan. This is not a cosmetic choice: the portal's own default
(``loginConfig: 0``) is QR login, it needs no behavioural captcha, and it never
handles the account password â€” so Home Assistant never stores one.

Home Assistant flow mechanics (verified against ``data_entry_flow.py`` source,
not from memory):

* ``async_show_progress`` requires a ``progress_task``; omitting it warns and
  breaks in HA 2024.8.
* A ``SHOW_PROGRESS`` step may only transition to ``SHOW_PROGRESS`` or
  ``SHOW_PROGRESS_DONE`` â€” returning ``CREATE_ENTRY`` straight from it raises
  ``ValueError``. Hence the three-step shape ``user -> scan -> confirm``.
* HA fires ``async_notify_flow_changed()`` itself when the step id changes,
  which is what nudges the frontend to advance past ``progress_done``.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

# probatio is Home Assistant's declared schema library (voluptuous now arrives
# only as a transitive dependency of annotatedyaml/hass-nabucasa, so depending
# on it would be fragile).
import probatio

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .core import EzykamClient, EzykamError, QrLoginError, QrLoginFlow
from .core.const import (
    CONF_REGION,
    CONF_SESSION,
    CONF_STREAM_PROFILE,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    STREAM_PROFILE_MAIN,
    STREAM_PROFILES,
)
from .core.discovery import discover
from .core.errors import QrLoginTimeoutError, SessionExpiredError
from .core.models import AuthSession
from .web import get_qr_store

_LOGGER = logging.getLogger(__name__)

#: Matches the QR token's own lifetime (observed as exactly 300 s).
QR_LOGIN_TIMEOUT = 300.0

_STEP_USER = "user"
_STEP_SCAN = "scan"
_STEP_CONFIRM = "confirm"


def _qr_data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class EzykamConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup and reauthentication."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._client: EzykamClient | None = None
        self._flow: QrLoginFlow | None = None
        self._task: Any = None
        self._qr_image: str = ""
        self._qr_url: str = ""
        self._errors: dict[str, str] = {}
        self._reauth_entry: config_entries.ConfigEntry | None = None

    # ------------------------------------------------------------------
    # shared login helpers
    # ------------------------------------------------------------------
    async def _start_login(self) -> None:
        """Mint a QR challenge, publish it, and launch the approval task."""
        self._client = EzykamClient(session=async_get_clientsession(self.hass))
        self._flow = QrLoginFlow(self._client)
        challenge = await self._flow.start()
        self._qr_image = (
            _qr_data_url(challenge.image_png) if challenge.image_png else ""
        )
        if challenge.image_png:
            nonce = get_qr_store(self.hass).put(challenge.image_png)
            # Relative URL: the browser resolves it against the page origin, so
            # no base_url/config API lookup is needed (ApiConfig.base_url was
            # removed in recent Home Assistant versions).
            self._qr_url = f"/api/ezykam/qr/{nonce}"
        else:
            self._qr_url = ""
        self._task = self.hass.async_create_task(
            self._flow.wait_for_approval(timeout=QR_LOGIN_TIMEOUT)
        )

    def _qr_form(self) -> dict[str, Any]:
        """Show the QR as a *form* step.

        Home Assistant's SHOW_PROGRESS UI does not render the step description
        (verified live: only the title and spinner appeared), so a progress
        step could never display the code. A form renders it, and the user
        presses Submit once they have scanned.
        """
        return self.async_show_form(
            step_id=_STEP_SCAN,
            data_schema=probatio.Schema({}),
            description_placeholders={
                "qr_image": self._qr_image,
                "qr_url": self._qr_url,
            },
        )

    async def _session_or_raise(self) -> AuthSession:
        """Await the login task and surface failures as typed exceptions."""
        assert self._task is not None
        return await self._task

    # ------------------------------------------------------------------
    # initial setup
    # ------------------------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Show a start button, then drive the QR login."""
        if user_input is None:
            return self.async_show_form(
                step_id=_STEP_USER, data_schema=probatio.Schema({}), errors=self._errors or None
            )

        self._errors = {}
        try:
            await self._start_login()
        except EzykamError as err:
            _LOGGER.error("Could not start Ezykam login: %s", err)
            self._errors = {"base": "cannot_connect"}
            return self.async_show_form(
                step_id=_STEP_USER, data_schema=probatio.Schema({}), errors=self._errors
            )
        return self._qr_form()

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Show the QR and complete once it has been scanned.

        Submitted before the scan completes? Show the code again rather than
        failing - the user simply presses Submit a second time.
        """
        if self._task is None:
            return self.async_abort(reason="login_failed")
        if not self._task.done():
            return self._qr_form()
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Turn a completed login into a config entry (or a clear abort)."""
        try:
            session = await self._session_or_raise()
        except QrLoginTimeoutError:
            return self.async_abort(reason="timeout")
        except (QrLoginError, EzykamError) as err:
            _LOGGER.error("Ezykam login failed: %s", type(err).__name__)
            return self.async_abort(reason="login_failed")
        except Exception:  # noqa: BLE001 - never let a flow die silently
            _LOGGER.exception("Unexpected Ezykam login failure")
            return self.async_abort(reason="unknown")

        assert self._client is not None
        if session.uid:
            await self.async_set_unique_id(str(session.uid))
            self._abort_if_unique_id_configured()

        try:
            inventory = await discover(self._client)
        except SessionExpiredError:
            return self.async_abort(reason="invalid_auth")
        except EzykamError as err:
            _LOGGER.error("Ezykam discovery failed: %s", type(err).__name__)
            return self.async_abort(reason="cannot_connect")

        if not inventory.cameras:
            return self.async_abort(reason="no_devices")

        title = inventory.homes[0].name if inventory.homes else "Ezykam"
        data: dict[str, Any] = {
            CONF_SESSION: session.to_dict(),
            CONF_REGION: session.region,
        }
        if self._reauth_entry is not None:
            self.hass.config_entries.async_update_entry(self._reauth_entry, data=data)
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        return self.async_create_entry(title=title, data=data)

    # ------------------------------------------------------------------
    # reauthentication
    # ------------------------------------------------------------------
    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> dict[str, Any]:
        entry_id = self.context.get("entry_id")
        if entry_id:
            self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Re-scan a QR code to refresh a rejected session."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm", data_schema=probatio.Schema({})
            )
        try:
            await self._start_login()
        except EzykamError as err:
            _LOGGER.error("Could not start Ezykam re-login: %s", err)
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=probatio.Schema({}),
                errors={"base": "cannot_connect"},
            )
        return self._qr_form()

    # ------------------------------------------------------------------
    # options
    # ------------------------------------------------------------------
    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        # The framework injects config_entry onto the flow instance; passing it
        # into __init__ is the deprecated pattern (and it is not available
        # there), so the argument is intentionally not forwarded.
        return EzykamOptionsFlow()


class EzykamOptionsFlow(config_entries.OptionsFlow):
    """Stream profile and refresh cadence."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = dict(self.config_entry.options)
        schema = probatio.Schema(
            {
                probatio.Optional(
                    CONF_STREAM_PROFILE,
                    default=current.get(CONF_STREAM_PROFILE, STREAM_PROFILE_MAIN),
                ): probatio.In(STREAM_PROFILES),
                probatio.Optional(
                    "scan_interval",
                    default=current.get(
                        "scan_interval", DEFAULT_SCAN_INTERVAL_SECONDS
                    ),
                ): probatio.All(probatio.Coerce(int), probatio.Range(min=10, max=3600)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
