#!/usr/bin/env bash
# Install py-spy and dump the stuck Home Assistant process's Python stacks.
set -uo pipefail

export PATH="$HOME/.local/bin:$PATH"

HPID="$(pgrep -f 'bin/hass' | head -1 || true)"
if [ -z "$HPID" ]; then
  echo "hass process not found"
  exit 1
fi
echo "target pid: $HPID"

if ! command -v py-spy >/dev/null 2>&1; then
  echo "-- installing py-spy"
  if command -v uv >/dev/null 2>&1; then
    uv tool install py-spy >/dev/null 2>&1 || true
  fi
fi

if command -v py-spy >/dev/null 2>&1; then
  py-spy dump --pid "$HPID" || echo "py-spy dump failed (try sudo)"
elif [ -x "$HOME/ha-venv/bin/py-spy" ]; then
  "$HOME/ha-venv/bin/py-spy" dump --pid "$HPID"
else
  echo "py-spy unavailable; falling back to /proc stack"
  cat "/proc/$HPID/stack" 2>/dev/null || true
  echo "-- thread states --"
  grep -E 'State|Threads' "/proc/$HPID/status" 2>/dev/null || true
fi
