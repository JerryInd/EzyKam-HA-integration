"""Measure the MJPEG live-stream frame rate served by Home Assistant.

Requests /api/camera_proxy_stream/{entity_id} and counts JPEG SOI markers
over a fixed window, reporting the effective frames per second. This is the
number the UI experiences, so it is the one worth measuring.

Usage:
    python scripts/ha_stream_test.py --entity camera.shop_inside --seconds 6
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

JPEG_SOI = b"\xff\xd8\xff"
JPEG_EOI = b"\xff\xd9"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    parser.add_argument("--token-file", default=str(REPO_ROOT / ".session" / "ha_token"))
    parser.add_argument("--entity", default="camera.shop_inside")
    parser.add_argument("--seconds", type=float, default=6.0, help="measurement window")
    parser.add_argument("--warmup", type=float, default=40.0, help="max wait for first frame")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    import aiohttp

    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if token.startswith("{"):
        token = json.loads(token)["access_token"]

    url = f"{args.ha_url.rstrip('/')}/api/camera_proxy_stream/{args.entity}"
    print(f"GET {url}")

    # Two phases: warm-up (the stream must start WebRTC first) and then a
    # measurement window. Timing from connection open would attribute the
    # negotiation cost to the frame rate and understate it badly.
    warmup_started = time.monotonic()
    first_frame_at: float | None = None
    frames = 0
    bytes_read = 0
    measure_started: float | None = None
    measured_frames = 0

    async with aiohttp.ClientSession(
        headers={"Authorization": f"Bearer {token}"}
    ) as session:
        try:
            timeout = aiohttp.ClientTimeout(total=args.warmup + args.seconds + 15)
            async with session.get(url, timeout=timeout) as resp:
                print(f"HTTP {resp.status}  content-type={resp.headers.get('Content-Type')}")
                if resp.status != 200:
                    body = await resp.text()
                    print(f"body: {body[:300]}")
                    return 1

                async for chunk in resp.content.iter_chunked(4096):
                    now = time.monotonic()
                    count = chunk.count(JPEG_SOI)
                    bytes_read += len(chunk)
                    if count and first_frame_at is None:
                        first_frame_at = now - warmup_started
                        measure_started = now
                        print(f"  first frame after {first_frame_at:.2f}s - measuring ...")
                    if measure_started is None:
                        continue
                    if now - measure_started >= args.seconds:
                        break
                    frames += count
                    measured_frames += count
        except asyncio.TimeoutError:
            pass
        except Exception as exc:  # noqa: BLE001
            print(f"stream error: {type(exc).__name__}: {exc}")

    measure_elapsed = (
        (time.monotonic() - measure_started) if measure_started else 0.0
    )
    fps = measured_frames / measure_elapsed if measure_elapsed else 0.0

    print(f"\n=== {args.entity} ===")
    print(f"time to first frame        : "
          f"{'%.2fs' % first_frame_at if first_frame_at is not None else 'never'}")
    print(f"measurement window         : {measure_elapsed:.1f}s")
    print(f"frames measured            : {measured_frames}")
    print(f"bytes received             : {bytes_read}")
    print(f"effective frame rate       : {fps:.1f} fps")

    if measured_frames == 0:
        print("\nRESULT: no frames during the measurement window")
        return 1
    print(f"\nRESULT: streaming at {fps:.1f} fps")
    return 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
