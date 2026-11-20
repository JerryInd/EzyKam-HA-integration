"""Probe which Home Assistant WebSocket commands are registered.

Tries a set of candidate names and reports success/unauthorized/unknown, which
tells us what administrative surface this instance actually exposes.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.ha_ws import HA_URL, _token  # noqa: E402

CANDIDATES = [
    "hassio/addons/list",
    "hassio/addons",
    "hassio/info",
    "hassio/addon_info",
    "supervisor/addons/list",
    "config_entries/flow_handlers",
    "config/panels/list",
    "backup/info",
    "backup/list",
    "config_entries/get",
]


async def main() -> int:
    import aiohttp

    url = HA_URL.replace("http://", "ws://") + "/api/websocket"
    results: dict[str, str] = {}
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": _token()})
            auth = await ws.receive_json()
            if auth.get("type") != "auth_ok":
                print("auth failed:", auth)
                return 1
            print("auth_ok:", json.dumps({k: v for k, v in auth.items() if k != "type"}))

            next_id = 1
            for command in CANDIDATES:
                mid = next_id
                next_id += 1
                await ws.send_json({"id": mid, "type": command})
                while True:
                    reply = await ws.receive_json()
                    if reply.get("id") == mid:
                        if reply.get("success"):
                            payload = reply.get("result")
                            if isinstance(payload, list):
                                results[command] = f"OK ({len(payload)} items)"
                            else:
                                results[command] = "OK"
                        else:
                            results[command] = reply.get("error", {}).get("code", "error")
                        break

    for command, outcome in results.items():
        marker = "  " if outcome.startswith("OK") else "-> "
        print(f"{marker}{command:34} {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
