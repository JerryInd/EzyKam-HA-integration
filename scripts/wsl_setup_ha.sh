#!/usr/bin/env bash
# Provision a real Home Assistant Core environment inside WSL.
#
# Home Assistant requires Python >=3.14.2 (2026.x) and refuses to run natively
# on Windows (runner.py imports fcntl), so the instance runs here in Ubuntu
# under WSL2 and is reached from Windows at http://localhost:8123.
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

echo "== provisioning Home Assistant in WSL =="

if ! command -v uv >/dev/null 2>&1; then
  echo "-- installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version

echo "-- installing Python 3.14"
uv python install 3.14

VENV="$HOME/ha-venv"
CONFIG="$HOME/ha-config"

if [ ! -x "$VENV/bin/python" ]; then
  echo "-- creating venv"
  uv venv --python 3.14 "$VENV"
fi

echo "-- installing Home Assistant"
uv pip install --python "$VENV/bin/python" homeassistant

"$VENV/bin/python" - <<'PY'
from homeassistant.const import __version__
print("Home Assistant", __version__)
PY

mkdir -p "$CONFIG"
echo "-- config dir: $CONFIG"
echo "PROVISION OK"
