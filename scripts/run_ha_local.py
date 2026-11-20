"""Run a real Home Assistant instance on Windows for integration testing.

Home Assistant's CLI refuses non-POSIX platforms: ``validate_os()`` in
``homeassistant/__main__.py`` calls ``sys.exit(1)`` unless the OS is Linux or
macOS. That check guards the *launcher*, not the library — the components and
config-flow machinery are platform independent.

This launcher skips only that one guard so a genuine Home Assistant can be
executed here to prove the integration works end to end. It is a **development
aid for this workstation**; the deployment target (Raspberry Pi / HA OS) is
Linux and never needs it.

Usage:
    python scripts/run_ha_local.py -c .ha-config
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    import homeassistant.__main__ as ha_main

    original = ha_main.validate_os
    ha_main.validate_os = lambda: None  # noqa: E731 - single-purpose shim
    try:
        argv = sys.argv[1:]
        if not argv:
            argv = ["-c", str(REPO_ROOT / ".ha-config")]
        sys.argv = ["hass", *argv]
        return int(ha_main.main())
    finally:
        ha_main.validate_os = original


if __name__ == "__main__":
    raise SystemExit(main())
