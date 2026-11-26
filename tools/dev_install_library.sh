#!/usr/bin/env bash
# Install the sibling smartbox checkout editable into this repo's venv.
#
# The integration pins the published smartbox RC; this overlays a local
# library checkout when developing against unreleased library changes.
# Override the sibling location with SMARTBOX_LIB=/path/to/smartbox.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
library="${SMARTBOX_LIB:-$repo_root/../smartbox}"
python="$repo_root/.venv/bin/python"

if [[ ! -x "$python" ]]; then
  echo "No virtualenv at $repo_root/.venv; run 'uv sync --all-extras --dev' first." >&2
  exit 1
fi

if [[ ! -d "$library/src/smartbox" ]]; then
  echo "smartbox checkout not found at $library" >&2
  echo "Set SMARTBOX_LIB=/path/to/smartbox and retry." >&2
  exit 1
fi

uv pip install --python "$python" -e "$library"
"$python" -c 'import smartbox.device.capabilities, smartbox.device.codes, smartbox.device.profile, smartbox.device.control_modes, smartbox.device.modes, smartbox.device.storage, smartbox.device.settings; print("smartbox.device OK")'
