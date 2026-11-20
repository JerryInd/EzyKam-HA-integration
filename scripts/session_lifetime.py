"""Measure how long an Ezykam session actually survives.

Polls an authenticated endpoint every N seconds until the cloud rejects it,
then reports the elapsed lifetime. Run this *without* starting any other QR
login: a second login invalidating the first would corrupt the measurement.

Usage:
    python scripts/session_lifetime.py .session/entry_storage.json --interval 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.ezykam.core import EzykamClient  # noqa: E402
from custom_components.ezykam.core.errors import SessionExpiredError  # noqa: E402
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


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


async def run(args: argparse.Namespace) -> int:
    auth = load_session(Path(args.storage))
    if auth is None:
        print("no ezykam session found - scan a QR first")
        return 1

    print(f"session: {auth!r}")
    print(f"polling every {args.interval}s for up to {args.max_minutes} min ...\n")

    client = EzykamClient(auth=auth, region=auth.region)
    started = time.monotonic()
    attempt = 0
    try:
        while True:
            attempt += 1
            elapsed = time.monotonic() - started
            if elapsed > args.max_minutes * 60:
                print(f"\nstill alive after {args.max_minutes} min - not expiring")
                return 0
            try:
                await client.homes()
                print(f"[{stamp()}] attempt {attempt}: OK   (+{elapsed:.0f}s)")
            except SessionExpiredError as exc:
                print(
                    f"\n[{stamp()}] attempt {attempt}: REJECTED after {elapsed:.0f}s "
                    f"({elapsed / 60:.1f} min)"
                )
                print(f"  code={exc.error_code} status={exc.status} path={exc.path}")
                print(f"\nRESULT: session lifetime ~= {elapsed:.0f}s ({elapsed / 60:.1f} min)")
                return 2
            except Exception as exc:  # noqa: BLE001 - transient errors don't end the test
                print(f"[{stamp()}] attempt {attempt}: {type(exc).__name__} (+{elapsed:.0f}s)")
            await asyncio.sleep(args.interval)
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "storage",
        nargs="?",
        default=str(REPO_ROOT / ".session" / "entry_storage.json"),
        help="HA core.config_entries JSON (see wsl/ssh fetch)",
    )
    parser.add_argument("--interval", type=float, default=20.0)
    parser.add_argument("--max-minutes", type=float, default=20.0)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
