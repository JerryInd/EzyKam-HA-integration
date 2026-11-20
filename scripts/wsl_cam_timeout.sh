#!/usr/bin/env bash
# Compare HA's camera image timeout with ours, and show ezykam warnings.
PY="$HOME/ha-venv/bin/python"
LOG="$HOME/ha-config/home-assistant.log"

echo "== HA CAMERA_IMAGE_TIMEOUT =="
"$PY" - <<'PY'
import homeassistant.components.camera as c
for name in ("CAMERA_IMAGE_TIMEOUT",):
    print(name, "=", getattr(c, name, "not found"))
PY

echo
echo "== ezykam warnings (last 20) =="
grep -E "No frame decoded|Could not start stream|ezykam" "$LOG" 2>/dev/null | tail -20

echo
echo "== recent log tail =="
tail -15 "$LOG"
