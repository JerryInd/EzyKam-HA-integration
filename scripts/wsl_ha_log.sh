#!/usr/bin/env bash
# Show Home Assistant startup errors and the api/http component status.
LOG="$HOME/ha-config/home-assistant.log"

echo "== ERROR/WARNING lines =="
grep -E "ERROR|WARNING" "$LOG" 2>/dev/null | tail -40

echo
echo "== api / http / onboarding mentions =="
grep -Ei "setup failed for '(api|http|onboarding|frontend)'|'api'|onboarding" "$LOG" 2>/dev/null | tail -25

echo
echo "== last 25 log lines =="
tail -25 "$LOG" 2>/dev/null
