#!/usr/bin/env bash
# Extract the stored secrets of the ezykam config entry so the diagnostics
# dump can be checked against them. Written to the Windows mount (git-ignored).
OUT="/mnt/f/Projects/Ezykam+ integration/.session/entry_secrets.json"
STORAGE="$HOME/ha-config/.storage/core.config_entries"

"$HOME/ha-venv/bin/python" - "$STORAGE" "$OUT" <<'PY'
import json, sys, pathlib
storage, out = sys.argv[1], sys.argv[2]
data = json.load(open(storage, encoding="utf-8"))
entries = data.get("data", {}).get("entries", [])
target = None
for e in entries:
    if e.get("domain") == "ezykam":
        target = e
        break
if target is None:
    print("no ezykam entry found")
    sys.exit(1)
session = (target.get("data") or {}).get("session") or {}
secrets = {
    k: v for k, v in session.items()
    if k in {"sid", "csrf_token", "uid", "client_id"} and v
}
pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
pathlib.Path(out).write_text(json.dumps(secrets, indent=2), encoding="utf-8")
print(f"wrote {len(secrets)} secret value(s) for redaction checking")
print("keys:", sorted(secrets))
PY
