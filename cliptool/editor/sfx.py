"""Sound effects and music.

- A built-in library of sound effects, made with ffmpeg the first time they're needed
  (no downloads, no licences to worry about).
- With an ElevenLabs API key (Settings): new sound effects or background music from a
  text description.
"""
from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from cliptool.editor.project import new_id
from cliptool.ffmpeg_utils import ffmpeg, probe
from cliptool.logger import get_logger

log = get_logger("sfx")

# name -> (label, ffmpeg lavfi source, length)
LIBRARY: dict[str, dict] = {
    "whoosh": {"label": "Whoosh", "help": "for transitions and titles flying in",
               "src": "anoisesrc=d=0.55:c=pink:a=0.9:r=48000,highpass=f=350,lowpass=f=5000,"
                      "afade=t=in:d=0.32:curve=exp,afade=t=out:st=0.32:d=0.23:curve=exp"},
    "swoosh_fast": {"label": "Fast swoosh", "help": "quick cut accent",
                    "src": "anoisesrc=d=0.25:c=white:a=0.6:r=48000,bandpass=f=2500:w=2000,"
                           "afade=t=in:d=0.12,afade=t=out:st=0.12:d=0.13"},
    "pop": {"label": "Pop", "help": "text or sticker appearing",
            "src": "aevalsrc='0.8*sin(2*PI*(900-3500*t)*t)*exp(-38*t)':s=48000:d=0.14"},
    "click": {"label": "Click", "help": "UI tap / small accent",
              "src": "aevalsrc='0.9*sin(2*PI*2200*t)*exp(-350*t)':s=48000:d=0.04"},
    "ding": {"label": "Ding", "help": "call to action, correct answer",
             "src": "aevalsrc='0.55*sin(2*PI*1318*t)*exp(-3.5*t)+0.25*sin(2*PI*2637*t)*exp(-6*t)':s=48000:d=1.3"},
    "impact": {"label": "Impact / boom", "help": "big reveal or punchline",
               "src": "aevalsrc='0.95*sin(2*PI*(45+140*exp(-18*t))*t)*exp(-4.5*t)':s=48000:d=0.9"},
    "riser": {"label": "Riser", "help": "build tension before a reveal",
              "src": "aevalsrc='0.35*(t/2)*sin(2*PI*(180+900*t*t/4)*t)':s=48000:d=2,"
                     "afade=t=out:st=1.92:d=0.08"},
    "bleep": {"label": "Bleep", "help": "censor tone",
              "src": "sine=frequency=1000:sample_rate=48000:duration=0.6,volume=0.5"},
    "record_scratch": {"label": "Record scratch", "help": "wait, what?",
                       "src": "aevalsrc='0.6*sin(2*PI*(300+800*sin(2*PI*9*t))*t)*exp(-3*t)':s=48000:d=0.5"},
    "boing": {"label": "Boing", "help": "comedy bounce",
              "src": "aevalsrc='0.7*sin(2*PI*(220+120*sin(2*PI*14*t))*t)*exp(-4*t)':s=48000:d=0.7"},
}


def library_dir() -> Path:
    from cliptool.settings import settings_file
    return settings_file().parent / "sfx"


def library_file(name: str) -> Path:
    if name not in LIBRARY:
        raise ValueError(f"unknown sound effect '{name}'")
    f = library_dir() / f"{name}.wav"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.stem + ".tmp.wav")
        ffmpeg(["-f", "lavfi", "-i", LIBRARY[name]["src"], "-ac", "2", "-ar", "48000", tmp], desc=f"make {name} sound")
        # same loudness for every sound: peak at -3 dB
        out = ffmpeg(["-i", tmp, "-af", "volumedetect", "-f", "null", "-"], desc="measure").stderr or ""
        m = re.search(r"max_volume:\s*(-?[\d.]+) dB", out)
        gain = -3.0 - float(m.group(1)) if m else 0.0
        ffmpeg(["-i", tmp, "-af", f"volume={gain:.2f}dB", "-ac", "2", "-ar", "48000", f], desc=f"level {name}")
        tmp.unlink(missing_ok=True)
    return f


def meta() -> dict:
    return {k: {"label": v["label"], "help": v["help"]} for k, v in LIBRARY.items()}


# ---- adding a sound to a project -------------------------------------------------------
def ensure_media(p: dict, store, pid: str, src: Path, name: Optional[str] = None) -> dict:
    """Copy a sound into the project (once) and return its media entry."""
    name = name or src.name
    for m in p["media"]:
        if m.get("sfx_source") == src.name and m.get("kind") == "audio":
            return m
    d = store.dir(pid) / "media"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / src.name
    if not dest.exists():
        shutil.copyfile(src, dest)
    info = probe(dest)
    m = {"id": new_id("m"), "kind": "audio", "name": name, "path": f"media/{dest.name}",
         "duration": round(info.duration, 3), "width": 0, "height": 0, "has_audio": True, "fps": 0,
         "alpha": False, "thumb": None, "proxy": None, "recording": False, "sfx_source": src.name}
    p["media"].append(m)
    return m


def add_sound(p: dict, store, pid: str, name: str, t: float, volume: float = 0.6, auto: Optional[str] = None,
              anchor: str = "start") -> dict:
    """Put a library sound on the audio track at timeline time t.

    anchor="peak": the loudest point of the sound lands on t (e.g. a whoosh peaks on the cut).
    """
    m = ensure_media(p, store, pid, library_file(name), LIBRARY[name]["label"])
    length = float(m["duration"])
    if anchor == "peak" and name in ("whoosh", "swoosh_fast", "riser"):
        t -= length * (0.58 if name == "whoosh" else 0.5 if name == "swoosh_fast" else 0.96)
    t = max(0.0, t)
    item = {"id": new_id("a"), "media": m["id"], "name": LIBRARY[name]["label"], "in": 0.0, "out": length,
            "start": round(t, 3), "end": round(t + length, 3), "volume": volume, "fade_in": 0, "fade_out": 0,
            "sfx": name}
    if auto:
        item["auto"] = auto
    p["audio"].append(item)
    return item


# ---- ElevenLabs ---------------------------------------------------------------------------
class GenerateError(RuntimeError):
    pass


def _eleven(path: str, body: dict, key: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(f"https://api.elevenlabs.io{path}", data=json.dumps(body).encode(),
                                 headers={"xi-api-key": key, "Content-Type": "application/json",
                                          "Accept": "audio/mpeg"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code in (401, 403):
            raise GenerateError("ElevenLabs rejected the API key (check it in Settings).") from exc
        raise GenerateError(f"ElevenLabs error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GenerateError(f"Could not reach ElevenLabs: {exc.reason}") from exc


def generate(kind: str, prompt: str, seconds: float, out_dir: Path) -> Path:
    """kind: 'sfx' (0.5-22 s) or 'music' (10 s - 5 min). Returns the saved mp3."""
    from cliptool.settings import get_key
    key = get_key("elevenlabs")
    if not key:
        raise GenerateError("Add an ElevenLabs API key in Settings to generate sounds.")
    prompt = prompt.strip()
    if not prompt:
        raise GenerateError("Describe the sound first.")
    if kind == "music":
        ms = int(max(10, min(300, seconds)) * 1000)
        data = _eleven("/v1/music", {"prompt": prompt, "music_length_ms": ms}, key, timeout=600)
    else:
        data = _eleven("/v1/sound-generation", {"text": prompt, "duration_seconds": max(0.5, min(22.0, seconds)),
                                                 "prompt_influence": 0.5}, key)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", prompt.lower()).strip("_")[:40] or kind
    out = out_dir / f"{kind}_{slug}_{new_id('')}.mp3"
    out.write_bytes(data)
    return out
