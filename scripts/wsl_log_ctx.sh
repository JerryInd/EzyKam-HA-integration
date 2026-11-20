#!/usr/bin/env bash
# Show Home Assistant log context around a pattern.
# Usage: wsl_log_ctx.sh PATTERN [LINES_BEFORE] [LINES_AFTER]
LOG="$HOME/ha-config/home-assistant.log"
PATTERN="${1:-probatio}"
BEFORE="${2:-15}"
AFTER="${3:-25}"

echo "== matches for '$PATTERN' =="
grep -n "$PATTERN" "$LOG" 2>/dev/null | tail -20 || echo "(none)"

echo
echo "== context (last match) =="
LAST="$(grep -n "$PATTERN" "$LOG" 2>/dev/null | tail -1 | cut -d: -f1)"
if [ -n "$LAST" ]; then
  START=$(( LAST > BEFORE ? LAST - BEFORE : 1 ))
  END=$(( LAST + AFTER ))
  sed -n "${START},${END}p" "$LOG"
else
  echo "(no match)"
fi
