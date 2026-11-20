#!/usr/bin/env bash
# Start Home Assistant Core in the foreground, logging to the config directory.
set -uo pipefail

CONFIG="$HOME/ha-config"
HASS="$HOME/ha-venv/bin/hass"

test -x "$HASS" || { echo "missing $HASS"; exit 1; }

echo "starting Home Assistant: $HASS -c $CONFIG"
exec "$HASS" -c "$CONFIG" >> "$CONFIG/ha-console.log" 2>&1
