"""Fetch Home Assistant diagnostics for the ezykam entry and prove redaction.

Downloads the real diagnostics payload over the WebSocket API, then checks it
against the actual secret values stored in the config entry. Any match means a
leak; zero matches means redaction held on real HA output.

Usage:
    python scripts/ha_diagnostics.py --ha-url http://<host>:8123 \
        --token-file .session/ha_local_token.json
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

SECRETS_FILE = REPO_ROOT / ".session" / "entry_secrets.json"
OUT_FILE = REPO_ROOT / ".session" / "ha_diagnostics.json"


async def fetch_diagnostics(ha_url: str, token_file: Path, entry_id: str) -> dict:
    """Download the diagnostics JSON file (REST view, not a WS command)."""
    import aiohttp

    url = ha_url.rstrip("/") + f"/api/diagnostics/config_entry/{entry_id}"
    token = token_file.read_text(encoding="utf-8").strip()
    if token.startswith("{"):
        token = json.loads(token)["access_token"]

    async with aiohttp.ClientSession(
        headers={"Authorization": f"Bearer {token}"}
    ) as session:
        async with session.get(url) as resp:
            text = await resp.text()
            if resp.status != 200:
                return {"error": f"HTTP {resp.status}: {text[:400]}"}
            try:
                return {"success": True, "result": json.loads(text)}
            except json.JSONDecodeError as err:
                return {"error": f"non-JSON response: {text[:300]!r}"}


async def run(args: argparse.Namespace) -> int:
    result = await fetch_diagnostics(args.ha_url, Path(args.token_file), args.entry_id)
    if not result.get("success"):
        print(f"diagnostics failed: {json.dumps(result)[:500]}")
        return 1

    payload = result.get("result") or {}
    text = json.dumps(payload, indent=2, default=str)
    OUT_FILE.write_text(text, encoding="utf-8")
    print(f"diagnostics saved: {OUT_FILE} ({len(text)} bytes)")

    # Top-level shape only; the point is what is NOT in there.
    if isinstance(payload, dict):
        print("top-level keys:", sorted(payload))

    if not SECRETS_FILE.exists():
        print(
            "\n(no entry_secrets.json - run scripts/wsl_dump_secrets.sh first "
            "to enable leak checking)"
        )
        return 0

    secrets = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    leaks = [
        name
        for name, value in secrets.items()
        if isinstance(value, str) and value and value in text
    ]
    print("\n== redaction check against real stored secrets ==")
    for name in secrets:
        print(f"  {name}: {'LEAKED' if name in leaks else 'not present'}")

    if leaks:
        print(f"\nFAILED: {leaks} appear in the diagnostics dump")
        return 1
    print("\nPASSED: no stored secret appears in the diagnostics dump")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    parser.add_argument(
        "--token-file", default=str(REPO_ROOT / ".session" / "ha_local_token.json")
    )
    parser.add_argument("--entry-id", required=True)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
