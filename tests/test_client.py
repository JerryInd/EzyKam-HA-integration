"""Envelope handling, header injection and typed error mapping."""

from __future__ import annotations

from aiohttp import web

from custom_components.ezykam.core.client import EzykamClient
from custom_components.ezykam.core.errors import (
    EzykamApiError,
    EzykamProtocolError,
    EzykamRateLimitError,
    EzykamTransportError,
    SessionExpiredError,
)
from custom_components.ezykam.core.models import AuthSession
from support import err, make_client, ok, run, serve

SID = "test-sid-value"
CSRF = "test-csrf-value"


def _capture(captured: dict[str, str]):
    """Async handler that records request headers and answers success."""

    async def handler(request: web.Request) -> web.Response:
        captured.update({k.lower(): v for k, v in request.headers.items()})
        return ok({})

    return handler


def test_unwraps_successful_envelope() -> None:
    async def main() -> None:
        async def route(_request: web.Request) -> web.Response:
            return ok({"hello": "world"})

        app = web.Application()
        app.router.add_post("/api/x", route)
        async with serve(app) as base:
            async with make_client(base) as client:
                assert await client.request("/api/x", {}) == {"hello": "world"}

    run(main())


def test_sends_portal_header_set() -> None:
    captured: dict[str, str] = {}

    async def main() -> None:
        app = web.Application()
        app.router.add_post("/api/x", _capture(captured))
        async with serve(app) as base:
            async with make_client(base) as client:
                await client.request("/api/x", {})

    run(main())

    assert captured["cloud-type"] == "cpplus"
    assert captured["cpplus-region-code"] == "EU"
    assert captured["x-requested-with"] == "XMLHttpRequest"
    assert captured["origin"] == captured["referer"].rsplit("/", 1)[0]
    # An unauthenticated request must not carry session headers.
    assert "s-sid" not in captured
    assert "csrf-token" not in captured


def test_authenticated_request_carries_session_headers() -> None:
    captured: dict[str, str] = {}

    async def main() -> None:
        app = web.Application()
        app.router.add_post("/api/x", _capture(captured))
        async with serve(app) as base:
            auth = AuthSession(sid=SID, csrf_token=CSRF)
            async with make_client(base, auth=auth) as client:
                await client.request("/api/x", {})

    run(main())

    assert captured["s-sid"] == SID
    assert captured["csrf-token"] == CSRF
    assert captured["cpplus-region-code"] == "EU"


def test_login_response_cookies_are_captured_and_reused() -> None:
    async def main() -> None:
        async def login(_request: web.Request) -> web.Response:
            response = ok({"uid": "u1", "clientId": "c1"})
            response.set_cookie("s-sid", SID, path="/")
            response.set_cookie("csrf-token", CSRF, path="/")
            return response

        app = web.Application()
        app.router.add_post("/api/login", login)
        async with serve(app) as base:
            async with make_client(base) as client:
                assert not client.is_authenticated
                await client.request("/api/login", {})
                assert client.is_authenticated
                assert client.auth.sid == SID
                assert client.auth.csrf_token == CSRF

                # The captured session must flow back out on the next call.
                captured: dict[str, str] = {}
                app2 = web.Application()
                app2.router.add_post("/api/probe", _capture(captured))
                async with serve(app2) as base2:
                    client.base_url = base2
                    await client.request("/api/probe", {})
                assert captured["s-sid"] == SID
                assert captured["csrf-token"] == CSRF

    run(main())


def test_restored_session_seeds_cookie_jar_and_headers() -> None:
    captured: dict[str, str] = {}

    async def main() -> None:
        app = web.Application()
        app.router.add_post("/api/x", _capture(captured))
        async with serve(app) as base:
            auth = AuthSession(sid=SID, csrf_token=CSRF)
            async with make_client(base, auth=auth) as client:
                assert client.is_authenticated
                await client.request("/api/x", {})

    run(main())

    assert captured["s-sid"] == SID
    assert captured["csrf-token"] == CSRF


def test_stale_cookie_in_shared_jar_is_not_adopted() -> None:
    """A cookie left in the jar by someone else must not become our session.

    Regression: Home Assistant hands every integration the *same*
    ``ClientSession``, so the coordinator's expired ``s-sid`` was sitting in the
    cookie jar when the config flow built its login client. The old
    ``_absorb_cookies`` read the whole jar, so the first response of a brand-new
    login silently adopted the dead session — ``is_authenticated`` went True
    before anybody scanned, and the flow "succeeded" carrying credentials the
    cloud had already rejected.

    Only cookies this response actually issued may be absorbed.
    """
    stale = "stale-sid-from-another-client"

    async def main() -> None:
        # The response deliberately issues no session cookie at all.
        async def login(_request: web.Request) -> web.Response:
            return ok({"token": "abc"})

        app = web.Application()
        app.router.add_post("/api/customized/qrcode", login)
        async with serve(app) as base:
            async with make_client(base) as client:
                # Simulate the shared jar already holding another client's session.
                client._session.cookie_jar.update_cookies(  # noqa: SLF001
                    {"s-sid": stale}
                )
                assert not client.is_authenticated
                # Guard against a vacuous test: the jar really must hold it,
                # otherwise "not absorbed" would prove nothing.
                from yarl import URL

                held = client._session.cookie_jar.filter_cookies(  # noqa: SLF001
                    URL(client.base_url)
                )
                assert held.get("s-sid") is not None, "stale cookie never landed"
                assert held["s-sid"].value == stale

                await client.request("/api/customized/qrcode", {})

                # The response set no s-sid, so we must still be unauthenticated.
                assert client.auth.sid == ""
                assert not client.is_authenticated

    run(main())


def test_http_401_maps_to_session_expired() -> None:
    async def main() -> None:
        async def route(_request: web.Request) -> web.Response:
            return err("USER_SESSION_LOSS", status=401, message="Not login")

        app = web.Application()
        app.router.add_post("/api/x", route)
        async with serve(app) as base:
            auth = AuthSession(sid=SID, csrf_token=CSRF)
            async with make_client(base, auth=auth) as client:
                try:
                    await client.request("/api/x", {})
                except SessionExpiredError as exc:
                    assert exc.error_code == "USER_SESSION_LOSS"
                    assert exc.status == 401
                else:
                    raise AssertionError("expected SessionExpiredError")

    run(main())


def test_session_loss_code_with_http_200_maps_to_session_expired() -> None:
    async def main() -> None:
        async def route(_request: web.Request) -> web.Response:
            return err("USER_SESSION_INVALID")

        app = web.Application()
        app.router.add_post("/api/x", route)
        async with serve(app) as base:
            auth = AuthSession(sid=SID, csrf_token=CSRF)
            async with make_client(base, auth=auth) as client:
                try:
                    await client.request("/api/x", {})
                except SessionExpiredError:
                    pass
                else:
                    raise AssertionError("expected SessionExpiredError")

    run(main())


def test_generic_failure_maps_to_api_error_without_leaking_body() -> None:
    async def main() -> None:
        async def route(_request: web.Request) -> web.Response:
            return err("API_OR_API_VERSION_WRONG", message="api version wrong")

        app = web.Application()
        app.router.add_post("/api/x", route)
        async with serve(app) as base:
            async with make_client(base) as client:
                try:
                    await client.request("/api/x", {})
                except EzykamApiError as exc:
                    assert exc.error_code == "API_OR_API_VERSION_WRONG"
                    assert exc.path == "/api/x"
                    assert "api version wrong" not in str(exc)
                else:
                    raise AssertionError("expected EzykamApiError")

    run(main())


def test_rate_limit_maps_to_specific_error() -> None:
    async def main() -> None:
        async def route(_request: web.Request) -> web.Response:
            return err("TOO_MANY_REQUESTS", status=429)

        app = web.Application()
        app.router.add_post("/api/x", route)
        async with serve(app) as base:
            async with make_client(base) as client:
                try:
                    await client.request("/api/x", {})
                except EzykamRateLimitError:
                    pass
                else:
                    raise AssertionError("expected EzykamRateLimitError")

    run(main())


def test_non_json_body_raises_protocol_error() -> None:
    async def main() -> None:
        async def route(_request: web.Request) -> web.Response:
            return web.Response(text="<html>gateway error</html>", status=200)

        app = web.Application()
        app.router.add_post("/api/x", route)
        async with serve(app) as base:
            async with make_client(base) as client:
                try:
                    await client.request("/api/x", {})
                except EzykamProtocolError:
                    pass
                else:
                    raise AssertionError("expected EzykamProtocolError")

    run(main())


def test_transport_failure_is_typed() -> None:
    async def main() -> None:
        # Port 1 is never listening; connection refused surfaces as ClientError.
        async with make_client("http://127.0.0.1:1") as client:
            try:
                await client.request("/api/x", {})
            except EzykamTransportError:
                pass
            else:
                raise AssertionError("expected EzykamTransportError")

    run(main())


def test_describe_is_redacted() -> None:
    async def main() -> None:
        auth = AuthSession(sid=SID, csrf_token=CSRF)
        async with make_client("https://web.ezykam.com", auth=auth) as client:
            info = client.describe()
            text = repr(info)
            assert SID not in text
            assert CSRF not in text
            assert info["region"] == "EU"
            assert info["authenticated"] is True

    run(main())


def test_context_manager_closes_owned_session() -> None:
    async def main() -> None:
        async def route(_request: web.Request) -> web.Response:
            return ok({})

        app = web.Application()
        app.router.add_post("/api/x", route)
        async with serve(app) as base:
            async with make_client(base) as client:
                await client.request("/api/x", {})
                session = client._session  # noqa: SLF001 - asserting ownership
            assert session.closed

    run(main())


def test_client_does_not_close_a_session_it_does_not_own() -> None:
    async def main() -> None:
        async def route(_request: web.Request) -> web.Response:
            return ok({})

        app = web.Application()
        app.router.add_post("/api/x", route)
        async with serve(app) as base:
            import aiohttp

            shared = aiohttp.ClientSession()
            try:
                async with EzykamClient(session=shared, base_url=base) as client:
                    await client.request("/api/x", {})
                assert not shared.closed, "client closed a session it does not own"
            finally:
                await shared.close()

    run(main())
