#!/usr/bin/env bash
# Open the ClipTool video editor in your web browser.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "ClipTool isn't installed yet - running the installer first."
  bash install.sh || exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m cliptool app "$@"
