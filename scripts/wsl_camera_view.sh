#!/usr/bin/env bash
# Locate Home Assistant's camera proxy handler and show how it responds.
PY="$HOME/ha-venv/bin/python"
"$PY" - <<'PY'
import pathlib, re
p = pathlib.Path("/home/sushil/ha-venv/lib/python3.14/site-packages/homeassistant/components/camera/__init__.py")
text = p.read_text()
# find the view class handling camera_proxy
for m in re.finditer(r"class (\w+)\(.*?View.*?\):", text):
    print("view class:", m.group(1))
idx = text.find("camera_proxy")
print("\n--- around camera_proxy ---")
print(text[max(0, idx-1500):idx+2500])
PY
