#!/usr/bin/env bash
# Compare this integration's manifest requirements against Home Assistant's
# own package constraints, so a pin conflict is found before HA hits it.
C="$HOME/ha-venv/lib/python3.14/site-packages/homeassistant/package_constraints.txt"
MANIFEST="/mnt/f/Projects/Ezykam+ integration/custom_components/ezykam/manifest.json"

echo "== HA constraints for our requirements =="
for pkg in av aiortc aiomqtt Pillow aiohttp; do
  line="$(grep -i "^${pkg}==" "$C" 2>/dev/null | head -1)"
  if [ -n "$line" ]; then
    echo "  PINNED: $line"
  else
    echo "  not constrained: $pkg"
  fi
done

echo
echo "== our manifest requirements =="
"$HOME/ha-venv/bin/python" - "$MANIFEST" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
for r in m.get("requirements", []):
    print("  " + r)
PY
