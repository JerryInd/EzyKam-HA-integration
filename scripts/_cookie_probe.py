"""Determine how aiohttp serialises a cookie, so the check matches the wire."""

from __future__ import annotations

import asyncio
import base64

import aiohttp
from yarl import URL

SID = base64.b64encode(b"session-secret-value-0123456789abcdef").decode()


async def main() -> None:
    print(f"sid sample: {SID[:40]}  len={len(SID)}\n")
    jar = aiohttp.CookieJar()
    jar.update_cookies({"s-sid": SID}, response_url=URL("https://example.com/"))
    fc = jar.filter_cookies(URL("https://example.com/"))
    print("filter_cookies value == raw :", fc["s-sid"].value == SID)
    print("Morsel.OutputString()      :", repr(fc["s-sid"].OutputString()[:70]))
    print("BaseCookie.output(header=''):", repr(fc.output(header="")[:70]))
    print("BaseCookie.output()        :", repr(fc.output()[:70]))


if __name__ == "__main__":
    asyncio.run(main())
