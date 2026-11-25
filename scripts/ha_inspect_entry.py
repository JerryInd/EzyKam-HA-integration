"""Inspect the ezykam config entry on a Home Assistant host (redacted).

Reports structural facts only: sid length, region codes, state. Never prints
the secret values themselves, so it is safe to run over SSH and share output.
"""

from __future__ import annotations

import json
import sys

STORAGE = "/config/.storage/core.config_entries"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else STORAGE
    try:
        # utf-8-sig: tolerate a BOM (PowerShell's utf8 writer adds one).
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except OSError as exc:
        print(f"cannot read {path}: {exc}")
        return 1

    found = False
    for entry in data.get("data", {}).get("entries", []):
        if entry.get("domain") != "ezykam":
            continue
        found = True
        session = (entry.get("data") or {}).get("session") or {}
        sid = session.get("sid", "")
        print(f"entry_id      : {entry.get('entry_id')}")
        print(f"state         : {entry.get('state')}")
        print(f"title         : {entry.get('title')}")
        print(f"unique_id len : {len(entry.get('unique_id') or '')}")
        print(f"sid length    : {len(sid)}  (82 = s-sid cookie, 56 = payload sid)")
        print(f"csrf length   : {len(session.get('csrf_token') or '')}")
        print(f"region        : {session.get('region')}")
        print(f"region_cpplus : {session.get('region_code_cpplus')}")
        print(f"mqtt_cpplus   : {session.get('mqtt_url_cpplus')}")
        print(f"session keys  : {sorted(session)}")
        acquired = session.get("acquired_at")
        print(f"acquired_at   : {acquired}")
        if acquired:
            try:
                from datetime import datetime, timezone

                when = datetime.fromisoformat(str(acquired))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - when
                print(f"session age   : {age}  (at time of this check)")
            except ValueError:
                print("session age   : <unparseable>")

    if not found:
        print("no ezykam config entry found")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
