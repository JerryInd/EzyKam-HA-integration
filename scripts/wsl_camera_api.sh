#!/usr/bin/env bash
# Show Home Assistant's Camera streaming API so we override the right method.
PY="$HOME/ha-venv/bin/python"
"$PY" - <<'PY'
import inspect
from homeassistant.components.camera import Camera
for name in sorted(dir(Camera)):
    if "stream" in name.lower() or "mjpeg" in name.lower() or "image" in name.lower():
        obj = getattr(Camera, name)
        try:
            sig = str(inspect.signature(obj))
        except (TypeError, ValueError):
            sig = ""
        print(f"{name}{sig}")
        doc = (inspect.getdoc(obj) or "").splitlines()
        if doc:
            print("    ", doc[0])

print("\n== CameraMjpegStream.handle ==")
import homeassistant.components.camera as cam
src = inspect.getsource(cam.CameraMjpegStream)
print(src[:2500])
PY
