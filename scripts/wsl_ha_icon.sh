#!/usr/bin/env bash
# How does /api/brands/integration/<domain>/icon.png resolve, and can a custom
# integration supply its own?
B=/home/sushil/ha-venv/lib/python3.14/site-packages/homeassistant/components/brands

echo "== brands const.py =="
sed -n '1,70p' "$B/const.py"

echo
echo "== views / resolution =="
grep -rn "url = \|def get\|custom_components\|integration" "$B"/*.py 2>/dev/null | head -30
