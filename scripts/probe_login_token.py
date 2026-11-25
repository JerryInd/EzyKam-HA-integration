"""Probe /api/login/token - the one routed endpoint that might renew a session.

Read-only and deliberately UNAUTHENTICATED, using only a fake sid, so this can
never rotate or invalidate the session Home Assistant is currently using.
No credential is printed or stored: only the cloud's error codes come back.

Why this matters more than password login:

* /api/jy/init and /api/password/login are gated off for this tenant - every
  payload, header, query-string and host shape returns API_OR_API_VERSION_WRONG
  while the QR control on the same host succeeds.
* /api/login/token IS routed: a bare {} gave REMOTE_API_PARAM_ALL_INPUT_LOSS
  ("all input params missing") rather than "not routed".
* The portal defines it as getLoginToken but never calls it - dead code there,
  but a live endpoint here.

If it accepts a session and returns a fresh one, the 3-day expiry is solvable
without a password at all.
"""

from __future__ import annotations

import asyncio
import json

import aiohttp

BASE = "https://web.ezykam.com"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json; charset=utf-8",
    "x-requested-with": "XMLHttpRequest",
    "cloud-type": "cpplus",
    "cpplus-region-code": "EU",
    "public-region-code": "",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    ),
}

#: A sid that is deliberately not real. This probe must never be able to touch
#: the live session Home Assistant holds.
FAKE_SID = "s:00000000-0000-0000-0000-000000000000.probe-not-a-real-session"

PARAM_SHAPES: list[tuple[str, dict]] = [
    ("bare {}", {}),
    ("sid", {"sid": FAKE_SID}),
    ("token", {"token": FAKE_SID}),
    ("uid", {"uid": "probe-not-a-real-uid"}),
    ("clientId", {"clientId": "probe"}),
    ("sid+uid", {"sid": FAKE_SID, "uid": "probe-not-a-real-uid"}),
    ("refresh", {"refreshToken": FAKE_SID}),
]


def _summarise(text: str) -> str:
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return f"non-json {text[:100]!r}"
    # Deliberately never echo the body: a success payload would contain a
    # session credential. Only shape and error code are safe to report.
    keys = sorted(body) if isinstance(body, dict) else []
    return (
        f"success={body.get('success')} code={body.get('errorCode', '-')} "
        f"top-keys={keys}"
    )


async def main() -> int:
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for label, payload in PARAM_SHAPES:
            async with session.post(BASE + "/api/login/token", json=payload) as r:
                print(f"{label:14} -> {_summarise(await r.text())}")

        print("\n--- same shapes WITH a fake s-sid cookie, still not the real one ---")
        cookies = {"s-sid": FAKE_SID}
        for label, payload in (("sid", {"sid": FAKE_SID}), ("bare {}", {})):
            async with session.post(
                BASE + "/api/login/token", json=payload, cookies=cookies
            ) as r:
                print(f"{label:14} -> {_summarise(await r.text())}")

        # Control proves the transport and headers are healthy throughout.
        async with session.post(BASE + "/api/customized/qrcode", json={}) as r:
            print(f"\ncontrol qrcode -> {_summarise(await r.text())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
