#!/usr/bin/env bash
# Is the Home Assistant requirement install progressing or hung?
LOG="$HOME/ha-config/home-assistant.log"

echo "== uv process =="
ps -o pid,etime,pcpu,stat,wchan:24,cmd -C uv 2>/dev/null || echo "no uv process"

echo
echo "== uv network =="
ss -tnp 2>/dev/null | grep -i uv || echo "no tcp connections"

echo
echo "== hass process =="
ps -o pid,etime,pcpu,stat -C python 2>/dev/null | head -5

echo
echo "== log tail =="
tail -6 "$LOG" 2>/dev/null || echo "no log"

echo
echo "== storage files =="
ls -la "$HOME/ha-config/.storage" 2>/dev/null

echo
echo "== port 8123 =="
ss -ltn | grep 8123 || echo "NOT listening"
