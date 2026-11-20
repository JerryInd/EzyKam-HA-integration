#!/usr/bin/env bash
# Report on the WSL Home Assistant instance: process, log tail, port, config.
LOG="$HOME/ha-config/ha-console.log"

echo "== process =="
pgrep -af hass || echo "NOT RUNNING"

echo
echo "== console log =="
if [ -f "$LOG" ]; then
  wc -l < "$LOG"
  echo "-- tail --"
  tail -40 "$LOG"
else
  echo "no log at $LOG"
fi

echo
echo "== home-assistant.log =="
if [ -f "$HOME/ha-config/home-assistant.log" ]; then
  cat "$HOME/ha-config/home-assistant.log"
else
  echo "missing"
fi

echo
echo "== storage =="
ls -la "$HOME/ha-config/.storage" 2>/dev/null || echo "no .storage"

echo
echo "== process cpu =="
ps -o pid,etime,pcpu,pmem,stat,cmd -p "$(pgrep -f 'bin/hass' | head -1)" 2>/dev/null || echo "no process"

echo
echo "== outbound connections of hass =="
HPID="$(pgrep -f 'bin/hass' | head -1)"
if [ -n "$HPID" ]; then
  ss -tnp 2>/dev/null | grep "pid=$HPID" || echo "none"
else
  echo "no process"
fi

echo
echo "== recent files in config =="
find "$HOME/ha-config" -newermt '-6 minutes' -type f 2>/dev/null | head -20

echo
echo "== port 8123 =="
ss -ltn | grep 8123 || echo "NOT listening"

echo
echo "== config dir =="
ls -la "$HOME/ha-config"
