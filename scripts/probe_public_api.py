"""Probe unauthenticated Ezykam public endpoints to confirm response shapes.

These are the same calls the browser makes on page load (before any login).
Nothing here requires or emits credentials. Output is printed, never stored.
"""
from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

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

PROBES = [
    ("config_info", "/api/customized/config/info", {"domain": "web.ezykam.com"}),
    ("web_app_info", "/api/customized/web/app/info", {}),
    ("app_info", "/api/customized/app/info", {}),
    ("oem_config", "/api/config/info", {}),
    ("jy_init", "/api/jy/init", {}),
    ("qrcode", "/api/customized/qrcode", {}),
    ("country_list", "/api/country/list", {}),
]


async def main() -> int:
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for name, path, payload in PROBES:
            print(f"\n=== {name}  POST {path} ===")
            try:
                async with session.post(BASE + path, json=payload) as resp:
                    text = await resp.text()
                    print(f"HTTP {resp.status}")
                    try:
                        body = json.loads(text)
                        print(json.dumps(body, indent=2)[:3000])
                    except json.JSONDecodeError:
                        print(text[:1000])
            except Exception as exc:  # noqa: BLE001 - probe tool, report everything
                print(f"ERROR: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
