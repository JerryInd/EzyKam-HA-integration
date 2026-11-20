"""Interactive QR login for the CP Plus Ezykam cloud.

Runs the verified portal login flow, shows you the QR code, waits for you to
scan it with the ezyKam+ mobile app, then stores the resulting session.

The account password is never handled. No secret is ever printed.

Usage:
    python scripts/qr_login.py --verify

Session files are written with owner-only permissions and are git-ignored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.ezykam.core import (  # noqa: E402
    EzykamClient,
    QrLoginFlow,
    QrLoginError,
    redact,
)

DEFAULT_SESSION_PATH = REPO_ROOT / ".session" / "ezykam_session.json"
DEFAULT_QR_DIR = REPO_ROOT / ".session"


def _fresh_qr_path() -> Path:
    """Return a unique QR path for this run.

    The default image viewer keeps the previous PNG open, which makes Windows
    refuse to overwrite it (Errno 22). A per-run name sidesteps that lock, and
    stale files are cleared best-effort so the folder does not grow forever.
    """
    DEFAULT_QR_DIR.mkdir(parents=True, exist_ok=True)
    for stale in DEFAULT_QR_DIR.glob("qr*.png"):
        try:
            stale.unlink()
        except OSError:
            pass  # still open in a viewer; harmless
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return DEFAULT_QR_DIR / f"qr-{stamp}.png"


def png_size(data: bytes) -> tuple[int, int] | None:
    """Read width/height from a PNG's IHDR chunk, without any dependency."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _restrict(path: Path) -> None:
    """Owner-only permissions where the platform supports them."""
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows without ACL support
        pass


def _open(path: Path) -> None:
    """Show the QR image to the user with the OS default viewer."""
    try:
        os.startfile(path)  # type: ignore[attr-defined]  # Windows only
    except (AttributeError, OSError):
        print(f"  (open this file manually: {path})")


def _save_session(path: Path, client: EzykamClient) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(client.auth.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _restrict(path)


async def _verify(client: EzykamClient) -> None:
    """Prove the session actually works against authenticated endpoints."""
    print("\n-- verifying session against the cloud --")
    print(f"  session established (s-sid present): {client.is_authenticated}")

    async def note(label: str, response_cookies: list) -> None:
        names = [c["name"] for c in response_cookies]
        print(f"  {label} set-cookie names: {names or '(none)'}")

    try:
        info = await client.web_app_info()
        await note("web/app/info", client.last_set_cookies)
    except Exception as exc:  # noqa: BLE001 - report, do not crash the login
        print(f"  web/app/info failed: {type(exc).__name__}: {exc}")
        await note("web/app/info", client.last_set_cookies)
        info = None
    if isinstance(info, dict):
        print(f"  app info keys: {sorted(info)}")
        print(f"  devIdPrefix: {info.get('devIdPrefix')!r}")

    try:
        homes = await client.homes()
        await note("homeList", client.last_set_cookies)
    except Exception as exc:  # noqa: BLE001
        print(f"  homeList failed: {type(exc).__name__}: {exc}")
        await note("homeList", client.last_set_cookies)
        return
    print(f"  homes returned: {len(homes)}")
    for home in homes[:10]:
        if isinstance(home, dict):
            safe = {
                k: v
                for k, v in home.items()
                if k.lower()
                in {"gid", "id", "groupid", "name", "housename", "groupname"}
            }
            print(f"    {safe}")


async def main_async(args: argparse.Namespace) -> int:
    session_path = Path(args.out)
    qr_path = Path(args.qr) if args.qr else _fresh_qr_path()

    async with EzykamClient() as client:
        flow = QrLoginFlow(client, on_progress=lambda msg: print(f"  * {msg}"))

        print("Requesting a QR code from the Ezykam cloud ...")
        try:
            challenge = await flow.start()
        except QrLoginError as exc:
            print(f"FAILED to start login: {exc}")
            return 1

        if challenge.image_png:
            qr_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                qr_path.write_bytes(challenge.image_png)
            except OSError as exc:
                print(f"\n  (could not save QR image: {exc})")
            else:
                _restrict(qr_path)
                size = png_size(challenge.image_png)
                dims = f"{size[0]}x{size[1]}" if size else "unknown size"
                print(
                    f"\n  QR code saved: {qr_path}  "
                    f"({dims}, {len(challenge.image_png)} bytes)"
                )
                _open(qr_path)
        else:
            print(f"\n  QR image unavailable; token fingerprint {challenge.token_fingerprint}")

        remaining = challenge.ttl_seconds
        print(f"\n  >>> Open the ezyKam+ app and scan the QR code. "
              f"Expires in {remaining:.0f}s. <<<\n")

        try:
            session = await flow.wait_for_approval(timeout=args.timeout)
        except KeyboardInterrupt:
            flow.cancel()
            print("\nCancelled.")
            return 130
        except QrLoginError as exc:
            print(f"\nLOGIN FAILED: {type(exc).__name__}: {exc}")
            return 1

        print("\n  LOGIN SUCCESSFUL")
        print(f"  {session!r}")
        print(f"  final poll set-cookie: {client.last_set_cookies}")

        _save_session(session_path, client)
        print(f"\n  Session stored: {session_path} (owner-only permissions)")
        print("  The file contains live session secrets. Never commit or share it.")

        if args.verify:
            await _verify(client)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_SESSION_PATH), help="session output path")
    parser.add_argument(
        "--qr", default=None, help="QR image output path (default: a fresh file per run)"
    )
    parser.add_argument("--timeout", type=float, default=240.0, help="seconds to wait for a scan")
    parser.add_argument("--verify", action="store_true", help="call authenticated endpoints afterwards")
    parser.add_argument(
        "--verbose", action="store_true", help="enable DEBUG logs (values are redacted)"
    )
    return parser


if __name__ == "__main__":
    _args = build_parser().parse_args()
    if _args.verbose:
        import logging

        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)-7s %(name)s: %(message)s",
        )
        # Keep third-party chatter down; we only want our own redacted logs.
        logging.getLogger("aiohttp").setLevel(logging.INFO)
    raise SystemExit(asyncio.run(main_async(_args)))
