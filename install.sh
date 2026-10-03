#!/usr/bin/env bash
# ClipTool installer for macOS and Linux.  Run:  bash install.sh
set -e
cd "$(dirname "$0")"

echo "=== ClipTool installer ==="

PY=""
for c in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 or newer is needed."
  echo "  macOS:  brew install python      Linux:  sudo apt install python3 python3-venv"
  exit 1
fi
echo "Using $($PY --version)"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo
  echo "ffmpeg is not installed - ClipTool needs it."
  if command -v brew >/dev/null 2>&1; then
    read -r -p "Install it now with Homebrew? (y/n) " a
    [ "$a" = "y" ] && brew install ffmpeg
  elif command -v apt-get >/dev/null 2>&1; then
    read -r -p "Install it now with apt (needs your password)? (y/n) " a
    [ "$a" = "y" ] && sudo apt-get update && sudo apt-get install -y ffmpeg
  else
    echo "Please install ffmpeg from https://ffmpeg.org/download.html and run this again."
  fi
fi

if [ ! -d .venv ]; then
  echo "Creating virtual environment in .venv ..."
  "$PY" -m venv .venv || { echo "Could not create a venv. On Linux: sudo apt install python3-venv"; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
echo "Installing ClipTool (this can take a few minutes)..."
python -m pip install -e .

echo
read -r -p "Install Claude AI support for smarter clip picks & titles? (y/n) " a
[ "$a" = "y" ] && python -m pip install anthropic
read -r -p "Install high-accuracy speaker identification + AI expressions (large download, ~2GB)? (y/n) " a
[ "$a" = "y" ] && python -m pip install pyannote.audio transformers torch

[ -f config.yaml ] || cp config.example.yaml config.yaml
mkdir -p input output avatars logs
chmod +x run.sh

echo
python -m cliptool doctor || true
echo
echo "Done! Start ClipTool with:   ./run.sh"
