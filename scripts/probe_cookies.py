"""Discover which Ezykam endpoints issue which cookies.

Values are always masked: we only care about cookie *names*, domains and flags.
No credentials are involved (these are pre-login endpoints).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiohttp
from yarl import URL

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.ezykam.core.redact import mask  # noqa: E402

BASE = "https://web.ezykam.com"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json; charset=utf-8",
    "origin": BASE,
    "referer": BASE + "/login",
    "x-requested-with": "XMLHttpRequest",
    "cloud-type": "cpplus",
    "cpplus-region-code": "",
    "public-region-code": "",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    ),
}


def describe(response: aiohttp.ClientResponse, *, label: str) -> None:
    raw = response.headers.getall("Set-Cookie", [])
    print(f"\n--- {label}: HTTP {response.status}, {len(raw)} Set-Cookie header(s)")
    if not raw:
        print("    (none)")
    for item in raw:
        parts = [p.strip() for p in item.split(";")]
        name_value = parts[0]
        name, _, value = name_value.partition("=")
        attrs = ", ".join(p for p in parts[1:] if p)
        print(f"    name={name!r} value={mask(value)}  [{attrs}]")


async def main() -> int:
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # 1. The very first thing the browser does: load the login page.
        async with session.get(BASE + "/login") as resp:
            await resp.text()
            describe(resp, label="GET /login")

        # 2. Config and QR token, as the portal calls them immediately after.
        async with session.post(
            BASE + "/api/customized/config/info", json={"domain": "web.ezykam.com"}
        ) as resp:
            await resp.text()
            describe(resp, label="POST /api/customized/config/info")

        async with session.post(BASE + "/api/customized/qrcode", json={}) as resp:
            body = await resp.json(content_type=None)
            describe(resp, label="POST /api/customized/qrcode")
        token = (body or {}).get("result", {}).get("token")
        if not token:
            print("\nno token; stopping")
            return 1

        # 3. Pending poll (nobody has scanned yet) — this is the endpoint that
        #    will later carry the authenticated session.
        async with session.post(
            BASE + "/api/customized/login/poll", json={"token": token}
        ) as resp:
            await resp.text()
            describe(resp, label="POST /api/customized/login/poll (pending)")

        # 4. What does the jar look like after all of the above?
        cookies = session.cookie_jar.filter_cookies(URL(BASE + "/"))
        print("\n--- accumulated cookie jar for web.ezykam.com")
        if not cookies:
            print("    (empty)")
        for name, morsel in cookies.items():
            print(f"    {name!r} = {mask(morsel.value)}  domain={morsel.domain!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
