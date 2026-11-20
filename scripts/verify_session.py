"""Restore a stored Ezykam session and exercise authenticated endpoints.

No QR scan is required: this replays the session saved by ``qr_login.py`` so
the session can be validated (and re-validated after code changes) on demand.

Output is redacted — cookie values, sids and usernames are never printed.
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
from custom_components.ezykam.core.models import AuthSession  # noqa: E402

SESSION_PATH = REPO_ROOT / ".session" / "ezykam_session.json"


async def attempt(label: str, coro) -> object:  # noqa: ANN001 - heterogeneous results
    """Run one call, reporting outcome without leaking response contents."""
    try:
        result = await coro
    except Exception as exc:  # noqa: BLE001 - diagnostic tool, report everything
        print(f"  {label}: FAILED {type(exc).__name__}: {exc}")
        return None
    if isinstance(result, dict):
        print(f"  {label}: OK  keys={sorted(result)}")
    elif isinstance(result, list):
        print(f"  {label}: OK  {len(result)} item(s)")
    else:
        print(f"  {label}: OK  {type(result).__name__}")
    return result


async def main_async() -> int:
    if not SESSION_PATH.exists():
        print(f"No session at {SESSION_PATH}. Run scripts/qr_login.py first.")
        return 1

    raw = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    auth = AuthSession.from_dict(raw)
    print(f"Loaded session: {auth!r}")

    client = EzykamClient(auth=auth, region=auth.region)
    try:
        print("\n-- authenticated calls --")
        info = await attempt("web/app/info", client.web_app_info())
        print(f"  last set-cookie names: {[c['name'] for c in client.last_set_cookies]}")

        homes = await attempt("homeList", client.homes())
        if isinstance(homes, list):
            for home in homes[:5]:
                if isinstance(home, dict):
                    safe = {
                        k: v
                        for k, v in home.items()
                        if str(k).lower()
                        in {"gid", "id", "groupid", "name", "housename", "groupname"}
                    }
                    print(f"    home: {safe}")

        if isinstance(homes, list):
            for home in homes:
                if not isinstance(home, dict):
                    continue
                home_id = home.get("gid") or home.get("id") or home.get("groupId")
                if home_id is None:
                    continue
                rooms = await attempt(f"roomList({home_id})", client.rooms(str(home_id)))
                if isinstance(rooms, list):
                    for room in rooms[:8]:
                        if isinstance(room, dict):
                            print(
                                "    room keys:",
                                sorted(room)[:20],
                            )
                            print(
                                "    room sample:",
                                {
                                    k: v
                                    for k, v in room.items()
                                    if str(k).lower()
                                    in {
                                        "rid",
                                        "id",
                                        "roomId",
                                        "name",
                                        "roomName",
                                        "devId",
                                        "deviceId",
                                    }
                                },
                            )
                break

        await attempt("shareList", client.shared_devices())
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
