"""Settings handling.

Defaults live in DEFAULTS below. Users override any of them in ``config.yaml``
(in the ClipTool folder, the current folder, or a path given with --config).
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Optional

import yaml

APP_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "paths": {
        "input_dir": "input",
        "output_dir": "output",
        "avatars_dir": "avatars",
        "log_dir": "logs",
    },
    "transcription": {
        # auto = faster-whisper if installed, else openai-whisper
        "engine": "auto",
        # tiny / base / small / medium / large-v3  (bigger = more accurate, slower)
        "model": "small",
        "language": None,  # None = auto-detect, or e.g. "en"
        "device": "auto",  # auto / cpu / cuda
        "compute_type": "auto",
    },
    "silence": {
        "threshold_db": -35,  # anything quieter than this counts as silence
        "min_silence": 0.6,  # seconds of quiet before it gets cut
        "padding": 0.15,  # seconds of breathing room kept around speech
        "use_auto_editor": False,  # use auto-editor instead of the built-in cutter
    },
    "captions": {
        "font": "Arial",
        "font_size": 0,  # 0 = automatic, based on video size
        "uppercase": True,
        "max_words": 3,  # words shown on screen at once
        "primary_color": "#FFFFFF",
        "highlight_color": "#FFE000",  # color of the word currently being spoken
        "outline_color": "#000000",
        "outline": 0,  # 0 = automatic
        "position": "lower",  # bottom / lower / middle / top
        "pop": True,  # slightly enlarge the active word
        "speaker_colors": False,  # give each speaker their own text color
    },
    "reformat": {
        # smart = follow faces (needs opencv), crop = center crop,
        # blur = fit whole frame on a blurred background
        "mode": "smart",
        "blur_strength": 20,
    },
    "clips": {
        "min_length": 30,
        "max_length": 60,
        "count": 5,
        "use_ai": "auto",  # auto = use Claude if ANTHROPIC_API_KEY is set
    },
    "bestof": {
        "target_length": 58,
        "moment_min": 3,
        "moment_max": 15,
        "max_per_source": 2,
        "canvas": "1080x1920",
    },
    "longform": {
        "canvas": "1920x1080",
        "chapters": True,
        "reformat_mode": "blur",
    },
    "speakers": {
        # auto = pyannote if installed + token given, else built-in voice clustering
        "engine": "auto",
        "num_speakers": None,  # None = guess
        "max_speakers": 4,
        "huggingface_token": "",
        # map detected speaker ids to names / avatar folders, e.g. SPEAKER_1: Dave
        "names": {},
    },
    "emotions": {
        # auto = transformer model if 'transformers' is installed, else word lists + voice energy
        "engine": "auto",
        "transformer_model": "j-hartmann/emotion-english-distilroberta-base",
    },
    "avatars": {
        "layout": "active",  # active = only the person talking, all = everyone on screen
        "position": "top-left",  # top-left / top-right / bottom-left / bottom-right / center
        "size": 0.30,  # fraction of the video width
        "margin": 0.04,
        "talk_flap_seconds": 0.15,  # how fast talk/idle images alternate
        "dim_idle": True,  # in 'all' layout, fade people who are not talking
    },
    "export": {
        "platforms": ["tiktok", "youtube_shorts"],
        "loudnorm": True,  # normalize volume to platform loudness (-14 LUFS)
        "preset": "medium",  # x264 speed: veryfast / fast / medium / slow
    },
    "metadata": {
        "use_ai": "auto",
        "num_titles": 5,
        "num_hashtags": 8,
    },
    "ai": {
        "model": "claude-opus-5-5",
        "effort": "medium",
    },
    "batch": {
        "watch_interval": 10,
        "move_processed": True,
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config(dict):
    """A dict with dotted lookup: cfg.get_path('captions.font')."""

    source: Optional[Path] = None

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set_path(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node: Any = self
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def resolve_dir(self, key: str) -> Path:
        p = Path(os.path.expanduser(self["paths"][key]))
        if not p.is_absolute():
            p = APP_ROOT / p
        return p


def find_config_file(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        return p
    for candidate in (Path.cwd() / "config.yaml", APP_ROOT / "config.yaml"):
        if candidate.exists():
            return candidate
    return None


def load_config(explicit: Optional[str] = None) -> Config:
    path = find_config_file(explicit)
    data: dict = {}
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    cfg = Config(deep_merge(DEFAULTS, data))
    cfg.source = path
    return cfg
