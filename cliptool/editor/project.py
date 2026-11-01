"""Editor projects: a JSON document describing one edit.

All times on the timeline are in seconds of the FINISHED video. Positions (x, y)
are the centre of an item as a fraction of the frame (0..1), sizes are fractions
of the frame width, so a project looks the same at any resolution.

The timeline maths here (clip_starts / transition_duration) is mirrored exactly
in static/js/model.js so the browser preview and the final render agree.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cliptool.editor import textfx
from cliptool.emotions import EXPRESSION_LABELS, EXPRESSIONS, FALLBACKS
from cliptool.reformat import FRAME_STYLES

EXPRESSION_EMOJI = {"neutral": "😐", "happy": "😊", "sad": "😢", "mad": "😠", "angry": "😡", "rofl": "🤣",
                    "lol": "😂", "disappointed": "😞", "face_slap": "🤦", "excited": "🤩", "surprised": "😮",
                    "scared": "😱"}
CROP_MODES = {"crop": "Fill the screen (crops the sides)"}
CHROMA_COLORS = {"green": "#00FF00", "blue": "#0000FF"}

SCHEMA_VERSION = 1

CANVAS_PRESETS: dict[str, dict] = {
    "tiktok": {"label": "TikTok 9:16", "width": 1080, "height": 1920, "platform": "tiktok"},
    "youtube_shorts": {"label": "YouTube Shorts 9:16", "width": 1080, "height": 1920, "platform": "youtube_shorts"},
    "instagram_reels": {"label": "Instagram Reels 9:16", "width": 1080, "height": 1920,
                        "platform": "instagram_reels"},
    "youtube": {"label": "YouTube 16:9", "width": 1920, "height": 1080, "platform": "youtube"},
    "square": {"label": "Square 1:1", "width": 1080, "height": 1080, "platform": "instagram_reels"},
}

# Effects: label, default params, and a short help text. Each has a start/end bar on the timeline.
EFFECTS: dict[str, dict] = {
    "zoom_in": {"label": "Zoom in", "help": "Punch in on a point, hold, then back out",
                "params": {"amount": 1.35, "x": 0.5, "y": 0.45, "ramp": 0.25}},
    "zoom_out": {"label": "Zoom out", "help": "Start zoomed in and slowly pull back",
                 "params": {"amount": 1.35, "x": 0.5, "y": 0.45}},
    "shake": {"label": "Camera shake", "help": "Shaky camera for impact moments", "params": {"intensity": 0.5}},
    "flash": {"label": "Flash", "help": "White flash that fades out", "params": {"strength": 0.8}},
    "black_white": {"label": "Black & white", "help": "Removes the color", "params": {}},
    "sepia": {"label": "Sepia / old film", "help": "Warm vintage look", "params": {}},
    "blur": {"label": "Blur", "help": "Blurs the whole picture", "params": {"amount": 8}},
    "vignette": {"label": "Vignette", "help": "Darkens the corners", "params": {}},
    "invert": {"label": "Invert colors", "help": "Negative image", "params": {}},
    "glitch": {"label": "Glitch / RGB split", "help": "Color channels split apart", "params": {"amount": 12}},
    "bleep": {"label": "Bleep (censor)", "help": "Mutes the sound and plays a bleep tone", "params": {"tone": 1000}},
    "mute": {"label": "Mute", "help": "Silences the video's sound", "params": {}},
}
AUDIO_EFFECTS = {"bleep", "mute"}

# ffmpeg xfade transition names -> labels.
TRANSITIONS: dict[str, str] = {
    "fade": "Crossfade",
    "fadeblack": "Fade through black",
    "fadewhite": "Fade through white",
    "dissolve": "Dissolve",
    "wipeleft": "Wipe left",
    "wiperight": "Wipe right",
    "wipeup": "Wipe up",
    "wipedown": "Wipe down",
    "slideleft": "Slide left",
    "slideright": "Slide right",
    "slideup": "Slide up",
    "slidedown": "Slide down",
    "circleopen": "Circle open",
    "circleclose": "Circle close",
    "radial": "Radial",
    "pixelize": "Pixelize",
    "zoomin": "Zoom in",
}

PERSON_COLORS = ["#FFFFFF", "#7CF7FF", "#FFB3F0", "#B6FF8A", "#FFC27A", "#C9B8FF"]

DEFAULT_CAPTION_STYLE = {
    "x": 0.5, "y": 0.72,  # centre of the caption block
    "font": "Arial", "size": 78,  # px for a 1080px-wide frame
    "uppercase": True, "max_words": 3,
    "color": "#FFFFFF", "highlight": "#FFE000", "outline": "#000000", "outline_width": 7,
    "use_person_color": True,  # captions take the speaker's color
    "pop": True,
    "show_avatar": True, "avatar_size": 0.22, "avatar_place": "above",  # above / left / right
    "talk_flap": 0.15,
}

DEFAULT_FRAME = {"style": "blur", "scale": 1.0, "offset_y": 0.0, "blur": 20,
                 "color": "#101018", "color2": "#2575fc"}


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def meta() -> dict:
    """Everything the editor UI needs to build its menus."""
    return {
        "presets": CANVAS_PRESETS,
        "frame_styles": FRAME_STYLES,
        "crop_modes": CROP_MODES,
        "effects": EFFECTS,
        "transitions": TRANSITIONS,
        "expressions": EXPRESSIONS,
        "expression_labels": EXPRESSION_LABELS,
        "expression_fallbacks": FALLBACKS,
        "expression_emoji": EXPRESSION_EMOJI,
        "default_expressions": default_expressions(),
        **textfx.meta(),
        "person_colors": PERSON_COLORS,
        "caption_style": DEFAULT_CAPTION_STYLE,
        "frame": DEFAULT_FRAME,
    }


def default_expressions() -> list[dict]:
    return [{"id": e, "label": EXPRESSION_LABELS[e], "emoji": EXPRESSION_EMOJI.get(e, "")} for e in EXPRESSIONS]


def new_project(name: str) -> dict:
    return normalize({"name": name, "created": datetime.now().isoformat(timespec="seconds")})


def normalize(p: dict) -> dict:
    """Fill in anything missing so older/hand-edited projects always load."""
    p = copy.deepcopy(p)
    p.setdefault("version", SCHEMA_VERSION)
    p.setdefault("name", "Untitled")
    if p.get("preset") not in CANVAS_PRESETS:
        p["preset"] = "tiktok"
    p["frame"] = {**DEFAULT_FRAME, **(p.get("frame") or {})}
    p["caption_style"] = {**DEFAULT_CAPTION_STYLE, **(p.get("caption_style") or {})}
    for key in ("media", "clips", "people", "captions", "effects", "overlays", "audio"):
        p.setdefault(key, [])
    if not p.get("expressions"):
        p["expressions"] = default_expressions()
    if not any(e.get("id") == "neutral" for e in p["expressions"]):
        p["expressions"].insert(0, default_expressions()[0])
    expr_ids = {e["id"] for e in p["expressions"]}
    for m in p["media"]:
        m.setdefault("kind", "video")
    p.setdefault("export", {"loudnorm": True})
    media_ids = {m["id"] for m in p["media"]}
    video_ids = {m["id"] for m in p["media"] if m.get("kind", "video") == "video"}
    p["clips"] = [c for c in p["clips"] if c.get("media") in video_ids and c.get("out", 0) > c.get("in", 0)]
    p["audio"] = [a for a in p["audio"] if a.get("media") in media_ids and a.get("out", 0) > a.get("in", 0)]
    for a in p["audio"]:
        a.setdefault("id", new_id("a"))
        a.setdefault("volume", 1.0)
        a["end"] = round(float(a["start"]) + float(a["out"]) - float(a["in"]), 3)
    for c in p["clips"]:
        c.setdefault("id", new_id("c"))
        c.setdefault("volume", 1.0)
        c.setdefault("transition", None)
    for i, person in enumerate(p["people"]):
        person.setdefault("id", new_id("p"))
        person.setdefault("name", f"Person {i + 1}")
        person.setdefault("color", PERSON_COLORS[i % len(PERSON_COLORS)])
        person.setdefault("avatars", {})
    for k in p["captions"]:
        k.setdefault("id", new_id("k"))
        k.setdefault("person", None)
        k.setdefault("expression", "neutral")
        if k["expression"] not in expr_ids:
            k["expression"] = "neutral"
        k.setdefault("words", None)
    for e in p["effects"]:
        e.setdefault("id", new_id("e"))
        e["params"] = {**EFFECTS.get(e.get("type"), {}).get("params", {}), **(e.get("params") or {})}
    p["overlays"] = [o for o in p["overlays"] if o.get("type") != "video" or o.get("media") in media_ids]
    for o in p["overlays"]:
        o.setdefault("id", new_id("o"))
        if o.get("type") == "video":  # timeline length always equals the used part of the file
            o["end"] = round(float(o["start"]) + float(o["out"]) - float(o["in"]), 3)
        if o.get("type") == "text":
            o.setdefault("style", "bold")
    return p


# ---------------------------------------------------------------------------
# Timeline maths (keep in sync with static/js/model.js)
# ---------------------------------------------------------------------------

def clip_length(clip: dict) -> float:
    return max(0.0, float(clip["out"]) - float(clip["in"]))


def transition_duration(clips: list[dict], i: int) -> float:
    """Overlap between clip i-1 and clip i (0 when there is no transition)."""
    if i <= 0 or i >= len(clips):
        return 0.0
    t = clips[i].get("transition") or {}
    if not t.get("type") or t["type"] not in TRANSITIONS:
        return 0.0
    want = float(t.get("duration", 0.5) or 0.0)
    limit = 0.45 * min(clip_length(clips[i - 1]), clip_length(clips[i]))
    return round(max(0.0, min(want, limit)), 3)


def clip_starts(clips: list[dict]) -> list[float]:
    starts, t = [], 0.0
    for i, c in enumerate(clips):
        t -= transition_duration(clips, i)
        starts.append(round(t, 3))
        t += clip_length(c)
    return starts


def total_duration(p: dict) -> float:
    clips = p["clips"]
    if not clips:
        return 0.0
    return round(clip_starts(clips)[-1] + clip_length(clips[-1]), 3)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def safe_folder_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\- ]+", "", name).strip().replace(" ", "_")[:60] or "project"


class ProjectStore:
    """Projects live in <projects_dir>/<id>/project.json with media/, avatars/, cache/, exports/."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, pid: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", pid or ""):
            raise ValueError("bad project id")
        return self.root / pid

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir(), key=lambda x: -x.stat().st_mtime):
            f = d / "project.json"
            if f.is_file():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                out.append({"id": d.name, "name": data.get("name", d.name),
                            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="minutes"),
                            "clips": len(data.get("clips", []))})
        return out

    def create(self, name: str) -> tuple[str, dict]:
        base = safe_folder_name(name)
        pid, n = base, 2
        while (self.root / pid).exists():
            pid, n = f"{base}_{n}", n + 1
        d = self.root / pid
        for sub in ("media", "avatars", "cache", "exports"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        proj = new_project(name)
        self.save(pid, proj)
        return pid, proj

    def rename(self, pid: str, name: str) -> dict:
        name = (name or "").strip()[:120]
        if not name:
            raise ValueError("the name can't be empty")
        proj = self.load(pid)
        proj["name"] = name
        return self.save(pid, proj)

    def delete(self, pid: str) -> None:
        d = self.dir(pid)
        if not (d / "project.json").is_file():
            raise FileNotFoundError("no such project")
        # Move the folder aside first so a half-deleted project never shows up in the list.
        trash = self.root / f".deleted_{pid}_{datetime.now().strftime('%H%M%S%f')}"
        d.rename(trash)
        shutil.rmtree(trash, ignore_errors=True)

    def load(self, pid: str) -> dict:
        f = self.dir(pid) / "project.json"
        return normalize(json.loads(f.read_text(encoding="utf-8")))

    def save(self, pid: str, proj: dict) -> dict:
        proj = normalize(proj)
        d = self.dir(pid)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "project.json.tmp"
        tmp.write_text(json.dumps(proj, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(d / "project.json")
        return proj

    def resolve(self, pid: str, rel: str) -> Path:
        """A file inside the project folder (or an absolute path the project references)."""
        p = Path(rel)
        if p.is_absolute():
            return p
        d = self.dir(pid).resolve()
        full = (d / rel).resolve()
        if d not in full.parents and full != d:
            raise ValueError("path outside project")
        return full


def media_path(store: ProjectStore, pid: str, media: dict) -> Path:
    return store.resolve(pid, media["path"])


def find(items: list[dict], item_id: Optional[str]) -> Optional[dict]:
    return next((i for i in items if i.get("id") == item_id), None)


def person_for(p: dict, person_id: Optional[str]) -> Optional[dict[str, Any]]:
    return find(p["people"], person_id)
