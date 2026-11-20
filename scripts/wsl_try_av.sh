#!/usr/bin/env bash
# Why can't Home Assistant install av (PyAV) into the WSL venv?
VENV="$HOME/ha-venv"
export PATH="$HOME/.local/bin:$PATH"

echo "== python version =="
"$VENV/bin/python" --version

echo
echo "== is av already present? =="
"$VENV/bin/python" -c "import av; print('av', av.__version__)" 2>&1 | tail -2

echo
echo "== try installing av<19 =="
uv pip install --python "$VENV/bin/python" --dry-run "av>=12,<19" 2>&1 | tail -25

echo
echo "== available av versions =="
uv pip install --python "$VENV/bin/python" --dry-run "av" 2>&1 | tail -12
