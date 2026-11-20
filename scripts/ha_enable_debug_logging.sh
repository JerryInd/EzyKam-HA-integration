#!/usr/bin/env bash
# Enable debug logging for the ezykam integration, server-side.
#
# Appends (never rewrites) so the original bytes are untouched, and takes a
# backup first: a broken configuration.yaml stops Home Assistant from starting.
set -euo pipefail

CFG=/config/configuration.yaml
BAK=/config/configuration.yaml.bak-ezykam

if grep -q '^logger:' "$CFG"; then
  echo "logger block already present - nothing to do"
else
  cp "$CFG" "$BAK"
  printf '\n# Temporary diagnostic logging for the Ezykam integration.\nlogger:\n  default: info\n  logs:\n    custom_components.ezykam: debug\n' >> "$CFG"
  echo "appended logger block"
  echo "backup: $BAK"
fi

echo "--- tail of $CFG ---"
tail -9 "$CFG"
echo "--- validate YAML parses (python not available here; size check) ---"
wc -c "$CFG"
