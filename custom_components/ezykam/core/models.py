"""Value objects for authentication and session handling.

The defining constraint here is that **no secret may ever be printed**. Python
dataclasses generate a ``__repr__`` that interpolates every field, which would
leak ``s-sid`` and ``csrf-token`` into the first accidental ``_LOGGER.debug("%s",
session)``. Every type in this module therefore controls its own ``__repr__``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping

from .redact import mask, redact_url

#: Cookie names observed in the portal's own code (``js-cookie.get(...)``).
COOKIE_SESSION_ID = "s-sid"
COOKIE_CSRF_TOKEN = "csrf-token"

#: Header names the portal echoes the cookies back as.
HEADER_SESSION_ID = "s-sid"
HEADER_CSRF_TOKEN = "csrf-token"

#: Error codes that mean "your session is gone, log in again".
SESSION_LOST_CODES = frozenset({"USER_SESSION_LOSS", "USER_SESSION_INVALID"})

_QR_TOKEN_RE = re.compile(
    r"^(?P<region>[A-Z]{2})"
    r"(?P<created>\d{13})"
    r"(?P<random>[A-Za-z0-9]{26})"
    r"(?P<expires>\d{13})"
    r"(?P<tail>\d{2})$"
)

#: Observed QR token lifetime. Derived from two live samples where
#: ``expires - created == 300000`` ms. Re-checked on every live login.
DEFAULT_QR_TOKEN_TTL_MS = 300_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(value: str | None) -> str:
    """Return a short, non-reversible handle for correlating secrets in logs.

    Allows "this is the same session as before" reasoning without ever printing
    the value itself.
    """
    if not value:
        return "<none>"
    return sha256(value.encode("utf-8")).hexdigest()[:12]


@dataclass(slots=True)
class AuthSession:
    """An established, authenticated cloud session.

    ``sid`` and ``csrf_token`` are the two short-lived secrets the portal keeps
    in cookies and echoes back as request headers. They are stored with
    ``repr=False`` *and* hidden behind a custom ``__repr__``.
    """

    sid: str = field(default="", repr=False)
    csrf_token: str = field(default="", repr=False)
    uid: str | None = field(default=None, repr=False)
    client_id: str | None = field(default=None, repr=False)
    region: str = "EU"
    cloud_type: str = "cpplus"
    acquired_at: datetime = field(default_factory=_utcnow)

    # Populated from the successful ``login/poll`` payload. The portal stores
    # exactly these in localStorage and reads them back on every request, so we
    # mirror them rather than re-deriving region/MQTT settings.
    region_code_cpplus: str = ""
    region_code_public: str = ""
    mqtt_url_cpplus: str = ""
    mqtt_url_public: str = ""
    #: From ``/api/customized/app/info``. Device IDs starting with this prefix
    #: belong to the CPPLUS cloud; others are PUBLIC (third-party) devices.
    dev_id_prefix: str = ""

    @property
    def is_complete(self) -> bool:
        """True when the session carries an ``s-sid``.

        Only ``s-sid`` is required. The portal itself sends
        ``csrf-token: ""`` when the cookie is absent (its header builder drops
        falsy values), and the live cloud never issues that cookie — so treating
        csrf as mandatory would reject sessions the portal happily uses.
        """
        return bool(self.sid)

    @property
    def has_csrf(self) -> bool:
        """True when a ``csrf-token`` cookie was also supplied (optional)."""
        return bool(self.csrf_token)

    @property
    def sid_fingerprint(self) -> str:
        """Non-reversible handle for this session, safe to log."""
        return fingerprint(self.sid)

    @property
    def age_seconds(self) -> float:
        """Seconds since the session was established."""
        return (_utcnow() - self.acquired_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        """Serialise for Home Assistant config-entry storage.

        The caller is responsible for storing this in a protected location;
        the dict itself still contains live secrets by design.
        """
        return {
            "sid": self.sid,
            "csrf_token": self.csrf_token,
            "uid": self.uid,
            "client_id": self.client_id,
            "region": self.region,
            "cloud_type": self.cloud_type,
            "acquired_at": self.acquired_at.isoformat(),
            "region_code_cpplus": self.region_code_cpplus,
            "region_code_public": self.region_code_public,
            "mqtt_url_cpplus": self.mqtt_url_cpplus,
            "mqtt_url_public": self.mqtt_url_public,
            "dev_id_prefix": self.dev_id_prefix,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AuthSession:
        """Rebuild a session from stored data, tolerating missing fields."""
        raw_acquired = data.get("acquired_at")
        acquired = _utcnow()
        if isinstance(raw_acquired, str):
            try:
                acquired = datetime.fromisoformat(raw_acquired)
            except ValueError:
                acquired = _utcnow()
        return cls(
            sid=str(data.get("sid") or ""),
            csrf_token=str(data.get("csrf_token") or ""),
            uid=(str(data["uid"]) if data.get("uid") else None),
            client_id=(str(data["client_id"]) if data.get("client_id") else None),
            region=str(data.get("region") or "EU"),
            cloud_type=str(data.get("cloud_type") or "cpplus"),
            acquired_at=acquired,
            region_code_cpplus=str(data.get("region_code_cpplus") or ""),
            region_code_public=str(data.get("region_code_public") or ""),
            mqtt_url_cpplus=str(data.get("mqtt_url_cpplus") or ""),
            mqtt_url_public=str(data.get("mqtt_url_public") or ""),
            dev_id_prefix=str(data.get("dev_id_prefix") or ""),
        )

    def __repr__(self) -> str:  # pragma: no cover - exercised via tests
        return (
            "AuthSession("
            f"sid={mask(self.sid)}, "
            f"csrf_token={mask(self.csrf_token)}, "
            f"uid={mask(self.uid)}, "
            f"client_id={mask(self.client_id)}, "
            f"region={self.region!r}, "
            f"cloud_type={self.cloud_type!r}, "
            f"region_code_cpplus={self.region_code_cpplus!r}, "
            f"region_code_public={self.region_code_public!r}, "
            f"mqtt_url_cpplus={redact_url(self.mqtt_url_cpplus)!r}, "
            f"mqtt_url_public={redact_url(self.mqtt_url_public)!r}, "
            f"dev_id_prefix={self.dev_id_prefix!r}, "
            f"sid_fp={self.sid_fingerprint!r}"
            ")"
        )

    __str__ = __repr__


@dataclass(slots=True)
class QrChallenge:
    """A pending QR login, minted by ``/api/customized/qrcode``."""

    token: str = field(default="", repr=False)
    image_png: bytes = field(default=b"", repr=False)
    region: str = "EU"
    created_at: datetime = field(default_factory=_utcnow)
    expires_at: datetime | None = None

    @classmethod
    def from_token(cls, token: str, *, region: str = "EU") -> QrChallenge:
        """Build a challenge, deriving expiry from the token when parseable.

        The token layout (``<region><created_ms><26 random><expires_ms><tail>``)
        was observed on two live samples. Parsing is best-effort: if a future
        token does not match, ``expires_at`` falls back to the default TTL and
        the flow still works, just with a conservative deadline.
        """
        created = _utcnow()
        expires: datetime | None = None
        match = _QR_TOKEN_RE.match(token)
        if match:
            created_ms = int(match.group("created"))
            expires_ms = int(match.group("expires"))
            created = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            expires = datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc)
        if expires is None:
            from datetime import timedelta

            expires = created + timedelta(milliseconds=DEFAULT_QR_TOKEN_TTL_MS)
        return cls(token=token, region=region, created_at=created, expires_at=expires)

    @property
    def ttl_seconds(self) -> float:
        if self.expires_at is None:
            return 0.0
        return max(0.0, (self.expires_at - _utcnow()).total_seconds())

    @property
    def is_expired(self) -> bool:
        return self.ttl_seconds <= 0.0

    @property
    def token_fingerprint(self) -> str:
        return fingerprint(self.token)

    def __repr__(self) -> str:
        return (
            "QrChallenge("
            f"token={mask(self.token)}, "
            f"image_bytes={len(self.image_png)}, "
            f"region={self.region!r}, "
            f"ttl_s={self.ttl_seconds:.0f}, "
            f"token_fp={self.token_fingerprint!r}"
            ")"
        )

    __str__ = __repr__


@dataclass(slots=True)
class Home:
    """An account home (group) as returned by ``customized/common/homeList``.

    ``home_id`` is the ``gid``/``groupId`` value — the key that ``roomList``
    accepts. The separate ``id`` field is a different identifier and returns no
    devices (verified live), so it is kept only for reference.
    """

    home_id: str
    name: str = ""
    record_id: str = ""
    owner_id: str = ""
    is_admin: bool = False

    @classmethod
    def from_payload(cls, payload: Any) -> Home | None:
        """Build from a raw homeList entry, or None if it has no usable id."""
        if not isinstance(payload, Mapping):
            return None
        home_id = payload.get("gid") or payload.get("groupId")
        if home_id is None:
            return None
        record_id = payload.get("id")
        return cls(
            home_id=str(home_id),
            name=str(payload.get("name") or ""),
            record_id=str(record_id) if record_id is not None else "",
            owner_id=str(payload.get("ownerId") or ""),
            is_admin=bool(payload.get("admin")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "home_id": self.home_id,
            "name": self.name,
            "record_id": self.record_id,
            "is_admin": self.is_admin,
        }


@dataclass(slots=True)
class Camera:
    """A normalised camera.

    ``device_id`` (the cloud's ``devId``) is the stable identity: it survives
    renames, re-homing and firmware updates, which is exactly what a Home
    Assistant ``unique_id`` needs.

    Deliberately **not** stored: ``localKey`` and ``secKey``. Those are
    per-device secrets returned by ``roomList``; they are not needed to stream
    (the RTC config supplies what the media path requires) and persisting them
    in a config entry would create an avoidable copy of credential material.
    """

    device_id: str
    name: str = ""
    online: bool = False
    home_id: str = ""
    home_name: str = ""
    product_id: str = ""
    owner_id: str = ""
    shared: bool = False
    icon_url: str = ""

    @property
    def unique_id(self) -> str:
        """Stable identifier for Home Assistant entity/device registry use."""
        return self.device_id

    @classmethod
    def from_payload(
        cls, payload: Any, *, home: Home | None = None, shared: bool | None = None
    ) -> Camera | None:
        """Build from a raw roomList/shareList device entry."""
        if not isinstance(payload, Mapping):
            return None
        device_id = payload.get("devId") or payload.get("deviceId")
        if not device_id:
            return None
        if shared is None:
            shared = "shareInfo" in payload
        return cls(
            device_id=str(device_id),
            name=str(payload.get("name") or payload.get("deviceName") or ""),
            online=bool(payload.get("cloudOnline", payload.get("online", False))),
            home_id=home.home_id if home else "",
            home_name=home.name if home else "",
            product_id=str(payload.get("productId") or ""),
            owner_id=str(payload.get("ownerId") or ""),
            shared=bool(shared),
            icon_url=str(payload.get("iconUrl") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        """Loggable/diagnostic view. Contains no credential material."""
        return {
            "device_id": self.device_id,
            "name": self.name,
            "online": self.online,
            "home_id": self.home_id,
            "home_name": self.home_name,
            "product_id": self.product_id,
            "owner_id": self.owner_id,
            "shared": self.shared,
        }
