"""Discovery normalisation, deduplication and failure tolerance."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.ezykam.core.discovery import DiscoveryResult, discover
from custom_components.ezykam.core.errors import (
    EzykamApiError,
    SessionExpiredError,
)
from custom_components.ezykam.core.models import Camera, Home

# Shapes captured from a live account (secrets removed).
HOME_LIVE = {
    "admin": True,
    "gid": 100000001,
    "groupId": 100000001,
    "id": 200605275,
    "name": "Test Home",
    "ownerId": "100000001",
    "uid": "100000002",
}

DEVICE_LIVE = {
    "devId": "beaaaaaaaaaaaaaaaaaa01",
    "name": "Front Camera",
    "cloudOnline": True,
    "productId": "ABC123",
    "ownerId": "100000001",
    "localKey": "0123456789abcdef",
    "secKey": "fedcba9876543210",
    "ip": "203.0.113.7",
    "mac": "aabbccddeeff",
}

SHARED_LIVE = {
    "devId": "becccccccccccccccccc03",
    "name": "Shared Camera",
    "cloudOnline": False,
    "productId": "ABC123",
    "ownerId": "100000003",
    "shareInfo": {"nickname": "someone"},
    "localKey": "aaaaaaaaaaaaaaaa",
}


class FakeClient:
    """Duck-typed stand-in for EzykamClient covering the discovery surface."""

    def __init__(
        self,
        *,
        homes: list[Any] | None = None,
        devices: dict[str, list[Any]] | None = None,
        shared: list[Any] | None = None,
        fail_home: str | None = None,
        fail_shared: Exception | None = None,
        prefix: str | None = "be",
    ) -> None:
        self._homes = homes or []
        self._devices = devices or {}
        self._shared = shared or []
        self._fail_home = fail_home
        self._fail_shared = fail_shared
        self._prefix = prefix
        self.prefix_calls = 0

    async def fetch_dev_id_prefix(self) -> str:
        self.prefix_calls += 1
        return self._prefix or ""

    async def homes(self) -> list[Any]:
        return list(self._homes)

    async def home_devices(self, home_id: str) -> list[Any]:
        if self._fail_home is not None and home_id == self._fail_home:
            raise EzykamApiError("boom", path="/api/customized/common/roomList", status=500)
        return list(self._devices.get(home_id, []))

    async def shared_devices(self) -> list[Any]:
        if self._fail_shared is not None:
            raise self._fail_shared
        return list(self._shared)


def run(coro):  # noqa: ANN201 - tiny helper
    import asyncio

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Home normalisation
# ---------------------------------------------------------------------------


def test_home_uses_gid_as_the_roomlist_key() -> None:
    home = Home.from_payload(HOME_LIVE)
    assert home is not None
    # gid/groupId is what roomList accepts; `id` is a different identifier.
    assert home.home_id == "100000001"
    assert home.record_id == "200605275"
    assert home.name == "Test Home"
    assert home.is_admin is True


def test_home_without_gid_is_rejected() -> None:
    assert Home.from_payload({"id": 1, "name": "x"}) is None
    assert Home.from_payload(None) is None
    assert Home.from_payload("not a mapping") is None


def test_home_falls_back_to_group_id() -> None:
    home = Home.from_payload({"groupId": 42, "name": "g"})
    assert home is not None
    assert home.home_id == "42"


# ---------------------------------------------------------------------------
# Camera normalisation
# ---------------------------------------------------------------------------


def test_camera_normalisation_from_live_shape() -> None:
    home = Home.from_payload(HOME_LIVE)
    cam = Camera.from_payload(DEVICE_LIVE, home=home, shared=False)
    assert cam is not None
    assert cam.device_id == "beaaaaaaaaaaaaaaaaaa01"
    assert cam.name == "Front Camera"
    assert cam.online is True
    assert cam.home_id == "100000001"
    assert cam.home_name == "Test Home"
    assert cam.product_id == "ABC123"
    assert cam.shared is False
    assert cam.unique_id == cam.device_id


def test_camera_without_dev_id_is_rejected() -> None:
    assert Camera.from_payload({"name": "no id"}) is None
    assert Camera.from_payload(None) is None


def test_shared_camera_detected_from_share_info() -> None:
    cam = Camera.from_payload(SHARED_LIVE)
    assert cam is not None
    assert cam.shared is True
    assert cam.home_id == ""  # not inside a home of this account
    assert cam.owner_id == "100000003"


def test_camera_diagnostics_dict_contains_no_secrets() -> None:
    cam = Camera.from_payload(DEVICE_LIVE, shared=False)
    assert cam is not None
    text = repr(cam.as_dict())
    assert "0123456789abcdef" not in text
    assert "fedcba9876543210" not in text
    assert "203.0.113.7" not in text
    assert cam.as_dict()["device_id"] == "beaaaaaaaaaaaaaaaaaa01"


def test_camera_repr_does_not_carry_device_secrets() -> None:
    cam = Camera.from_payload(DEVICE_LIVE, shared=False)
    assert cam is not None
    # localKey/secKey must never even be fields on the model.
    assert not hasattr(cam, "local_key")
    assert not hasattr(cam, "sec_key")


# ---------------------------------------------------------------------------
# End-to-end discovery
# ---------------------------------------------------------------------------


def test_discover_owned_and_shared_cameras() -> None:
    client = FakeClient(
        homes=[HOME_LIVE],
        devices={"100000001": [DEVICE_LIVE]},
        shared=[SHARED_LIVE],
    )
    result = run(discover(client))

    assert isinstance(result, DiscoveryResult)
    assert len(result.homes) == 1
    assert result.camera_count == 2
    assert result.online_count == 1

    owned = result.camera_by_id("beaaaaaaaaaaaaaaaaaa01")
    shared = result.camera_by_id("becccccccccccccccccc03")
    assert owned is not None and owned.shared is False
    assert shared is not None and shared.shared is True
    # devIdPrefix is fetched once, up front.
    assert client.prefix_calls == 1


def test_discover_deduplicates_owned_over_shared() -> None:
    """If a device is both owned and shared, ownership must win."""
    client = FakeClient(
        homes=[HOME_LIVE],
        devices={"100000001": [DEVICE_LIVE]},
        shared=[dict(DEVICE_LIVE, shareInfo={"nickname": "x"})],
    )
    result = run(discover(client))

    assert result.camera_count == 1
    cam = result.camera_by_id("beaaaaaaaaaaaaaaaaaa01")
    assert cam is not None
    assert cam.shared is False


def test_one_failing_home_does_not_lose_the_others() -> None:
    other_home = dict(HOME_LIVE, gid=999, groupId=999, id=999, name="second")
    second_device = dict(DEVICE_LIVE, devId="beaaaaaaaaaaaaaaaaaa2", name="Second")

    client = FakeClient(
        homes=[HOME_LIVE, other_home],
        devices={"100000001": [DEVICE_LIVE], "999": [second_device]},
        shared=[],
        fail_home="100000001",
    )
    result = run(discover(client))

    assert len(result.homes) == 2
    assert result.camera_count == 1
    assert result.camera_by_id("beaaaaaaaaaaaaaaaaaa2") is not None


def test_shared_device_failure_is_non_fatal() -> None:
    client = FakeClient(
        homes=[HOME_LIVE],
        devices={"100000001": [DEVICE_LIVE]},
        fail_shared=EzykamApiError("boom", path="/api/customized/playback/shareList", status=500),
    )
    result = run(discover(client))
    assert result.camera_count == 1


def test_session_expiry_propagates_for_reauth() -> None:
    """Session loss must never be swallowed — it drives reauthentication."""

    class ExpiredClient(FakeClient):
        async def homes(self) -> list[Any]:
            raise SessionExpiredError("gone", path="/api/customized/common/homeList", status=401)

    with pytest.raises(SessionExpiredError):
        run(discover(ExpiredClient(homes=[HOME_LIVE])))


def test_session_expiry_during_device_listing_propagates() -> None:
    class ExpiredMidway(FakeClient):
        async def home_devices(self, home_id: str) -> list[Any]:
            raise SessionExpiredError("gone", status=401)

    with pytest.raises(SessionExpiredError):
        run(discover(ExpiredMidway(homes=[HOME_LIVE])))


def test_discovery_result_summary_is_redaction_safe() -> None:
    client = FakeClient(
        homes=[HOME_LIVE],
        devices={"100000001": [DEVICE_LIVE]},
        shared=[SHARED_LIVE],
    )
    result = run(discover(client))
    text = repr(result.as_dict())
    assert "0123456789abcdef" not in text
    assert "203.0.113.7" not in text
    assert result.as_dict()["camera_count"] == 2


# ---------------------------------------------------------------------------
# discover_with_retry: a single rejection must not destroy the config entry
# ---------------------------------------------------------------------------


class FlakyClient(FakeClient):
    """Fails the first N homeList calls with a session error, then recovers."""

    def __init__(self, *, fail_times: int, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self._fail_times = fail_times
        self.homes_calls = 0

    async def homes(self) -> list[Any]:
        self.homes_calls += 1
        if self.homes_calls <= self._fail_times:
            raise SessionExpiredError(
                "blip", path="/api/customized/common/homeList", status=401
            )
        return await super().homes()


def test_retry_recovers_from_a_transient_session_rejection() -> None:
    """One rejection must not mark the entry as needing reauth."""
    from custom_components.ezykam.core.discovery import discover_with_retry

    client = FlakyClient(
        fail_times=1,
        homes=[HOME_LIVE],
        devices={"100000001": [DEVICE_LIVE]},
        shared=[],
    )
    result = run(discover_with_retry(client, delay=0.0))

    assert result.camera_count == 1
    assert client.homes_calls == 2, "should have retried exactly once"


def test_retry_still_raises_after_consecutive_failures() -> None:
    """A genuinely dead session must still trigger reauthentication."""
    from custom_components.ezykam.core.discovery import discover_with_retry

    client = FlakyClient(fail_times=99, homes=[HOME_LIVE], shared=[])
    with pytest.raises(SessionExpiredError):
        run(discover_with_retry(client, delay=0.0, attempts=2))
    assert client.homes_calls == 2, "must stop after the configured attempts"


def test_retry_does_not_fire_when_discovery_succeeds() -> None:
    from custom_components.ezykam.core.discovery import discover_with_retry

    client = FakeClient(
        homes=[HOME_LIVE], devices={"100000001": [DEVICE_LIVE]}, shared=[]
    )
    result = run(discover_with_retry(client, delay=0.0))
    assert result.camera_count == 1
