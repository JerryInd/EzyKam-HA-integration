"""Set the GitHub repository description/topics using the stored git credential.

The credential is read via `git credential fill` and used in-process only; it
is never printed or written to disk.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "JerryInd/EzyKam-HA-integration"
API = f"https://api.github.com/repos/{REPO}"

DESCRIPTION = (
    "Unofficial cloud-only Home Assistant integration for CP Plus Ezykam+ "
    "cameras - QR sign-in, automatic discovery and WebRTC streaming. "
    "No RTSP, ONVIF, local camera IPs or port forwarding required."
)
TOPICS = [
    "home-assistant",
    "homeassistant",
    "custom-integration",
    "hacs",
    "ezykam",
    "cpplus",
    "camera",
    "cctv",
    "webrtc",
    "iot",
]


def get_token() -> str:
    """Read the stored GitHub credential without echoing it."""
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            token = line[len("password="):].strip()
            if token:
                return token
    return ""


def patch(payload: dict) -> int:
    token = get_token()
    if not token:
        print("no stored GitHub credential found")
        return 1
    req = urllib.request.Request(
        API,
        data=json.dumps(payload).encode("utf-8"),
        method="PATCH",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ezykam-ha-setup",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        print(f"API error {exc.code}: {detail}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"request failed: {type(exc).__name__}: {exc}")
        return 1

    print("description set:")
    print(f"  {body.get('description')}")
    print(f"topics: {body.get('topics')}")
    return 0


def main() -> int:
    return patch({"description": DESCRIPTION, "topics": TOPICS})


if __name__ == "__main__":
    sys.exit(main())
