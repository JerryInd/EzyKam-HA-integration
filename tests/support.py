"""Shared helpers for the Ezykam test suite.

Tests drive a real local ``aiohttp`` server rather than mocking ``aiohttp``
internals, so cookie capture, header injection and envelope parsing are all
exercised over a genuine HTTP exchange.
"""

from __future__ import annotations

import asyncio
import secrets
import string
import time
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.ezykam.core.client import EzykamClient
from custom_components.ezykam.core.models import AuthSession

T = TypeVar("T")

#: A real token captured from the live portal. Layout:
#: <region:2><created_ms:13><random:26><expires_ms:13><tail:2>, lifetime 300 s.
#: Its embedded expiry is in the past by now, so it is used only for
#: *parsing* assertions — never for a live flow test.
SAMPLE_QR_TOKEN = "EU1795149219663UBNcZPBDkFoMSdSmzUeKsohHDe179514951966304"

_ALPHABET = string.ascii_letters + string.digits


def make_token(*, ttl_ms: int = 300_000, region: str = "EU") -> str:
    """Build a structurally valid QR token with a *future* expiry.

    Mirrors the observed layout so the parser exercises the real code path,
    while remaining valid for the duration of a test run.
    """
    created = int(time.time() * 1000)
    random_part = "".join(secrets.choice(_ALPHABET) for _ in range(26))
    return f"{region}{created}{random_part}{created + ttl_ms}04"


def run(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion on a private event loop."""
    return asyncio.run(coro)


@asynccontextmanager
async def serve(app: web.Application) -> AsyncIterator[str]:
    """Start a throwaway local server and yield its base URL."""
    server = TestServer(app)
    await server.start_server()
    try:
        yield str(server.make_url("/")).rstrip("/")
    finally:
        await server.close()


def make_client(base_url: str, *, auth: AuthSession | None = None) -> EzykamClient:
    """Build a client that owns its own HTTP session.

    The cookie jar sets ``unsafe=True`` purely so the *local* test server can
    issue cookies to an IP-address host. Production traffic targets
    ``web.ezykam.com``, where the default (safe) jar applies.
    """
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    return EzykamClient(
        session=session, auth=auth, base_url=base_url, owns_session=True
    )


def ok(result: Any) -> web.Response:
    """Standard success envelope."""
    return web.json_response({"result": result, "t": 0, "success": True, "status": "ok"})


def err(error_code: str, *, status: int = 200, message: str = "error") -> web.Response:
    """Standard failure envelope."""
    return web.json_response(
        {
            "success": False,
            "errorCode": error_code,
            "errorMsg": message,
            "status": "error",
            "t": 0,
        },
        status=status,
    )
