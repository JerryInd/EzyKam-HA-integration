"""Minimal Home Assistant WebSocket API client for automation.

Used to drive config flows and inspect the running instance without a browser.
Credentials are read from the git-ignored token file and never printed.

Usage:
    python scripts/ha_ws.py <command> [json-args]
    python scripts/ha_ws.py hassio/addons/list
    python scripts/ha_ws.py config_entries/flow {"handler": "ezykam"}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN_FILE = REPO_ROOT / ".session" / "ha_token"
HA_URL = "http://homeassistant.local:8123"


def _token(path: Path | None = None) -> str:
    target = path or TOKEN_FILE
    if not target.exists():
        raise SystemExit(f"missing token file: {target}")
    raw = target.read_text(encoding="utf-8").strip()
    if raw.startswith("{"):
        import json as _json

        data = _json.loads(raw)
        token = data.get("access_token") or ""
        if not token:
            raise SystemExit(f"no access_token in {target}")
        return token
    return raw


async def call(
    command: str,
    args: dict | None = None,
    *,
    ha_url: str | None = None,
    token_file: Path | None = None,
) -> dict:
    import aiohttp

    base = ha_url or HA_URL
    url = base.replace("http://", "ws://").replace("https://", "wss://")
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url + "/api/websocket") as ws:
            first = await ws.receive_json()
            if first.get("type") != "auth_required":
                return {"error": f"unexpected handshake: {first}"}
            await ws.send_json({"type": "auth", "access_token": _token(token_file)})
            ok = await ws.receive_json()
            if ok.get("type") != "auth_ok":
                return {"error": f"auth failed: {ok.get('message')}"}

            msg: dict = {"id": 1, "type": command}
            if args:
                msg.update(args)
            await ws.send_json(msg)

            while True:
                reply = await ws.receive_json()
                if reply.get("id") == 1:
                    return reply
                # ignore events/subscriptions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", help="WebSocket command type")
    parser.add_argument("args", nargs="?", help="JSON arguments")
    parser.add_argument("--ha-url", default=None)
    parser.add_argument("--token-file", default=None)
    ns = parser.parse_args()
    if not ns.command:
        print(__doc__)
        return 1
    args = json.loads(ns.args) if ns.args else None
    token_file = Path(ns.token_file) if ns.token_file else None
    result = asyncio.run(
        call(ns.command, args, ha_url=ns.ha_url, token_file=token_file)
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
