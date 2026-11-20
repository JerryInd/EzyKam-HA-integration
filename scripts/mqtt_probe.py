"""Live Phase 4 gate: connect to the Ezykam MQTT broker over secure WebSocket.

Fetches fresh MQTT credentials, connects, subscribes to the account's user
topic, and reports whether the link stays up. Credentials are never printed —
only their lengths and the masked topic.
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
from custom_components.ezykam.core.compat import use_compatible_event_loop  # noqa: E402

# Must run before asyncio.run(): Windows' Proactor loop cannot service paho.
use_compatible_event_loop()

from custom_components.ezykam.core.models import AuthSession  # noqa: E402
from custom_components.ezykam.core.mqtt import (  # noqa: E402
    DEFAULT_BROKER_HOST,
    EzykamMqttClient,
    user_topic,
)
from custom_components.ezykam.core.redact import mask  # noqa: E402

SESSION_PATH = REPO_ROOT / ".session" / "ezykam_session.json"
LISTEN_SECONDS = 20.0


async def main_async() -> int:
    if not SESSION_PATH.exists():
        print(f"No session at {SESSION_PATH}. Run scripts/qr_login.py first.")
        return 1

    auth = AuthSession.from_dict(json.loads(SESSION_PATH.read_text(encoding="utf-8")))
    client = EzykamClient(auth=auth, region=auth.region)
    try:
        creds = await client.request("/api/hybrid/getMsid", {})
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        print(f"getMsid FAILED: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await client.close()

    if not isinstance(creds, dict) or not creds.get("msid"):
        print(f"getMsid returned no msid: type={type(creds).__name__}")
        return 1

    msid = str(creds["msid"])
    password = str(creds.get("password") or "")
    print(f"getMsid OK: msid={mask(msid)} password={mask(password)}")
    print(f"  ecode present: {bool(creds.get('ecode'))}")
    print(f"  broker: {DEFAULT_BROKER_HOST}/mqtt (wss)")

    received: list[tuple[str, dict]] = []

    async def on_message(topic: str, payload: dict) -> None:
        received.append((topic, payload))

    mqtt = EzykamMqttClient(
        msid=msid, password=password, on_message=on_message
    )
    topic = user_topic(msid)
    mqtt.subscribe(topic, qos=1)
    print(f"  subscribe: {mqtt.safe(topic)}")

    print("\nconnecting ...")
    await mqtt.start()
    try:
        await mqtt.wait_connected(timeout=25)
    except asyncio.TimeoutError:
        print("FAILED: did not connect within 25s")
        await mqtt.stop()
        return 1

    print(f"CONNECTED: {mqtt.describe()}")
    print(f"listening for {LISTEN_SECONDS:.0f}s ...")
    try:
        await asyncio.sleep(LISTEN_SECONDS)
    finally:
        await mqtt.stop()

    print(f"\nmessages received: {len(received)}")
    for t, p in received[:10]:
        print(f"  {mqtt.safe(t)} -> {mqtt.safe(json.dumps(p))}")

    print("\nRESULT: MQTT over WSS established and subscription active")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
