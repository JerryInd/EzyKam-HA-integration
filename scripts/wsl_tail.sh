#!/usr/bin/env bash
# Show the last N lines of the Home Assistant log.
N="${1:-70}"
LOG="$HOME/ha-config/home-assistant.log"
echo "== last $N lines of home-assistant.log =="
tail -n "$N" "$LOG"
