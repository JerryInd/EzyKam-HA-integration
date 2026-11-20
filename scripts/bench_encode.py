"""Benchmark the per-frame JPEG encode path used by the MJPEG stream.

Isolates colour conversion from JPEG encoding so we know whether the ~6 fps
ceiling comes from the pipeline or from the camera itself.
"""

from __future__ import annotations

import io
import time

import av
from PIL import Image

W, H = 640, 360
FRAMES = 30


def bench(label: str, fn) -> None:
    # warm up
    fn()
    start = time.perf_counter()
    for _ in range(FRAMES):
        fn()
    elapsed = time.perf_counter() - start
    per = elapsed / FRAMES
    print(f"{label:42} {per * 1000:7.1f} ms/frame  {1 / per:6.1f} fps")


def main() -> None:
    # A realistic decoded frame: YUV420P, what aiortc hands us after H.264.
    src = av.VideoFrame(width=W, height=H, format="yuv420p")
    for plane in src.planes:
        plane.update(bytes(plane.buffer_size))

    import numpy as np

    rgb = np.random.randint(0, 255, (H, W, 3), dtype=np.uint8)

    print(f"{W}x{H}, {FRAMES} frames each\n")

    def via_pil_from_av():
        arr = src.to_ndarray(format="rgb24")
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="JPEG", quality=82)
        buf.getvalue()

    def via_pil_direct():
        buf = io.BytesIO()
        Image.fromarray(rgb).save(buf, format="JPEG", quality=82)
        buf.getvalue()

    def yuv_to_rgb_only():
        src.to_ndarray(format="rgb24")

    def pil_only_rgb():
        buf = io.BytesIO()
        Image.fromarray(rgb).save(buf, format="JPEG", quality=82)

    try:
        bench("yuv420p -> rgb24 conversion only", yuv_to_rgb_only)
    except Exception as exc:  # noqa: BLE001
        print(f"conversion benchmark failed: {type(exc).__name__}: {exc}")
    try:
        bench("PIL JPEG encode only (rgb array)", pil_only_rgb)
    except Exception as exc:  # noqa: BLE001
        print(f"pil benchmark failed: {type(exc).__name__}: {exc}")
    try:
        bench("FULL path: av frame -> rgb -> JPEG", via_pil_from_av)
    except Exception as exc:  # noqa: BLE001
        print(f"full benchmark failed: {type(exc).__name__}: {exc}")
    try:
        bench("numpy rgb -> JPEG (control)", via_pil_direct)
    except Exception as exc:  # noqa: BLE001
        print(f"control failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
