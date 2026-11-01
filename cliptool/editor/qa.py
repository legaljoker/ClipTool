"""Check a finished video for problems before it is uploaded.

Layer 1 - picture and sound, measured on the rendered video with ffmpeg:
  black frames, frozen picture, long silences, loudness (platforms expect about -14 LUFS),
  clipping (sound distorting at full volume).
Layer 2 - the edit itself: captions (overlaps, too fast to read, past the end), text hidden
  behind the TikTok / Shorts buttons, missing files, no hook in the first seconds.
Anything that could not be checked is listed as "not checked", never reported as fine.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from cliptool.editor.project import CANVAS_PRESETS, media_path, total_duration
from cliptool.ffmpeg_utils import ffmpeg_bin
from cliptool.logger import get_logger

log = get_logger("qa")

# Parts of a vertical video covered by the app's buttons and description (fractions of the frame)
SAFE = {"bottom": 0.80, "right": 0.86, "top": 0.06}


def issue(level: str, message: str, time: Optional[float] = None, kind: str = "") -> dict:
    return {"level": level, "message": message, "time": None if time is None else round(time, 2), "kind": kind}


def measure(video: Path, cancel=None) -> dict:
    """One ffmpeg pass over the video and its sound."""
    vf = "blackdetect=d=0.1:pix_th=0.10,freezedetect=n=-60dB:d=1.5"
    af = "silencedetect=n=-40dB:d=0.6,astats=metadata=0:reset=0,ebur128=peak=true"
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(video), "-vf", vf, "-af", af, "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    err = proc.stderr
    out: dict = {"black": [], "freeze": [], "silence": []}
    for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+)", err):
        out["black"].append((float(m.group(1)), float(m.group(2))))
    fs = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", err)]
    fe = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", err)]
    out["freeze"] = list(zip(fs, fe + [None] * (len(fs) - len(fe))))
    ss = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    se = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    out["silence"] = list(zip(ss, se + [None] * (len(ss) - len(se))))
    summary = err[err.rfind("Summary:"):] if "Summary:" in err else ""
    m = re.search(r"I:\s*(-?[\d.]+) LUFS", summary)
    out["lufs"] = float(m.group(1)) if m else None
    m = re.search(r"Peak:\s*(-?[\d.]+|-inf) dBFS", summary)
    out["true_peak"] = float(m.group(1)) if m and m.group(1) != "-inf" else None
    flat = re.findall(r"Flat factor: ([\d.]+)", err)
    out["flat_factor"] = max((float(x) for x in flat), default=0.0)
    peaks = re.findall(r"Peak level dB: (-?[\d.]+|-inf)", err)
    out["peak_db"] = max((float(x) for x in peaks if x != "-inf"), default=None)
    out["has_audio"] = "Audio:" in err
    return out


def check_layer1(video: Path, duration: float) -> tuple[list[dict], list[str]]:
    issues: list[dict] = []
    m = measure(video)
    for a, b in m["black"]:
        if a < 0.05 and b - a < 0.3:
            continue  # a fade-in from black at the very start is normal
        issues.append(issue("error" if b - a > 0.5 else "warning",
                            f"Black picture for {b - a:.1f}s", a, "black"))
    for a, b in m["freeze"]:
        length = (b or duration) - a
        issues.append(issue("warning", f"Picture frozen for {length:.1f}s (stuck frame or still image)", a, "freeze"))
    for a, b in m["silence"]:
        a = max(0.0, a)
        length = (b or duration) - a
        if b is None and length < 1.0:
            continue  # quiet last moment
        issues.append(issue("warning" if length >= 1.0 else "info",
                            f"Silent for {length:.1f}s - viewers may scroll away", a, "silence"))
    skipped = []
    if not m["has_audio"]:
        issues.append(issue("error", "The video has no sound track", None, "audio"))
    else:
        if m["lufs"] is None:
            skipped.append("Loudness (could not be measured)")
        elif m["lufs"] < -18:
            issues.append(issue("warning", f"Too quiet overall ({m['lufs']:.1f} LUFS; aim for about -14). "
                                           "Turn on 'Even out the volume' in Export.", None, "loudness"))
        elif m["lufs"] > -10:
            issues.append(issue("warning", f"Very loud overall ({m['lufs']:.1f} LUFS; aim for about -14).",
                                None, "loudness"))
        if m["flat_factor"] > 0.5 or (m["peak_db"] is not None and m["peak_db"] >= -0.1):
            issues.append(issue("warning", "The sound clips (distorts) at its loudest moments. Lower the volume "
                                           "of the loud clip or sound.", None, "clipping"))
    return issues, skipped


def check_layer2(p: dict, store, pid: str) -> list[dict]:
    issues: list[dict] = []
    total = total_duration(p)
    preset = CANVAS_PRESETS.get(p["preset"], {})
    vertical = preset.get("height", 1) > preset.get("width", 1)
    for m in p["media"]:
        used = any(c["media"] == m["id"] for c in p["clips"]) or any(a["media"] == m["id"] for a in p["audio"]) \
            or any(o.get("media") == m["id"] for o in p["overlays"])
        if used and not media_path(store, pid, m).exists():
            issues.append(issue("error", f"File missing: {m.get('name') or m.get('path')}", None, "missing"))
    caps = sorted(p["captions"], key=lambda k: k["start"])
    for a, b in zip(caps, caps[1:]):
        if b["start"] < a["end"] - 0.05 and (a.get("person") == b.get("person")):
            issues.append(issue("warning", f"Two captions overlap: “{a['text'][:30]}” and “{b['text'][:30]}”",
                                b["start"], "caption"))
    for k in caps:
        words = len(k["text"].split())
        length = max(0.01, k["end"] - k["start"])
        if k["start"] >= total - 0.05:
            issues.append(issue("warning", f"Caption after the end of the video: “{k['text'][:40]}”", total, "caption"))
        elif words / length > 5.5 and words >= 4:
            issues.append(issue("info", f"Caption may be too fast to read ({words} words in {length:.1f}s)",
                                k["start"], "caption"))
        ws = k.get("words") or []
        if ws and (ws[0]["start"] < k["start"] - 0.3 or ws[-1]["end"] > k["end"] + 0.3):
            issues.append(issue("warning", f"Caption words are out of sync with the caption: “{k['text'][:30]}”",
                                k["start"], "caption"))
        if not k["text"].strip():
            issues.append(issue("info", "Empty caption", k["start"], "caption"))
    if vertical:
        cy = float(p["caption_style"].get("y", 0.72))
        if p["captions"] and cy > SAFE["bottom"]:
            issues.append(issue("warning", "Captions are low enough to be hidden behind the description on "
                                           "TikTok / Shorts. Drag them up a little.", None, "safe"))
        for o in p["overlays"]:
            if o.get("type") not in ("text", "title", "image"):
                continue
            x, y = float(o.get("x", 0.5)), float(o.get("y", 0.5))
            if y > SAFE["bottom"] or x > SAFE["right"] or y < SAFE["top"]:
                label = o.get("text") or "A picture"
                issues.append(issue("warning", f"“{str(label)[:30]}” may be hidden behind the app's buttons",
                                    float(o.get("start", 0)), "safe"))
    from cliptool.editor import textfx
    W, H = preset.get("width", 1080), preset.get("height", 1920)
    for o in p["overlays"]:
        if o.get("type") == "title":
            lay = textfx.title_layout(o, W, H)
            wide = lay["w"]
        elif o.get("type") == "text":
            px = textfx.text_px(o, W, H)
            st = textfx.TEXT_STYLES.get(o.get("style"), textfx.TEXT_STYLES["bold"])
            wide = max((textfx.measure(line, st.get("font", "Montserrat"), px, bool(st.get("bold")))
                        for line in str(o.get("text", "")).split("\n")), default=0)
        else:
            continue
        if wide > 0.96 * W:
            issues.append(issue("warning", f"“{str(o.get('text'))[:30]}” is wider than the video and gets cut off. "
                                           "Make it smaller or shorter.", float(o.get("start", 0)), "overflow"))
    hook = any(float(o.get("start", 99)) < 1.0 and o.get("type") in ("text", "title") for o in p["overlays"])
    if total > 8 and not hook and not any(k["start"] < 1.0 for k in caps):
        issues.append(issue("info", "No text on screen in the first second. A hook title helps people stay.",
                            0.0, "hook"))
    if vertical and total > 180:
        issues.append(issue("info", f"The video is {total / 60:.1f} minutes long; YouTube Shorts allows up to "
                                    "3 minutes.", None, "length"))
    return issues


def run(p: dict, store, pid: str, video: Optional[Path]) -> dict:
    issues = check_layer2(p, store, pid)
    skipped: list[str] = []
    checked = "the edit"
    if video and video.exists():
        l1, sk = check_layer1(video, total_duration(p))
        issues = l1 + issues
        skipped += sk
        checked = f"{video.name} and the edit"
    else:
        skipped.append("Picture and sound (no rendered video to check)")
    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (order.get(i["level"], 3), i["time"] if i["time"] is not None else -1))
    return {"issues": issues, "skipped": skipped, "checked": checked}
