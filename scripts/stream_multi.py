"""Phase 6 gate: stream two cameras at the same time.

One MQTT connection is shared; each camera gets its own WebRTC session with its
own ``sessionid``/``motoId``, demultiplexed by the router. Frames from both are
saved as PNGs so simultaneous decoding can be inspected visually.

Usage:
    python scripts/stream_multi.py [--frames 4] [--timeout 60]
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
    SessionRouter,
    WebRtcSession,
)

SESSION_PATH = REPO_ROOT / ".session" / "ezykam_session.json"
OUTPUT_DIR = REPO_ROOT / ".session" / "frames_multi"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--count", type=int, default=2, help="cameras to stream")
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    if not SESSION_PATH.exists():
        print(f"No session at {SESSION_PATH}. Run scripts/qr_login.py first.")
        return 1

    auth = AuthSession.from_dict(json.loads(SESSION_PATH.read_text(encoding="utf-8")))
    client = EzykamClient(auth=auth, region=auth.region)

    try:
        inventory = await discover(client)
        cameras = [c for c in inventory.cameras if c.online][: args.count]
        if len(cameras) < 2:
            print(f"Need {args.count} online cameras, found {len(cameras)}")
            return 1

        print("targets:")
        for cam in cameras:
            print(f"  {cam.device_id}  {cam.name!r}")

        creds = await client.request("/api/hybrid/getMsid", {})
        msid, password = str(creds["msid"]), str(creds.get("password") or "")

        router = SessionRouter()
        mqtt = EzykamMqttClient(msid=msid, password=password, on_message=router.dispatch)
        mqtt.subscribe(user_topic(msid), qos=1)
        await mqtt.start()
        await mqtt.wait_connected(timeout=25)
        print(f"\none shared MQTT link: {mqtt.describe()}")
        print(f"router sessions registered: {len(router.session_ids)}")

        captured: dict[str, list] = {c.device_id: [] for c in cameras}
        done = {c.device_id: asyncio.Event() for c in cameras}

        def make_collector(device_id: str):
            async def on_frame(frame) -> None:  # noqa: ANN001
                bucket = captured[device_id]
                if len(bucket) >= args.frames:
                    return
                try:
                    bucket.append(frame.to_ndarray(format="rgb24"))
                except Exception as exc:  # noqa: BLE001
                    print(f"  [{device_id}] conversion failed: {type(exc).__name__}")
                    return
                if len(bucket) >= args.frames:
                    done[device_id].set()

            return on_frame

        sessions = [
            WebRtcSession(
                client=client,
                mqtt=mqtt,
                router=router,
                camera=cam,
                profile=PROFILE_MAIN,
                on_video_frame=make_collector(cam.device_id),
            )
            for cam in cameras
        ]

        print("\nnegotiating both cameras concurrently ...")
        started = time.monotonic()
        results = await asyncio.gather(
            *(s.start(connect_timeout=args.timeout) for s in sessions),
            return_exceptions=True,
        )
        elapsed = time.monotonic() - started
        print(f"both negotiated in {elapsed:.1f}s (concurrent, not serial)\n")

        ok = True
        for cam, session, result in zip(cameras, sessions, results):
            if isinstance(result, Exception):
                ok = False
                print(f"  {cam.device_id}: FAILED {type(result).__name__}: {result}")
                continue
            print(f"  {cam.device_id} ({cam.name!r}):")
            print(f"    {json.dumps(session.stats.as_dict())}")
            print(
                f"    ice path: "
                f"{json.dumps(session.stats.describe_ice_path())}"
            )

        print("\nwaiting for frames ...")
        try:
            await asyncio.wait_for(
                asyncio.gather(*(done[c.device_id].wait() for c in cameras)),
                timeout=args.timeout,
            )
        except asyncio.TimeoutError:
            print("timed out waiting for every camera")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        from PIL import Image

        print("\n=== RESULT ===")
        for cam in sessions:
            frames = captured[cam.camera.device_id]
            print(f"{cam.camera.device_id}: {len(frames)} frame(s) decoded")
            for index, array in enumerate(frames):
                path = OUTPUT_DIR / (
                    f"{cam.camera.device_id}-{stamp}-{index:02d}.png"
                )
                Image.fromarray(array).save(path)
                print(f"  saved {path}")
            if not frames:
                ok = False

        for session in sessions:
            await session.stop()
        await mqtt.stop()
        return 0 if ok else 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
