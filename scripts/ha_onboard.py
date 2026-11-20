"""First-run bootstrap for the local Home Assistant test instance.

Completes onboarding (creating the admin user) and stores an access token in
the git-ignored token file. Idempotent: if already onboarded it just mints a
new token via the password grant.

Usage:
    python scripts/ha_onboard.py --ha-url http://127.0.0.1:8123
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOKEN_FILE = REPO_ROOT / ".session" / "ha_local_token"

ADMIN_USER = "ezykam-admin"
ADMIN_PASSWORD = "ezykam-local-ha-2026"  # local-only test instance


async def wait_ready(base_url: str, timeout: float = 300.0) -> bool:
    """Wait until the HTTP server answers.

    Any HTTP response counts as ready: before onboarding HA answers ``/api/``
    with 501, so gating on 200/401 would never fire.
    """
    import aiohttp

    deadline = time.monotonic() + timeout
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.get(base_url + "/api/") as resp:
                    if resp.status:
                        print(f"  server responding (GET /api/ -> {resp.status})")
                        return True
            except Exception:  # noqa: BLE001 - still booting
                pass
            await asyncio.sleep(2.0)
    return False


async def run(args: argparse.Namespace) -> int:
    import aiohttp

    base = args.ha_url.rstrip("/")
    print(f"waiting for {base} ...")
    if not await wait_ready(base):
        print("Home Assistant did not become ready in time")
        return 1
    print("Home Assistant is responding")

    async with aiohttp.ClientSession() as session:
        # 1. Onboarding — create the first (admin) user.
        async with session.post(
            base + "/api/onboarding/users",
            json={
                "client_id": base + "/",
                "name": "Ezykam Admin",
                "username": ADMIN_USER,
                "password": ADMIN_PASSWORD,
                "language": "en",
            },
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status == 200 and isinstance(body, dict) and body.get("access_token"):
                print(f"onboarded as {ADMIN_USER!r}")
                _save(args.token_file, body["access_token"])
                return 0
            if resp.status in (400, 403, 409):
                print("already onboarded; minting a token via password grant")
            else:
                print(f"onboarding response {resp.status}: {str(body)[:300]}")
                if not isinstance(body, dict) or not body.get("access_token"):
                    return 1

        # 2. Password grant for a fresh token.
        async with session.post(
            base + "/auth/token",
            data={
                "grant_type": "password",
                "client_id": base + "/",
                "username": ADMIN_USER,
                "password": ADMIN_PASSWORD,
            },
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200 or not body.get("access_token"):
                print(f"token grant failed {resp.status}: {str(body)[:300]}")
                return 1
            _save(args.token_file, body["access_token"])
            print(f"token stored in {args.token_file}")
            return 0


def _save(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        import os

        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-url", default="http://127.0.0.1:8123")
    parser.add_argument("--token-file", default=str(DEFAULT_TOKEN_FILE))
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
