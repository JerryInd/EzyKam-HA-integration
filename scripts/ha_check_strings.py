"""Check what Home Assistant actually serves for a translation string.

Verifies the deployed fix end to end rather than assuming a file copy plus a
restart was enough: the browser renders whatever this returns.

Usage:
    python scripts/ha_check_strings.py --ha-url http://<host>:8123 \
        --token-file .session/ha_token
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


async def run(args: argparse.Namespace) -> int:
    import aiohttp

    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if token.startswith("{"):
        token = json.loads(token)["access_token"]

    ws_url = args.ha_url.rstrip("/").replace("http://", "ws://") + "/api/websocket"

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            auth = await ws.receive_json()
            if auth.get("type") != "auth_ok":
                print(f"auth failed: {auth.get('message')}")
                return 1

            await ws.send_json(
                {
                    "id": 1,
                    "type": "frontend/get_translations",
                    "language": "en",
                    "category": "config",
                    "integration": "ezykam",
                }
            )
            while True:
                reply = await ws.receive_json()
                if reply.get("id") != 1:
                    continue
                if not reply.get("success"):
                    print(f"command failed: {json.dumps(reply.get('error'))}")
                    return 1
                result = reply.get("result") or {}
                break

    # HA nests translations under <language>/<integration>/...
    blob = json.dumps(result, ensure_ascii=False)
    print(f"response keys: {sorted(result)[:10]}")

    needle = "component.ezykam.config.step.reauth_confirm.description"
    found = None

    def walk(node: object, path: str = "") -> None:
        nonlocal found
        if isinstance(node, dict):
            for key, value in node.items():
                if key == needle or path.endswith(needle):
                    found = value
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    walk(result)

    if found is None:
        print(f"\n{needle}: NOT FOUND in the response")
        print("raw snippet:", blob[:400])
        return 1

    print(f"\n{needle}:")
    print(f"  {found}")
    if "{qr_image}" in str(found):
        print("\nRESULT: STILL BROKEN - old strings are being served")
        return 2
    print("\nRESULT: fixed strings are being served")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    parser.add_argument("--token-file", default=str(REPO_ROOT / ".session" / "ha_token"))
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
