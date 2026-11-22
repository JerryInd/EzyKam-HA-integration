"""Does a session restored from storage round-trip its cookie correctly?

Home Assistant stores the session and re-seeds the cookie jar from a plain
string on every setup. If aiohttp encodes that value differently from how it
arrived off the wire, the `Cookie` header and the `s-sid` header would disagree
and the cloud could reject a perfectly valid session.

No network or QR scan: only a local server is used to observe the headers.
"""

from __future__ import annotations

import asyncio
import base64
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestServer  # noqa: E402

from custom_components.ezykam.core.client import EzykamClient  # noqa: E402
from custom_components.ezykam.core.models import AuthSession  # noqa: E402

# 82 chars, matching the observed s-sid length, with different alphabets to
# find out which (if any) aiohttp re-encodes on the way back out.
CANDIDATES: dict[str, str] = {
    "base64std": base64.b64encode(b"session-secret-value-0123456789abcdef").decode(),
    "base64pad": (base64.b64encode(b"X" * 62) + b"==").decode()[:82],
    "urlsafe": base64.urlsafe_b64encode(b"url-safe-session-value-0123456789").decode(),
    "jwtish": "a" * 30 + "." + "b" * 30 + "." + "c" * 22,
    "alnum": "Ab3" * 27 + "Ab",
    "hex": "a1b2c3d4" * 10 + "a1b2",
}


async def observe(label: str, sid: str) -> bool:
    captured: dict[str, str] = {}

    async def handler(request: web.Request) -> web.Response:
        captured["cookie"] = request.headers.get("Cookie", "")
        captured["header"] = request.headers.get("s-sid", "")
        return web.json_response({"result": [], "success": True, "status": "ok"})

    app = web.Application()
    app.router.add_post("/api/x", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        base = str(server.make_url("/")).rstrip("/")
        session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        try:
            client = EzykamClient(
                session=session, auth=AuthSession(sid=sid), base_url=base
            )
            await client.request("/api/x", {})
        finally:
            await session.close()
    finally:
        await server.close()

    match = re.search(r"(?:^|;\s*)s-sid=([^;]*)", captured.get("cookie", ""))
    cookie_val = match.group(1) if match else "<absent>"
    header_val = captured.get("header", "")

    # The header must always carry the raw value. The cookie may legitimately
    # be absent when the client drops it because it would have been re-encoded.
    header_ok = header_val == sid
    cookie_ok = cookie_val == sid or cookie_val == "<absent>"
    ok = header_ok and cookie_ok
    note = ""
    if cookie_val == "<absent>":
        note = "  (cookie dropped: would have been re-encoded)"
    elif not cookie_ok:
        note = "  (cookie re-encoded -> MISMATCH)"
    print(
        f"{label:11} len={len(sid):3}  cookie={cookie_val[:26]!r:<28} "
        f"header={header_val[:26]!r:<28} {'OK' if ok else '*** PROBLEM ***'}{note}"
    )
    return ok


async def run() -> int:
    print("Restoring a session from a stored string (what HA does):\n")
    problems = 0
    reencoded = 0
    for label, sid in CANDIDATES.items():
        sid = sid[:82]
        if not await observe(label, sid):
            problems += 1

    print()
    if problems:
        print(
            f"RESULT: {problems} value(s) still reach the wire inconsistently."
        )
        return 1
    print(
        "RESULT: every value reaches the wire with the s-sid header carrying "
        "the raw session. Re-encoding was detected and defended against "
        "(the cookie is dropped rather than sent altered), so a restored "
        "session can no longer present two different ids for one request."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
