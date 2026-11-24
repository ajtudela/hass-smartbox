#!/usr/bin/env bash
# Install the sibling smartbox checkout editable into this repo's venv.
#
# The migration branch imports modules that are not in the published PyPI
# release. This keeps local testing reproducible without publishing a wheel.
# Override the sibling location with SMARTBOX_LIB=/path/to/smartbox.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
library="${SMARTBOX_LIB:-$repo_root/../smartbox}"
python="$repo_root/.venv/bin/python"

if [[ ! -d "$library/src/smartbox" ]]; then
  echo "smartbox checkout not found at $library" >&2
  echo "Set SMARTBOX_LIB=/path/to/smartbox and retry." >&2
  exit 1
fi

uv pip install --python "$python" -e "$library"
"$python" -c 'import smartbox.device.profile, smartbox.device.control_modes, smartbox.device.modes, smartbox.device.storage, smartbox.device.settings; print("smartbox.device OK")'
