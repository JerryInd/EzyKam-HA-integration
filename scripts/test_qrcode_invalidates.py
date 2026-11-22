"""Test what invalidates an Ezykam session (one QR scan required).

Sequence:
  1. QR login                      -> session established
  2. homeList                      -> baseline, expect OK
  3. POST /api/hybrid/getMsid      -> MQTT credentials (runs at every setup)
  4. homeList                      -> killed? getMsid is the culprit
  5. POST /api/customized/qrcode   -> mint a NEW login token
  6. homeList                      -> killed? a new QR login is the culprit
  7. login/exchange                -> re-check

getMsid is tested first because it runs during every Home Assistant setup,
which makes it the most likely cause of "session expired" shortly after start.
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

        print("\n=== step 3: call getMsid (runs at every HA setup) ===")
        try:
            creds = await client.request("/api/hybrid/getMsid", {})
            if isinstance(creds, dict):
                print(f"  getMsid keys={sorted(creds)} msid={mask(creds.get('msid'))}")
            else:
                print(f"  getMsid returned {type(creds).__name__}")
        except Exception as exc:  # noqa: BLE001
            print(f"  getMsid failed: {type(exc).__name__}: {exc}")

        print("\n=== step 4: is the session still valid after getMsid? ===")
        alive_after_msid = await check(client, "homeList after getMsid")

        print("\n=== step 5: mint a NEW QR token ===")
        print("  calling /api/customized/qrcode ...")
        new_challenge = None
        try:
            new_challenge = await client.qrcode()
            print(f"  new token minted fp={new_challenge.token_fingerprint}")
        except Exception as exc:  # noqa: BLE001
            print(f"  qrcode call failed: {type(exc).__name__}: {exc}")

        print("\n=== step 6: is the session still valid? ===")
        alive_after_qrcode = await check(client, "homeList after qrcode")

        print("\n=== step 7: call login/exchange too ===")
        if new_challenge is not None:
            try:
                await client.login_exchange(new_challenge)
                print("  login/exchange            OK")
            except Exception as exc:  # noqa: BLE001
                print(f"  login/exchange            {type(exc).__name__}: {exc}")
        alive_after_exchange = await check(client, "homeList after exchange")

        print("\n=== RESULT ===")
        if not alive_after_msid:
            print(
                "CONFIRMED: /api/hybrid/getMsid invalidates the session. "
                "It runs during every HA setup, which explains 'session "
                "expired' shortly after start."
            )
            return 2
        if not alive_after_qrcode:
            print(
                "CONFIRMED: minting a new QR token invalidates the current "
                "session. Retrying a login kills the session you just made."
            )
            return 3
        if not alive_after_exchange:
            print("CONFIRMED: login/exchange invalidates the current session.")
            return 4
        print(
            "NOT CONFIRMED: the session survived every call. Session expiry has "
            "a different cause (most likely a server-side lifetime limit)."
        )
        return 0
    finally:
        await client.close()


def main() -> int:
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    return asyncio.run(run(timeout))


if __name__ == "__main__":
    raise SystemExit(main())
