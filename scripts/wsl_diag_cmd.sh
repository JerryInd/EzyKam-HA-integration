#!/usr/bin/env bash
# Print the full body of HA's diagnostics download view.get().
PY="$HOME/ha-venv/bin/python"
"$PY" - <<'PY'
import pathlib, re
p = pathlib.Path("/home/sushil/ha-venv/lib/python3.14/site-packages/homeassistant/components/diagnostics/__init__.py")
text = p.read_text(encoding="utf-8")
idx = text.find("async def get(\n        self,")
if idx < 0:
    idx = text.find('"""Download diagnostics."""')
print(text[idx:idx+4500])
PY
