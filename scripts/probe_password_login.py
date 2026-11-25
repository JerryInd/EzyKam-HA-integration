"""Consolidated probe: is password login reachable for this tenant at all?

Read-only. Uses a deliberately fake account only, so the real one can never be
touched, locked out or rate-limited. No credential is printed or stored - only
the cloud's error codes and response key names.

This replaces the four narrower probe_password_*.py scripts by covering every
dimension in one pass:

  1. page-load cookies (gTyPlatLang / locale / fast-sid) - the portal always
     has these before it posts; earlier probes sent none
  2. body shapes: portal-exact, +api/version, +securekey, Tuya envelope
  3. query-string forms
  4. header spellings for api / api version
  5. alternate hosts advertised by the tenant config
  6. controls that must succeed, so a failure below means "gated" rather than
     "our transport is broken"

Decision rule: if the QR control succeeds on the same host with the same
headers while every password shape returns API_OR_API_VERSION_WRONG, the
endpoints are not routed for this tenant (loginConfig 0 == scan) and password
login cannot be implemented - regardless of the captcha.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from urllib.parse import urlencode

import aiohttp

BASE = "https://web.ezykam.com"

FAKE_USER = "ezykam-probe-does-not-exist@example.invalid"
FAKE_MD5 = hashlib.md5(b"definitely-not-the-password").hexdigest()
FAKE_SID = "s:00000000-0000-0000-0000-000000000000.probe-not-a-real-session"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json; charset=utf-8",
    "origin": BASE,
    "referer": BASE + "/login",
    "x-requested-with": "XMLHttpRequest",
    "cloud-type": "cpplus",
    "cpplus-region-code": "EU",
    "public-region-code": "",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    ),
}

LOGIN = {
    "countryCode": "91",
    "userName": FAKE_USER,
    "password": FAKE_MD5,
    "domain": "web.ezykam.com",
}


def _code(text: str) -> str:
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return f"non-json {text[:70]!r}"
    if body.get("success") is True:
        return "SUCCESS (unexpected for a fake account)"
    return str(body.get("errorCode", "-"))


async def _post(session: aiohttp.ClientSession, url: str, payload: dict, **kw) -> str:
    try:
        async with session.post(url, json=payload, **kw) as r:
            return _code(await r.text())
    except Exception as exc:  # noqa: BLE001 - probe tool, report everything
        return f"{type(exc).__name__}"


async def main() -> int:
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # Controls first: these must succeed, or nothing below means anything.
        print("--- controls (must succeed) ---")
        print(f"qrcode      -> {await _post(session, BASE + '/api/customized/qrcode', {})}")
        print(
            "config/info -> "
            f"{await _post(session, BASE + '/api/customized/config/info', {'domain': 'web.ezykam.com'})}"
        )

        # 1. cookies a real browser always has by the time it posts
        cookies = {"gTyPlatLang": "en", "locale": "en", "fast-sid": "probe"}
        print("\n--- 1. with page-load cookies ---")
        print(f"jy/init         -> {await _post(session, BASE + '/api/jy/init', {}, cookies=cookies)}")
        print(f"password/login  -> {await _post(session, BASE + '/api/password/login', LOGIN, cookies=cookies)}")

        # 2. body shapes
        print("\n--- 2. body shapes ---")
        shapes = {
            "portal-exact": LOGIN,
            "+api/version": {**LOGIN, "api": "password.login", "version": "1.0"},
            "+securekey": {**LOGIN, "securekey": json.dumps({"p": 1})},
            "tuya envelope": {"api": "password.login", "version": "1.0", "data": LOGIN},
        }
        for label, payload in shapes.items():
            print(f"password/login {label:15} -> {await _post(session, BASE + '/api/password/login', payload)}")
        for label, payload in {
            "portal-exact": {},
            "api/version": {"api": "jy.init", "version": "1.0"},
        }.items():
            print(f"jy/init {label:26} -> {await _post(session, BASE + '/api/jy/init', payload)}")

        # 3. query string
        print("\n--- 3. query string ---")
        qs = urlencode({"api": "jy.init", "version": "1.0"})
        print(f"jy/init?{qs} -> {await _post(session, f'{BASE}/api/jy/init?{qs}', {})}")

        # 4. header spellings
        print("\n--- 4. api/version as headers ---")
        for extra in (
            {"api": "jy.init", "version": "1.0"},
            {"x-api": "jy.init", "x-api-version": "1.0"},
            {"x-ty-appid": "web.ezykam.com", "x-ty-appver": "1.0"},
        ):
            session._headers = {**HEADERS, **extra}  # noqa: SLF001 - probe only
            label = ",".join(extra)
            print(f"{label:44} -> {await _post(session, BASE + '/api/jy/init', {})}")
        session._headers = dict(HEADERS)  # noqa: SLF001 - restore

        # 5. alternate hosts
        print("\n--- 5. alternate hosts ---")
        for host in (
            "https://ezykam-eu.cppluscloud.com",
            "https://hxp-cube.cppluscloud.com",
        ):
            ctrl = await _post(session, host + "/api/customized/qrcode", {})
            jy = await _post(session, host + "/api/jy/init", {})
            print(f"{host:40} control={ctrl:16} jy/init={jy}")

        # 6. the one routed sibling, for contrast
        print("\n--- 6. routed sibling, for contrast ---")
        print(f"app/check -> {await _post(session, BASE + '/api/app/check', {'appId': 'web.ezykam.com'})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
