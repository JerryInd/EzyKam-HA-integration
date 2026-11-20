#!/usr/bin/env bash
# Verify custom_components.ezykam.diagnostics imports and exposes the handler.
CFG="$HOME/ha-config"
"$HOME/ha-venv/bin/python" - "$CFG" <<'PY'
import sys, traceback, pathlib, importlib
cfg = sys.argv[1]
sys.path.insert(0, cfg)

try:
    mod = importlib.import_module("custom_components.ezykam.diagnostics")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print("import OK:", mod.__file__)
handler = getattr(mod, "async_get_config_entry_diagnostics", None)
print("has async_get_config_entry_diagnostics:", callable(handler))

# also check the whole package imports
for name in ("custom_components.ezykam", "custom_components.ezykam.camera",
             "custom_components.ezykam.binary_sensor", "custom_components.ezykam.coordinator",
             "custom_components.ezykam.config_flow"):
    try:
        importlib.import_module(name)
        print("OK  ", name)
    except Exception as exc:
        print("FAIL", name, "->", type(exc).__name__, exc)
PY
