"""'Check setup': shows what is installed and how to fix anything missing."""
from __future__ import annotations

import os
import shutil
import sys

from cliptool import ai, reformat, speakers, transcribe
from cliptool.emotions import transformer_available
from cliptool.ffmpeg_utils import has_filter


def checks(cfg) -> list[tuple[str, bool, bool, str]]:
    """(name, ok, required, how-to-fix)"""
    out = []
    out.append(("Python 3.9+", sys.version_info >= (3, 9), True, "Install Python 3.10+ from python.org"))
    ff = bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))
    out.append(("ffmpeg + ffprobe", ff, True,
                "Windows: winget install Gyan.FFmpeg | macOS: brew install ffmpeg | Linux: sudo apt install ffmpeg"))
    if ff:
        out.append(("ffmpeg caption support (libass)", has_filter("ass"), True,
                    "Install a full ffmpeg build (the ones above include it)"))
    engines = transcribe.available_engines()
    out.append(("Whisper transcription (" + (", ".join(engines) or "none") + ")", bool(engines), True,
                "pip install faster-whisper"))
    out.append(("Smart face-tracking reframe (opencv)", reformat.opencv_available(), False,
                "pip install opencv-python-headless   (otherwise center crop is used)"))
    out.append(("auto-editor (alternative silence cutter)", bool(shutil.which("auto-editor")), False,
                "pip install auto-editor   (the built-in cutter works without it)"))
    token = cfg.get_path("speakers.huggingface_token") or os.environ.get("HUGGINGFACE_TOKEN") \
        or os.environ.get("HF_TOKEN")
    out.append(("High-accuracy speaker ID (pyannote + token)", speakers.pyannote_available() and bool(token), False,
                "pip install pyannote.audio, then set speakers.huggingface_token (see README)"))
    out.append(("AI expression model (transformers)", transformer_available(), False,
                "pip install transformers torch   (word lists + voice energy are used otherwise)"))
    out.append(("Claude AI for clips & titles (anthropic + API key)", ai.ai_available(), False,
                "pip install anthropic, then set ANTHROPIC_API_KEY (see README)"))
    return out


def run_doctor(cfg) -> bool:
    print("\nClipTool setup check")
    print("-" * 60)
    all_required = True
    for name, ok, required, fix in checks(cfg):
        mark = "OK " if ok else ("MISSING" if required else "optional - not installed")
        print(f"  [{'x' if ok else ' '}] {name}: {mark}")
        if not ok:
            print(f"        -> {fix}")
            all_required &= not required
    print("-" * 60)
    print(f"  Config file: {cfg.source or 'none (using defaults) - copy config.example.yaml to config.yaml'}")
    print(f"  Output folder: {cfg.resolve_dir('output_dir')}")
    print(f"  Avatars folder: {cfg.resolve_dir('avatars_dir')}")
    print("  Everything required is installed!" if all_required else
          "  Some REQUIRED parts are missing - see the fixes above.")
    return all_required
