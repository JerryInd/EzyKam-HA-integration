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

        print("\n=== step 3: app/info (first call discover() makes) ===")
        try:
            info = await client.request("/api/customized/app/info", {})
            if isinstance(info, dict):
                print(f"  app/info keys={sorted(info)}")
            else:
                print(f"  app/info returned {type(info).__name__}")
        except Exception as exc:  # noqa: BLE001
            print(f"  app/info failed: {type(exc).__name__}: {exc}")
        alive_after_appinfo = await check(client, "homeList after app/info")

        print("\n=== step 4: getMsid (runs at every HA setup) ===")
        try:
            creds = await client.request("/api/hybrid/getMsid", {})
            if isinstance(creds, dict):
                print(f"  getMsid keys={sorted(creds)} msid={mask(creds.get('msid'))}")
            else:
                print(f"  getMsid returned {type(creds).__name__}")
        except Exception as exc:  # noqa: BLE001
            print(f"  getMsid failed: {type(exc).__name__}: {exc}")
        alive_after_msid = await check(client, "homeList after getMsid")

        print("\n=== step 5: mint a NEW QR token ===")
        print("  calling /api/customized/qrcode ...")
        new_challenge = None
        try:
            new_challenge = await client.qrcode()
            print(f"  new token minted fp={new_challenge.token_fingerprint}")
        except Exception as exc:  # noqa: BLE001
            print(f"  qrcode call failed: {type(exc).__name__}: {exc}")
        alive_after_qrcode = await check(client, "homeList after qrcode")

        print("\n=== step 6: call login/exchange too ===")
        if new_challenge is not None:
            try:
                await client.login_exchange(new_challenge)
                print("  login/exchange            OK")
            except Exception as exc:  # noqa: BLE001
                print(f"  login/exchange            {type(exc).__name__}: {exc}")
        alive_after_exchange = await check(client, "homeList after exchange")

        print("\n=== RESULT ===")
        verdicts = [
            (alive_after_appinfo, "app/info", 2),
            (alive_after_msid, "getMsid", 3),
            (alive_after_qrcode, "qrcode", 4),
            (alive_after_exchange, "login/exchange", 5),
        ]
        for alive, label, code in verdicts:
            if not alive:
                print(
                    f"CONFIRMED: {label} invalidates the session. It runs during "
                    "normal operation, which explains sessions dying ~7s after login."
                )
                return code
        print(
            "NOT CONFIRMED: the session survived every call. Expiry has a "
            "different cause (server-side lifetime, or another call in the path)."
        )
        return 0
    finally:
        await client.close()


def main() -> int:
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    return asyncio.run(run(timeout))


if __name__ == "__main__":
    raise SystemExit(main())
