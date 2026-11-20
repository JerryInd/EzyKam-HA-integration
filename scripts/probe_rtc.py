"""Fetch per-camera RTC configuration from the live cloud (Phase 5 input).

Replays the stored session, discovers cameras, then requests RTC config for
each online one. Output is redacted: ICE/TURN server *urls* are kept because
they are needed to reason about connectivity, while auth tokens, localKey and
TURN credentials are masked.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.ezykam.core import EzykamClient  # noqa: E402
from custom_components.ezykam.core.discovery import discover  # noqa: E402
from custom_components.ezykam.core.models import AuthSession  # noqa: E402
from custom_components.ezykam.core.redact import redact  # noqa: E402

SESSION_PATH = REPO_ROOT / ".session" / "ezykam_session.json"


def shape(value: Any, depth: int = 0) -> str:
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return "{" + ", ".join(sorted(str(k) for k in value)) + "}"
    if isinstance(value, list):
        inner = shape(value[0], depth + 1) if value else ""
        return f"[{len(value)}] {inner}"
    if isinstance(value, str):
        return f"str({len(value)})"
    return type(value).__name__


async def main_async() -> int:
    if not SESSION_PATH.exists():
        print(f"No session at {SESSION_PATH}. Run scripts/qr_login.py first.")
        return 1

    auth = AuthSession.from_dict(json.loads(SESSION_PATH.read_text(encoding="utf-8")))
    client = EzykamClient(auth=auth, region=auth.region)
    try:
        inventory = await discover(client)
        online = [c for c in inventory.cameras if c.online]
        print(f"cameras: {inventory.camera_count} total, {len(online)} online\n")

        if not online:
            print("No online cameras; nothing to query.")
            return 1

        target = online[0]
        print(f"=== rtc/config for {target.device_id} ({target.name!r}) ===")
        cfg = await client.rtc_config(target.device_id)
        print(f"shape: {shape(cfg)}")
        print(json.dumps(redact(cfg), indent=2, ensure_ascii=False)[:6000])

        if len(online) > 1:
            second = online[1]
            print(f"\n=== rtc/config for {second.device_id} ({second.name!r}) ===")
            cfg2 = await client.rtc_config(second.device_id)
            print(f"shape: {shape(cfg2)}")
            print(json.dumps(redact(cfg2), indent=2, ensure_ascii=False)[:6000])
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
