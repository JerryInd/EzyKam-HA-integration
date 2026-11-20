"""Run real camera discovery against the live Ezykam cloud.

This is the executable proof for Phase 3: it replays the stored session and
prints a redaction-safe inventory. No QR scan is required.

Usage:
    python scripts/discover_live.py
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
from custom_components.ezykam.core.redact import redact  # noqa: E402

SESSION_PATH = REPO_ROOT / ".session" / "ezykam_session.json"


async def main_async() -> int:
    if not SESSION_PATH.exists():
        print(f"No session at {SESSION_PATH}. Run scripts/qr_login.py first.")
        return 1

    auth = AuthSession.from_dict(json.loads(SESSION_PATH.read_text(encoding="utf-8")))
    client = EzykamClient(auth=auth, region=auth.region)
    try:
        print(f"Session: {client.auth!r}\n")
        result = await discover(client)
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        print(f"DISCOVERY FAILED: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await client.close()

    print("=== redaction-safe inventory ===")
    print(json.dumps(redact(result.as_dict()), indent=2, ensure_ascii=False))

    print("\n=== stream readiness ===")
    for cam in result.cameras:
        print(f"  {cam.device_id}  online={cam.online}  shared={cam.shared}  {cam.name!r}")

    print(f"\nCloud-Type rule: devIdPrefix={auth.dev_id_prefix!r}")
    for cam in result.cameras:
        print(
            f"  {cam.device_id} -> cloud-type="
            f"{client.cloud_type_for_device(cam.device_id)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
