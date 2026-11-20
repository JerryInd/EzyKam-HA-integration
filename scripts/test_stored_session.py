"""Test a session stored in a Home Assistant config entry against the cloud.

Answers definitively whether an "Ezykam session expired" failure means the
session is genuinely dead or was a transient error.

Usage:
    python scripts/test_stored_session.py .session/entry_storage.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.ezykam.core import EzykamClient  # noqa: E402
from custom_components.ezykam.core.discovery import discover  # noqa: E402
from custom_components.ezykam.core.models import AuthSession  # noqa: E402


def load_session(path: Path) -> AuthSession | None:
    with open(path, encoding="utf-8-sig") as handle:
        data = json.load(handle)
    for entry in data.get("data", {}).get("entries", []):
        if entry.get("domain") == "ezykam":
            session = (entry.get("data") or {}).get("session")
            if session:
                return AuthSession.from_dict(session)
    return None


async def run(path: Path) -> int:
    auth = load_session(path)
    if auth is None:
        print("no ezykam session found in the storage file")
        return 1

    print(f"session: {auth!r}")

    client = EzykamClient(auth=auth, region=auth.region)
    try:
        print("\n-- direct API calls --")
        for label, coro in (
            ("homeList", client.homes()),
            ("web/app/info", client.web_app_info()),
            ("app/info (devIdPrefix)", client.request("/api/customized/app/info", {})),
        ):
            try:
                result = await coro
                if isinstance(result, list):
                    print(f"  {label}: OK ({len(result)} items)")
                elif isinstance(result, dict):
                    print(f"  {label}: OK keys={sorted(result)[:10]}")
                else:
                    print(f"  {label}: OK {type(result).__name__}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {label}: FAILED {type(exc).__name__}: {exc}")

        print("\n-- full discovery --")
        try:
            inventory = await discover(client)
            print(f"  homes={len(inventory.homes)} cameras={inventory.camera_count} "
                  f"online={inventory.online_count}")
            for cam in inventory.cameras:
                print(f"    {cam.device_id}  online={cam.online}  {cam.name!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  discovery FAILED {type(exc).__name__}: {exc}")
            return 1
    finally:
        await client.close()

    print("\nRESULT: stored session is VALID (the setup_error was transient)")
    return 0


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / ".session" / "entry_storage.json"
    return asyncio.run(run(target))


if __name__ == "__main__":
    raise SystemExit(main())
