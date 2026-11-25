"""The QR login state machine, exercised end-to-end against a local server."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from custom_components.ezykam.core.auth import QrLoginFlow, login_with_qr
from custom_components.ezykam.core.errors import (
    QrLoginCancelledError,
    QrLoginError,
    QrLoginTimeoutError,
)
from custom_components.ezykam.core.models import AuthSession
from support import err, make_client, make_token, ok, run, serve

SID = "qr-issued-sid"
CSRF = "qr-issued-csrf"

#: 1x1 transparent PNG, as a stand-in for the endpoint's real image payload.
PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _build_app(
    *,
    approve_after: int = 3,
    terminal_error: str | None = None,
    issue_cookies: bool = True,
) -> tuple[web.Application, dict[str, Any]]:
    """Build a portal-like login app.

    Args:
        approve_after: polls answered "pending" before approving.
        terminal_error: when set, every poll fails with this error code.
        issue_cookies: whether the approval response carries session cookies.
    """
    token = make_token()
    state: dict[str, Any] = {
        "token": token,
        "polls": 0,
        "saw_exchange": False,
        "poll_tokens": [],
        "poll_headers": {},
    }

    async def qrcode(_request: web.Request) -> web.Response:
        return ok({"token": token})

    async def exchange(request: web.Request) -> web.Response:
        body = await request.json()
        # The portal sends exactly this shape.
        assert body["url"].startswith("tuyaSmart--qrLogin?token=")
        assert body["url"].endswith(token)
        state["saw_exchange"] = True
        return ok(PNG_DATA_URL)

    async def poll(request: web.Request) -> web.Response:
        body = await request.json()
        state["poll_tokens"].append(body.get("token"))
        state["poll_headers"] = {k.lower(): v for k, v in request.headers.items()}
        state["polls"] += 1

        if terminal_error is not None:
            return err(terminal_error)

        if state["polls"] < approve_after:
            # Observed pending shape.
            return ok({"domain": {}})

        # Approved. Field names come verbatim from the portal's success
        # handler: it reads result.sid as its success signal, then pulls the
        # MQTT endpoints and region codes out of domain/publicSession.
        response = ok(
            {
                "sid": SID,
                "uid": "user-42",
                "domain": {
                    "mobileMqttsUrl": "wss://m1-cube.cppluscloud.com/mqtt",
                    "regionCode": "EU",
                },
                "publicSession": {
                    "domain": {
                        "mobileMqttsUrl": "wss://public-broker.example/mqtt",
                        "regionCode": "WE",
                    }
                },
                "nickname": "Front Door",
                "username": "owner-account",
            }
        )
        if issue_cookies:
            response.set_cookie("s-sid", SID, path="/")
            response.set_cookie("csrf-token", CSRF, path="/")
        return response

    app = web.Application()
    app.router.add_post("/api/customized/qrcode", qrcode)
    app.router.add_post("/api/login/exchange", exchange)
    app.router.add_post("/api/customized/login/poll", poll)
    return app, state


def test_full_qr_login_establishes_session() -> None:
    async def main() -> None:
        app, state = _build_app(approve_after=3)
        async with serve(app) as base:
            client = make_client(base)
            try:
                flow = QrLoginFlow(client, poll_interval=0.2)
                challenge = await flow.start()

                # The QR PNG must be rendered before we start waiting.
                assert challenge.image_png.startswith(b"\x89PNG")
                assert state["saw_exchange"] is True
                assert not client.is_authenticated

                session = await flow.wait_for_approval(timeout=30)

                assert session.is_complete
                assert session.sid == SID
                assert session.csrf_token == CSRF
                assert client.is_authenticated
                assert state["polls"] == 3
                assert all(t == state["token"] for t in state["poll_tokens"])
                # The login payload must carry the cloud configuration the
                # portal itself persists for later requests.
                assert session.uid == "user-42"
                assert session.region_code_cpplus == "EU"
                assert session.region_code_public == "WE"
                assert "m1-cube.cppluscloud.com" in session.mqtt_url_cpplus
                assert "public-broker.example" in session.mqtt_url_public
                # Secrets must never appear in the repr we would log.
                assert SID not in repr(session)
                assert CSRF not in repr(session)
            finally:
                await client.close()

    run(main())


def test_login_with_qr_convenience_wrapper() -> None:
    async def main() -> None:
        app, _state = _build_app(approve_after=2)
        async with serve(app) as base:
            client = make_client(base)
            try:
                challenge, session = await login_with_qr(client, poll_interval=0.2)
                assert challenge.image_png
                assert session.sid == SID
            finally:
                await client.close()

    run(main())


def test_progress_callback_reports_without_secrets() -> None:
    async def main() -> None:
        app, _state = _build_app(approve_after=2)
        messages: list[str] = []
        async with serve(app) as base:
            client = make_client(base)
            try:
                flow = QrLoginFlow(client, poll_interval=0.2, on_progress=messages.append)
                challenge = await flow.start()
                await flow.wait_for_approval(timeout=30)
            finally:
                await client.close()

        assert messages, "progress callback never fired"
        joined = "\n".join(messages)
        assert "scan" in joined.lower()
        assert SID not in joined
        assert CSRF not in joined
        assert challenge.token not in joined

    run(main())


def test_cancel_raises_typed_error() -> None:
    async def main() -> None:
        app, _state = _build_app(approve_after=10_000)
        async with serve(app) as base:
            client = make_client(base)
            try:
                flow = QrLoginFlow(client, poll_interval=0.2)
                await flow.start()
                flow.cancel()
                try:
                    await flow.wait_for_approval(timeout=30)
                except QrLoginCancelledError:
                    pass
                else:
                    raise AssertionError("expected QrLoginCancelledError")
            finally:
                await client.close()

    run(main())


def test_timeout_raises_typed_error() -> None:
    async def main() -> None:
        app, _state = _build_app(approve_after=10_000)
        async with serve(app) as base:
            client = make_client(base)
            try:
                flow = QrLoginFlow(client, poll_interval=0.2)
                await flow.start()
                try:
                    await flow.wait_for_approval(timeout=0.05)
                except QrLoginTimeoutError:
                    pass
                else:
                    raise AssertionError("expected QrLoginTimeoutError")
            finally:
                await client.close()

    run(main())


def test_expired_qr_error_code_is_typed() -> None:
    async def main() -> None:
        app, _state = _build_app(terminal_error="QRCODE_TIMEOUT")
        async with serve(app) as base:
            client = make_client(base)
            try:
                flow = QrLoginFlow(client, poll_interval=0.2)
                await flow.start()
                try:
                    await flow.wait_for_approval(timeout=30)
                except QrLoginTimeoutError:
                    pass
                else:
                    raise AssertionError("expected QrLoginTimeoutError")
            finally:
                await client.close()

    run(main())


def test_wait_before_start_is_rejected() -> None:
    async def main() -> None:
        app, _state = _build_app()
        async with serve(app) as base:
            client = make_client(base)
            try:
                flow = QrLoginFlow(client)
                try:
                    await flow.wait_for_approval(timeout=1)
                except QrLoginError:
                    pass
                else:
                    raise AssertionError("expected QrLoginError")
            finally:
                await client.close()

    run(main())


def test_body_sid_without_csrf_cookie_still_establishes_session() -> None:
    """Portal-faithful: success hinges on ``result.sid`` alone.

    The live cloud issued a session payload without a csrf-token cookie, so the
    flow must not treat that as fatal — it must succeed and leave the missing
    csrf visible to the caller.
    """
    async def main() -> None:
        app, _state = _build_app(approve_after=2, issue_cookies=False)
        async with serve(app) as base:
            client = make_client(base)
            try:
                flow = QrLoginFlow(client, poll_interval=0.2)
                await flow.start()
                session = await flow.wait_for_approval(timeout=30)

                assert session.sid == SID
                assert session.uid == "user-42"
                # csrf genuinely absent: surfaced, not fabricated — but the
                # session is still usable, because this cloud does not require
                # csrf-token (the portal sends an empty one).
                assert session.csrf_token == ""
                assert session.is_complete
                assert session.has_csrf is False
                assert client.is_authenticated
            finally:
                await client.close()

    run(main())


def test_login_ignores_session_held_before_start() -> None:
    """A login must not "succeed" with a session it never obtained.

    Regression: the config flow builds its login client on Home Assistant's
    shared ``ClientSession``, whose cookie jar already held the coordinator's
    expired ``s-sid``. ``wait_for_approval`` treated ``is_authenticated`` as
    approval, so it returned on the very first poll — carrying the dead session,
    ~160 ms after the QR was minted and long before any scan. Reauthentication
    then aborted with "The cloud rejected the stored session".

    Approval has to mean the session *changed*. Here the server never issues
    one, so the flow must keep polling and time out.
    """
    stale = "stale-sid-from-another-client"

    async def main() -> None:
        app, _state = _build_app(approve_after=10_000)
        async with serve(app) as base:
            # Pre-hold a session, exactly as the shared jar did in production.
            client = make_client(base, auth=AuthSession(sid=stale))
            try:
                assert client.is_authenticated
                flow = QrLoginFlow(client, poll_interval=0.2)
                await flow.start()
                try:
                    await flow.wait_for_approval(timeout=0.5)
                except QrLoginTimeoutError:
                    pass
                else:
                    raise AssertionError(
                        "login reported success without issuing a new session"
                    )
                # The stale session must still be the only one we hold: it was
                # never ours to begin with, and must not be mistaken for a login.
                assert client.auth.sid == stale
            finally:
                await client.close()

    run(main())


def test_boolean_false_poll_means_qr_no_longer_usable() -> None:
    """The portal stops polling when the cloud answers a bare `false`."""
    async def main() -> None:
        token = make_token()

        async def qrcode(_request: web.Request) -> web.Response:
            return ok({"token": token})

        async def exchange(_request: web.Request) -> web.Response:
            return ok(PNG_DATA_URL)

        async def poll(_request: web.Request) -> web.Response:
            return ok(False)

        app = web.Application()
        app.router.add_post("/api/customized/qrcode", qrcode)
        app.router.add_post("/api/login/exchange", exchange)
        app.router.add_post("/api/customized/login/poll", poll)

        async with serve(app) as base:
            client = make_client(base)
            try:
                flow = QrLoginFlow(client, poll_interval=0.2)
                await flow.start()
                try:
                    await flow.wait_for_approval(timeout=30)
                except QrLoginTimeoutError:
                    pass
                else:
                    raise AssertionError("expected QrLoginTimeoutError")
            finally:
                await client.close()

    run(main())


def test_poll_sends_token_without_session_headers() -> None:
    async def main() -> None:
        app, state = _build_app(approve_after=2)
        async with serve(app) as base:
            client = make_client(base)
            try:
                flow = QrLoginFlow(client, poll_interval=0.2)
                await flow.start()
                await flow.wait_for_approval(timeout=30)
            finally:
                await client.close()

        headers = state["poll_headers"]
        # Pre-login polling must not attach a session we do not have yet.
        assert "s-sid" not in headers
        assert "csrf-token" not in headers
        assert headers.get("cloud-type") == "cpplus"
        assert headers.get("cpplus-region-code") == ""
        assert state["poll_tokens"][0] == state["token"]
        assert state["polls"] == 2

    run(main())
