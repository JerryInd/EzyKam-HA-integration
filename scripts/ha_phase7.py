"""Execute Phase 7 for real: drive the config flow against a live Home Assistant.

Talks to HA over its REST API (the same endpoints the frontend uses):

    POST /api/config/config_entries/flow            {"handler": "ezykam"}
    POST /api/config/config_entries/flow/{flow_id}  {}   -> advance

The QR image arrives in ``description_placeholders["qr_image"]`` as a data URL;
it is written to disk and opened so the user can scan it with the ezyKam+ app.

Usage:
    python scripts/ha_phase7.py --ha-url http://<host>:8123 --token-file .session/ha_token
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

QR_DIR = REPO_ROOT / ".session"


def _fresh_qr_path() -> Path:
    """Unique QR path per run.

    The default image viewer keeps the previous PNG open, which makes Windows
    refuse to overwrite it (Errno 22) — the same lock that affected qr_login.py.
    """
    QR_DIR.mkdir(parents=True, exist_ok=True)
    for stale in QR_DIR.glob("ha_qr*.png"):
        try:
            stale.unlink()
        except OSError:
            pass  # still open in a viewer; harmless
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return QR_DIR / f"ha_qr-{stamp}.png"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    # Defaults target the local WSL test instance; override for a real one.
    parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    parser.add_argument(
        "--token-file",
        default=str(REPO_ROOT / ".session" / "ha_local_token.json"),
    )
    parser.add_argument("--scan-wait", type=float, default=300.0)
    parser.add_argument("--handler", default="ezykam")
    return parser.parse_args()


class Ha:
    """Tiny REST client for Home Assistant."""

    def __init__(self, base_url: str, token: str) -> None:
        import aiohttp

        self.base_url = base_url.rstrip("/")
        self._token = token
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {token}"}
        )

    async def close(self) -> None:
        await self._session.close()

    async def refresh(self, token_file: Path) -> bool:
        """Mint a new access token from the stored refresh token.

        HA issues access tokens with a 30-minute lifetime and has no
        ``password`` grant, so the refresh token is the only way to keep a
        long-running script authenticated.
        """
        import aiohttp

        raw = token_file.read_text(encoding="utf-8").strip()
        if not raw.startswith("{"):
            # A bare long-lived access token (Profile -> Long-Lived Access
            # Tokens) never expires, so there is nothing to refresh.
            return False

        data = json.loads(raw)
        refresh = data.get("refresh_token")
        client_id = data.get("clientId")
        if not refresh or not client_id:
            print("no refresh_token/clientId stored; cannot refresh")
            return False

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.base_url + "/auth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": client_id,
                },
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200 or not isinstance(body, dict) or not body.get(
                    "access_token"
                ):
                    print(f"token refresh failed: HTTP {resp.status} {str(body)[:200]}")
                    return False

        data["access_token"] = body["access_token"]
        data["expires_in"] = body.get("expires_in", 1800)
        data["expires"] = int(time.time() * 1000) + data["expires_in"] * 1000
        token_file.write_text(json.dumps(data), encoding="utf-8")

        self._token = body["access_token"]
        self._session.headers["Authorization"] = f"Bearer {self._token}"
        print("access token refreshed")
        return True

    @staticmethod
    def _parse(text: str, path: str, status: int) -> Any:
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            raise RuntimeError(
                f"{path} returned non-JSON (HTTP {status}): {text[:400]!r}"
            ) from err

    async def get(self, path: str) -> Any:
        async with self._session.get(self.base_url + path) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"GET {path} -> {resp.status}: {text[:300]}")
            return self._parse(text, path, resp.status)

    async def post(self, path: str, payload: dict | None = None) -> Any:
        async with self._session.post(
            self.base_url + path, json=payload if payload is not None else {}
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"POST {path} -> {resp.status}: {text[:400]}")
            return self._parse(text, path, resp.status)

    async def ws(self, command: str, args: dict | None = None) -> Any:
        """Fallback for commands only exposed over WebSocket."""
        from scripts.ha_ws import call

        return await call(command, args)


def _load_token(path: Path) -> str:
    """Accept either a bare token or the HA frontend's hassTokens JSON."""
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("{"):
        import json as _json

        data = _json.loads(raw)
        token = data.get("access_token") or ""
        if not token:
            raise SystemExit(f"no access_token in {path}")
        return token
    if not raw:
        raise SystemExit(f"missing token file: {path}")
    return raw


async def _wait_ready(base: str, timeout: float = 300.0) -> bool:
    """Block until the HA HTTP server answers (it may be restarting)."""
    import time as _time

    import aiohttp

    deadline = _time.monotonic() + timeout
    async with aiohttp.ClientSession() as session:
        while _time.monotonic() < deadline:
            try:
                async with session.get(base + "/api/") as resp:
                    if resp.status:
                        return True
            except Exception:  # noqa: BLE001 - still starting
                pass
            await asyncio.sleep(2.0)
    return False


async def run(args: argparse.Namespace) -> int:
    print(f"waiting for {args.ha_url} ...")
    if not await _wait_ready(args.ha_url):
        print("Home Assistant did not become ready")
        return 1

    token = _load_token(Path(args.token_file))
    ha = Ha(args.ha_url, token)
    # Refresh up front: the stored access token may already be past its
    # 30-minute lifetime.
    await ha.refresh(Path(args.token_file))
    try:
        info = await ha.get("/api/config")
        print(f"Home Assistant {info['version']}  state={info['state']}")
        # A custom integration is NOT in `components` until its config flow
        # runs, so absence here is expected — the flow start below is the real
        # test (it fails with unknown_handler if the module cannot be imported).
        print(f"ezykam component loaded: {'ezykam' in info['components']} (may be pre-setup)")

        # Existing entry? (re-runs should be idempotent)
        entries = [
            e
            for e in await ha.get("/api/config/config_entries/entry")
            if e.get("domain") == args.handler
        ]
        if entries:
            print(f"\nIntegration already configured ({len(entries)} entry).")
            return await report(ha)

        print("\n== starting config flow ==")
        try:
            flow = await ha.post(
                "/api/config/config_entries/flow", {"handler": args.handler}
            )
        except RuntimeError as err:
            print(f"\nFAILED to start flow: {err}")
            print("This usually means custom_components/ezykam could not be imported.")
            return 1
        print(f"  step={flow.get('step_id')} type={flow.get('type')}")
        flow_id = flow.get("flow_id")

        if flow.get("type") == "form" and flow.get("step_id") == "user":
            flow = await ha.post(f"/api/config/config_entries/flow/{flow_id}", {})
            print(f"  step={flow.get('step_id')} type={flow.get('type')}")

        placeholders = flow.get("description_placeholders") or {}
        qr_data_url = placeholders.get("qr_image") or ""
        qr_url = placeholders.get("qr_url") or ""

        # Verify the served QR endpoint really returns an image before asking
        # a human to scan - a broken <img> is otherwise only discoverable by
        # looking at the UI. The flow supplies a relative URL, so resolve it
        # against the instance origin.
        if qr_url:
            absolute = qr_url if qr_url.startswith("http") else ha.base_url + qr_url
            try:
                async with ha._session.get(absolute) as resp:  # noqa: SLF001
                    body = await resp.read()
                    ok = resp.status == 200 and body.startswith(b"\x89PNG")
                    print(
                        f"  QR endpoint {qr_url} -> HTTP {resp.status}, "
                        f"{len(body)} bytes, png={ok}"
                    )
                    if not ok:
                        print("    FAILED: endpoint did not return a PNG")
            except Exception as exc:  # noqa: BLE001
                print(f"  QR endpoint {qr_url} -> FAILED {type(exc).__name__}: {exc}")

        if qr_data_url and "," in qr_data_url:
            raw = base64.b64decode(qr_data_url.split(",", 1)[1])
            qr_out = _fresh_qr_path()
            try:
                qr_out.write_bytes(raw)
            except OSError as exc:
                print(f"\n  (could not save QR: {exc})")
            else:
                print(f"\n  QR saved: {qr_out} ({len(raw)} bytes)")
                try:
                    import os

                    os.startfile(qr_out)  # type: ignore[attr-defined]
                except (AttributeError, OSError):
                    print(f"  open it manually: {qr_out}")
                print("\n  >>> Scan with the ezyKam+ app. Waiting ... <<<\n")
        else:
            print(f"  (no QR in placeholders: {sorted(placeholders)})")

        deadline = time.monotonic() + args.scan_wait
        while time.monotonic() < deadline:
            await asyncio.sleep(2.0)
            flow = await ha.post(f"/api/config/config_entries/flow/{flow_id}", {})
            kind = flow.get("type")
            step = flow.get("step_id")
            print(f"  ... type={kind} step={step}")

            if kind == "create_entry":
                print(f"\nFLOW COMPLETE: entry_id={flow.get('entry_id')} title={flow.get('title')!r}")
                return await report(ha)
            if kind == "abort":
                print(f"\nFLOW ABORTED: reason={flow.get('reason')!r}")
                print(f"  detail={json.dumps(flow.get('description_placeholders'))}")
                return 1
            if kind == "form" and step not in ("scan",):
                errors = flow.get("errors") or {}
                print(f"\nFLOW ERROR: step={step} errors={errors}")
                return 1

        print("\nTIMED OUT waiting for the QR scan")
        return 1
    finally:
        await ha.close()


async def report(ha: Ha) -> int:
    """Show what the integration actually created."""
    print("\n== entities ==")
    states = await ha.get("/api/states")
    ours = [s for s in states if s["entity_id"].split(".")[0] in ("camera", "binary_sensor")
            and "ezykam" in json.dumps(s.get("attributes", {})).lower()]
    # fall back to device-based lookup
    if not ours:
        ours = [s for s in states if s["entity_id"].startswith(("camera.", "binary_sensor."))]
    for state in ours:
        attrs = state.get("attributes", {})
        print(f"  {state['entity_id']:45} state={state['state']:12} name={attrs.get('friendly_name')!r}")

    entries = [
        e
        for e in await ha.get("/api/config/config_entries/entry")
        if e.get("domain") == "ezykam"
    ]
    print(f"\nconfig entries (ezykam): {len(entries)}")
    for entry in entries:
        print(f"  {entry['entry_id']}  title={entry.get('title')!r} state={entry.get('state')}")

    # Pull a real frame through HA's camera proxy: this exercises
    # async_camera_image -> coordinator -> WebRTC -> JPEG end to end.
    cameras = [s for s in states if s["entity_id"].startswith("camera.") and s["state"] != "unavailable"]
    if not cameras:
        print("\nno available camera entities to snapshot")
        return 0

    out_dir = REPO_ROOT / ".session" / "ha_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    print("\n== camera proxy images (real WebRTC frames through HA) ==")
    for cam in cameras:
        entity_id = cam["entity_id"]
        path = f"/api/camera_proxy/{entity_id}"
        # First call starts the WebRTC session and returns before a frame has
        # been decoded; poll until HA has a JPEG to serve.
        saved = False
        for attempt in range(1, 13):
            try:
                async with ha._session.get(ha.base_url + path) as resp:  # noqa: SLF001
                    body = await resp.read()
                    if resp.status == 200 and body:
                        out = out_dir / f"{entity_id.replace('.', '_')}-{stamp}.jpg"
                        out.write_bytes(body)
                        print(
                            f"  {entity_id}: {len(body)} bytes on attempt {attempt} -> {out}"
                        )
                        saved = True
                        break
                    print(
                        f"  {entity_id}: attempt {attempt} -> HTTP {resp.status} "
                        f"({len(body)} bytes)"
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"  {entity_id}: attempt {attempt} FAILED {type(exc).__name__}: {exc}")
            await asyncio.sleep(3.0)
        if not saved:
            print(f"  {entity_id}: no image after 12 attempts")
    return 0


def main() -> int:
    args = parse_args()
    import asyncio

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
