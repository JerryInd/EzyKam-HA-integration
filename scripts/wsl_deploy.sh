#!/usr/bin/env bash
# Copy the integration into the WSL Home Assistant config directory.
set -euo pipefail

SRC="/mnt/f/Projects/Ezykam+ integration/custom_components/ezykam"
DST="$HOME/ha-config/custom_components/ezykam"

echo "HOME=$HOME"
test -d "$SRC" || { echo "source missing: $SRC"; exit 1; }

rm -rf "$DST"
mkdir -p "$DST"
# Copy the *contents*: `cp -r $SRC $DST` with $DST existing would nest as
# ezykam/ezykam/ and leave manifest.json one level too deep.
cp -r "$SRC"/. "$DST"/

# Drop bytecode caches copied from the Windows tree.
find "$DST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

printf 'copied %s files into %s\n' "$(find "$DST" -type f | wc -l)" "$DST"

cat > "$HOME/ha-config/configuration.yaml" <<'YAML'
homeassistant:
  name: Ezykam Test
  unit_system: metric
  time_zone: UTC
  latitude: 51.5
  longitude: -0.1
  currency: GBP
YAML

echo "-- config dir contents --"
ls -la "$HOME/ha-config"
echo "DEPLOY OK"
