"""Phase 5 gate: deliver a real camera image to a test player.

Connects to an online camera over the cloud (no RTSP/ONVIF/local IP), negotiates
WebRTC through MQTT signaling, decodes incoming H.264 frames and writes them to
PNG files. If a PNG contains a real picture, streaming works.

Usage:
    python scripts/stream_test.py [--camera DEV_ID] [--profile main|sub] [--frames 5]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.ezykam.core import EzykamClient  # noqa: E402
from custom_components.ezykam.core.compat import use_compatible_event_loop  # noqa: E402

# Must run before asyncio.run(): Windows' Proactor loop cannot service paho.
use_compatible_event_loop()

from custom_components.ezykam.core.discovery import discover  # noqa: E402
from custom_components.ezykam.core.models import AuthSession  # noqa: E402
from custom_components.ezykam.core.mqtt import EzykamMqttClient, user_topic  # noqa: E402
from custom_components.ezykam.core.webrtc import (  # noqa: E402
    PROFILE_MAIN,
    PROFILE_SUB,
    SessionRouter,
    WebRtcSession,
)

SESSION_PATH = REPO_ROOT / ".session" / "ezykam_session.json"
OUTPUT_DIR = REPO_ROOT / ".session" / "frames"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", help="devId to stream (default: first online camera)")
    parser.add_argument("--profile", choices=[PROFILE_MAIN, PROFILE_SUB], default=PROFILE_MAIN)
    parser.add_argument("--frames", type=int, default=5, help="frames to capture")
    parser.add_argument("--timeout", type=float, default=45.0, help="overall seconds to wait")
    parser.add_argument(
        "--force-relay",
        action="store_true",
        help="restrict ICE to TURN only, proving the cloud relay path works",
    )
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    if not SESSION_PATH.exists():
        print(f"No session at {SESSION_PATH}. Run scripts/qr_login.py first.")
        return 1

    auth = AuthSession.from_dict(json.loads(SESSION_PATH.read_text(encoding="utf-8")))
    client = EzykamClient(auth=auth, region=auth.region)

    try:
        inventory = await discover(client)
        online = [c for c in inventory.cameras if c.online]
        if args.camera:
            camera = next((c for c in inventory.cameras if c.device_id == args.camera), None)
            if camera is None:
                print(f"Camera {args.camera} not found in inventory")
                return 1
        else:
            camera = online[0] if online else None

        if camera is None:
            print("No online camera available")
            return 1

        print(f"target: {camera.device_id}  {camera.name!r}  online={camera.online}")

        creds = await client.request("/api/hybrid/getMsid", {})
        if not isinstance(creds, dict) or not creds.get("msid"):
            print("getMsid returned no credentials")
            return 1
        msid, password = str(creds["msid"]), str(creds.get("password") or "")

        router = SessionRouter()
        mqtt = EzykamMqttClient(msid=msid, password=password, on_message=router.dispatch)
        mqtt.subscribe(user_topic(msid), qos=1)
        await mqtt.start()
        await mqtt.wait_connected(timeout=25)
        print(f"MQTT connected: {mqtt.describe()}")

        captured: list = []
        done = asyncio.Event()

        async def on_frame(frame) -> None:  # noqa: ANN001 - aiortc VideoFrame
            if len(captured) >= args.frames:
                return
            try:
                captured.append(frame.to_ndarray(format="rgb24"))
            except Exception as exc:  # noqa: BLE001
                print(f"  frame conversion failed: {type(exc).__name__}: {exc}")
                return
            print(f"  frame {len(captured)}/{args.frames}: {frame.width}x{frame.height}")
            if len(captured) >= args.frames:
                done.set()

        session = WebRtcSession(
            client=client,
            mqtt=mqtt,
            router=router,
            camera=camera,
            profile=args.profile,
            on_video_frame=on_frame,
            ice_transport_policy="relay" if args.force_relay else None,
        )

        if args.force_relay:
            print("ICE policy: relay ONLY (cloud relay proof run)")

        print("\nnegotiating ...")
        started = time.monotonic()
        try:
            stats = await session.start(connect_timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001 - report and exit
            print(f"NEGOTIATION FAILED: {type(exc).__name__}: {exc}")
            print(f"partial stats: {json.dumps(session.stats.as_dict(), indent=2)}")
            await session.stop()
            await mqtt.stop()
            await client.close()
            return 1

        print(f"connected in {time.monotonic() - started:.1f}s")
        print(f"stats: {json.dumps(stats.as_dict(), indent=2)}")
        print(
            "ice path: "
            + json.dumps(stats.describe_ice_path(forced_relay=args.force_relay))
        )

        try:
            await asyncio.wait_for(done.wait(), timeout=args.timeout)
        except asyncio.TimeoutError:
            print(f"timed out waiting for frames (got {len(captured)})")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        saved = []
        for index, array in enumerate(captured):
            from PIL import Image

            path = OUTPUT_DIR / f"{camera.device_id}-{args.profile}-{stamp}-{index:02d}.png"
            Image.fromarray(array).save(path)
            saved.append(path)

        print(f"\n=== RESULT ===")
        print(f"frames decoded : {len(captured)}")
        print(f"final stats    : {json.dumps(session.stats.as_dict())}")
        for path in saved:
            print(f"saved          : {path}")

        await session.stop()
        await mqtt.stop()
        return 0 if captured else 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
