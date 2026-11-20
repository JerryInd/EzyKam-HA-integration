"""Test whether starting a new QR login invalidates the current session.

Sequence (one QR scan required):
  1. QR login                    -> session established
  2. homeList                    -> expect OK
  3. POST /api/customized/qrcode -> mint a NEW login token
  4. homeList                    -> OK = hypothesis wrong, killed = right
  5. login/exchange              -> also mints; re-check

Everything is reported with redacted values only.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.ezykam.core import EzykamClient, QrLoginFlow  # noqa: E402
from custom_components.ezykam.core.errors import (  # noqa: E402
    EzykamError,
    SessionExpiredError,
)
from custom_components.ezykam.core.redact import mask  # noqa: E402

QR_PATH = REPO_ROOT / ".session" / "hypothesis_qr.png"


async def check(client: EzykamClient, label: str) -> bool:
    """True when homeList still accepts the session."""
    try:
        homes = await client.homes()
    except SessionExpiredError as exc:
        print(f"  {label:28} REJECTED ({exc.error_code})")
        return False
    except EzykamError as exc:
        print(f"  {label:28} error {type(exc).__name__}: {exc}")
        return False
    print(f"  {label:28} OK ({len(homes)} homes)")
    return True


async def run(timeout: float) -> int:
    client = EzykamClient()
    flow = QrLoginFlow(client, on_progress=lambda m: print(f"  * {m}"))
    try:
        print("Requesting QR ...")
        challenge = await flow.start()
        if challenge.image_png:
            QR_PATH.write_bytes(challenge.image_png)
            print(f"QR saved: {QR_PATH} ({len(challenge.image_png)} bytes)")
            try:
                import os

                os.startfile(QR_PATH)  # type: ignore[attr-defined]
            except (AttributeError, OSError):
                print(f"  open manually: {QR_PATH}")
        print(f"\n>>> Scan with the ezyKam+ app ({timeout:.0f}s) <<<\n")

        session = await flow.wait_for_approval(timeout=timeout)
        print(f"session established: {session!r}\n")

        print("=== step 2: baseline ===")
        if not await check(client, "homeList (baseline)"):
            return 1

        print("\n=== step 3: mint a NEW QR token ===")
        print(f"  calling /api/customized/qrcode ...")
        try:
            new_challenge = await client.qrcode()
            print(f"  new token minted fp={new_challenge.token_fingerprint}")
        except Exception as exc:  # noqa: BLE001
            print(f"  qrcode call failed: {type(exc).__name__}: {exc}")

        print("\n=== step 4: is the old session still valid? ===")
        alive_after_qrcode = await check(client, "homeList after qrcode")

        print("\n=== step 5: call login/exchange too ===")
        try:
            await client.login_exchange(new_challenge)
            print("  login/exchange            OK")
        except Exception as exc:  # noqa: BLE001
            print(f"  login/exchange            {type(exc).__name__}: {exc}")

        alive_after_exchange = await check(client, "homeList after exchange")

        print("\n=== RESULT ===")
        if not alive_after_qrcode:
            print(
                "CONFIRMED: minting a new QR token invalidates the current "
                "session. Retrying a login kills the session you just made."
            )
            return 2
        if not alive_after_exchange:
            print(
                "CONFIRMED: login/exchange invalidates the current session."
            )
            return 3
        print(
            "NOT CONFIRMED: the session survived both calls. Session expiry has "
            "a different cause."
        )
        return 0
    finally:
        await client.close()


def main() -> int:
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    return asyncio.run(run(timeout))


if __name__ == "__main__":
    raise SystemExit(main())
