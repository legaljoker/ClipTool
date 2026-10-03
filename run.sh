#!/usr/bin/env bash
# Start ClipTool. With no arguments the menu opens; any arguments are passed on,
# e.g.  ./run.sh quick myvideo.mp4
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "ClipTool isn't installed yet - running the installer first."
  bash install.sh || exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m cliptool "$@"
