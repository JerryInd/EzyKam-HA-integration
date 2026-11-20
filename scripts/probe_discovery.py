"""Dump the structure of every discovery endpoint, with secrets redacted.

Phase 3 needs the real shape of homeList / roomList / shareList before device
normalisation can be written without guessing. This replays the stored session
(no QR scan required) and prints structure only: keys, types and non-sensitive
values. Anything matching a secret key name is masked.
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
from custom_components.ezykam.core.models import AuthSession  # noqa: E402
from custom_components.ezykam.core.redact import redact  # noqa: E402

SESSION_PATH = REPO_ROOT / ".session" / "ezykam_session.json"


def shape(value: Any, depth: int = 0) -> str:
    """Describe a value's type/length without dumping large payloads."""
    if isinstance(value, dict):
        return "{" + ", ".join(sorted(value)) + "}"
    if isinstance(value, list):
        return f"[{len(value)} items]" + ("" if not value else f" first={shape(value[0], depth+1)}")
    if isinstance(value, str):
        return f"str({len(value)})"
    return type(value).__name__


def dump(label: str, payload: Any) -> None:
    print(f"\n=== {label} ===")
    print(f"shape: {shape(payload)}")
    print(json.dumps(redact(payload), indent=2, ensure_ascii=False)[:4000])


async def _call(label: str, coro) -> Any:  # noqa: ANN001
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        print(f"\n=== {label} ===\nFAILED {type(exc).__name__}: {exc}")
        return None


async def main_async() -> int:
    if not SESSION_PATH.exists():
        print(f"No session at {SESSION_PATH}. Run scripts/qr_login.py first.")
        return 1

    auth = AuthSession.from_dict(json.loads(SESSION_PATH.read_text(encoding="utf-8")))
    client = EzykamClient(auth=auth, region=auth.region)
    try:
        # The portal calls this one immediately after login (module K.getAppInfo).
        app_info = await _call("customized/app/info", client.request("/api/customized/app/info", {}))
        dump("customized/app/info", app_info)

        web_info = await _call("web/app/info", client.web_app_info())
        dump("customized/web/app/info", web_info)

        homes = await _call("homeList", client.homes())
        dump("homeList", homes)

        if isinstance(homes, list):
            for home in homes:
                if not isinstance(home, dict):
                    continue
                for key in ("gid", "groupId", "id"):
                    home_id = home.get(key)
                    if home_id is None:
                        continue
                    rooms = await _call(
                        f"roomList({key}={home_id})",
                        client.home_devices(str(home_id)),
                    )
                    dump(f"roomList via {key}={home_id}", rooms)

        shared = await _call("shareList", client.shared_devices())
        dump("shareList", shared)
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
