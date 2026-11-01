"""Turns an editor project into one ffmpeg command (final video or a single preview frame)."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from cliptool import reformat
from cliptool.editor import textfx
from cliptool.avatars import ImageCache, _talk_frames, _write_concat
from cliptool.captions import _inline_color, ass_escape, ass_time, group_words, hex_to_ass
from cliptool.editor.project import (AUDIO_EFFECTS, CANVAS_PRESETS, CHROMA_COLORS, EFFECTS, ProjectStore,
                                     clip_length, clip_starts, find, media_path, total_duration,
                                     transition_duration)
from cliptool.emotions import FALLBACKS
from cliptool.export import PLATFORMS
from cliptool.ffmpeg_utils import MediaInfo, ffmpeg_bin, filter_script_args
from cliptool.logger import get_logger
from cliptool.transcribe import Word

log = get_logger("editor.render")

FPS = 30
STALL_SECONDS = 180  # a render step may be slow (e.g. big blur), but never this long without output


class RenderError(RuntimeError):
    pass


def canvas_size(p: dict) -> tuple[int, int]:
    preset = CANVAS_PRESETS[p["preset"]]
    return preset["width"], preset["height"]


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Captions: word groups (mirrored in static/js/model.js -> captionGroups)
# ---------------------------------------------------------------------------

def caption_words(cap: dict) -> list[Word]:
    text = (cap.get("text") or "").strip()
    words = cap.get("words") or []
    if words and " ".join(w["text"].strip() for w in words) == " ".join(text.split()):
        return [Word(float(w["start"]), float(w["end"]), " " + w["text"].strip()) for w in words]
    tokens = text.split()
    if not tokens:
        return []
    start, end = float(cap["start"]), float(cap["end"])
    step = max(0.01, end - start) / len(tokens)
    return [Word(start + i * step, start + (i + 1) * step, " " + t) for i, t in enumerate(tokens)]


@dataclass
class Group:
    caption: dict
    start: float
    end: float
    words: list[Word] = field(default_factory=list)


def caption_groups(p: dict) -> list[Group]:
    max_words = int(p["caption_style"].get("max_words", 3) or 3)
    out: list[Group] = []
    for cap in sorted(p["captions"], key=lambda c: c["start"]):
        groups = group_words(caption_words(cap), max_words)
        for i, g in enumerate(groups):
            start = cap["start"] if i == 0 else g[0].start
            end = groups[i + 1][0].start if i + 1 < len(groups) else cap["end"]
            if end - start > 0.02:
                out.append(Group(cap, start, end, g))
    return out


def font_px(p: dict, size: float) -> int:
    w, h = canvas_size(p)
    return max(8, int(round(size * min(w, h) / 1080)))


def caption_color(p: dict, cap: dict) -> str:
    st = p["caption_style"]
    person = find(p["people"], cap.get("person"))
    if st.get("use_person_color") and person and person.get("color"):
        return person["color"]
    return st.get("color", "#FFFFFF")


def build_ass(p: dict) -> str:
    W, H = canvas_size(p)
    st = p["caption_style"]
    fs = font_px(p, _num(st.get("size"), 78))
    outline = max(0, int(round(_num(st.get("outline_width"), 7) * min(W, H) / 1080)))
    X, Y = int(_num(st.get("x"), 0.5) * W), int(_num(st.get("y"), 0.72) * H)
    upper = bool(st.get("uppercase", True))
    highlight = st.get("highlight", "#FFE000")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st.get('font', 'Arial')},{fs},{hex_to_ass(st.get('color', '#FFFFFF'))},{hex_to_ass(highlight)},{hex_to_ass(st.get('outline', '#000000'))},{hex_to_ass('#000000', 0x80)},-1,0,0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},5,10,10,10,1
Style: Txt,Arial,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,5,10,10,10,1
Style: Box,Arial,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,10,0,5,10,10,10,1
Style: Shape,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    pop = r"\fscx112\fscy112" if st.get("pop", True) else ""
    for g in caption_groups(p):
        base = caption_color(p, g.caption)
        texts = [ass_escape(w.text.strip().upper() if upper else w.text.strip()) for w in g.words]
        for i, w in enumerate(g.words):
            s = g.start if i == 0 else w.start
            e = g.words[i + 1].start if i + 1 < len(g.words) else g.end
            if e - s < 0.01:
                continue
            parts = [f"{{\\c{_inline_color(highlight)}{pop}}}{t}{{\\c{_inline_color(base)}\\fscx100\\fscy100}}"
                     if j == i else t for j, t in enumerate(texts)]
            ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,"
                      f"{{\\an5\\pos({X},{Y})\\c{_inline_color(base)}}}" + " ".join(parts))
    for o in p["overlays"]:
        if o.get("type") == "text":
            ev.extend(textfx.text_events(o, W, H))
        elif o.get("type") == "title":
            ev.extend(textfx.title_events(o, W, H))
    return header + "\n".join(ev) + "\n"


# ---------------------------------------------------------------------------
# Caption avatars
# ---------------------------------------------------------------------------

def avatar_image(store: ProjectStore, pid: str, person: dict, expression: str, talking: bool) -> Optional[Path]:
    avatars = person.get("avatars") or {}
    chain = [expression] + FALLBACKS.get(expression, []) + ["neutral"]
    for emo in chain:
        for key in ([f"{emo}_talk"] if talking else []) + [emo]:
            if avatars.get(key):
                path = store.resolve(pid, avatars[key])
                if path.exists():
                    return path
    for key, rel in avatars.items():  # anything at all
        if rel and not key.endswith("_talk") and store.resolve(pid, rel).exists():
            return store.resolve(pid, rel)
    return None


def _steps(values: list[tuple[float, float]]) -> str:
    """Piecewise-constant ffmpeg expression from (time, value) pairs."""
    expr = f"{values[0][1]:.0f}"
    prev = values[0][1]
    for t, v in values[1:]:
        if abs(v - prev) >= 1:
            expr += f"+({v - prev:.0f})*gte(t,{t:.3f})"
            prev = v
    return expr


@dataclass
class AvatarTrack:
    concat: Path
    x: str
    y: str


def build_avatar_track(p: dict, store: ProjectStore, pid: str, work: Path) -> Optional[AvatarTrack]:
    st = p["caption_style"]
    if not st.get("show_avatar", True) or not p["people"]:
        return None
    W, H = canvas_size(p)
    size = int(_num(st.get("avatar_size"), 0.22) * W) // 2 * 2
    if size < 8:
        return None
    fs = font_px(p, _num(st.get("size"), 78))
    X, Y = _num(st.get("x"), 0.5) * W, _num(st.get("y"), 0.72) * H
    place = st.get("avatar_place", "above")
    cache = ImageCache(work / "caption_avatars", size)
    blank = cache.get(None)
    total = total_duration(p)

    entries: list[tuple[Path, float]] = []
    xs: list[tuple[float, float]] = []
    ys: list[tuple[float, float]] = []
    cursor = 0.0
    any_img = False
    for g in caption_groups(p):
        person = find(p["people"], g.caption.get("person"))
        if not person:
            continue
        expr = g.caption.get("expression") or "neutral"
        still = avatar_image(store, pid, person, expr, False)
        if still is None:
            continue
        talk = avatar_image(store, pid, person, expr, True) or still
        s, e = max(g.start, cursor), min(g.end, total)
        if e <= s:
            continue
        if s > cursor:
            entries.append((blank, s - cursor))
        entries.extend(_talk_frames(s, e, cache.get(talk), cache.get(still), _num(st.get("talk_flap"), 0.15)))
        text_w = sum(len(w.text.strip()) + 1 for w in g.words) * fs * 0.6
        if place == "left":
            ax, ay = X - text_w / 2 - 14 - size, Y - size / 2
        elif place == "right":
            ax, ay = X + text_w / 2 + 14, Y - size / 2
        else:
            ax, ay = X - size / 2, Y - fs * 0.75 - 12 - size
        xs.append((s, max(0, min(W - size, ax))))
        ys.append((s, max(0, min(H - size, ay))))
        cursor = e
        any_img = True
    if not any_img:
        return None
    if cursor < total:
        entries.append((blank, total - cursor))
    concat = _write_concat(entries, work / "caption_avatars.ffconcat")
    return AvatarTrack(concat, _steps(xs), _steps(ys))


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def effects_chain(effects: list[dict], W: int, H: int) -> list[str]:
    """Filters applied to the whole edited sequence."""
    filters: list[str] = []
    zoom_terms, cx_terms, cy_terms, sx_terms, sy_terms = [], [], [], [], []
    flash_terms = []
    for e in sorted(effects, key=lambda e: _num(e.get("start"))):
        a, b = _num(e.get("start")), _num(e.get("end"))
        if b - a < 0.02 or e.get("type") not in EFFECTS:
            continue
        prm = {**EFFECTS[e["type"]]["params"], **(e.get("params") or {})}
        win = f"between(in_time,{a:.3f},{b:.3f})"
        en = f"enable='between(t,{a:.3f},{b:.3f})'"
        kind = e["type"]
        if kind in ("zoom_in", "zoom_out"):
            amt = max(1.0, min(3.0, _num(prm.get("amount"), 1.35))) - 1
            if kind == "zoom_in":
                r = max(0.01, min(_num(prm.get("ramp"), 0.25), (b - a) / 2))
                zoom_terms.append(f"{amt:.3f}*clip((in_time-{a:.3f})/{r:.3f},0,1)*clip(({b:.3f}-in_time)/{r:.3f},0,1)")
            else:
                zoom_terms.append(f"{amt:.3f}*clip(({b:.3f}-in_time)/{b - a:.3f},0,1)*{win}")
            cx_terms.append(f"({_num(prm.get('x'), 0.5) - 0.5:.3f})*{win}")
            cy_terms.append(f"({_num(prm.get('y'), 0.5) - 0.5:.3f})*{win}")
        elif kind == "shake":
            k = max(0.0, min(1.0, _num(prm.get("intensity"), 0.5)))
            zoom_terms.append(f"{0.03 + 0.08 * k:.3f}*{win}")
            sx_terms.append(f"{0.025 * k:.4f}*iw*sin(in_time*53)*{win}")
            sy_terms.append(f"{0.02 * k:.4f}*ih*cos(in_time*41)*{win}")
        elif kind == "flash":
            s = max(0.0, min(1.0, _num(prm.get("strength"), 0.8)))
            flash_terms.append(f"{s:.2f}*clip(1-(t-{a:.3f})/{b - a:.3f},0,1)*between(t,{a:.3f},{b:.3f})")
        elif kind == "black_white":
            filters.append(f"hue=s=0:{en}")
        elif kind == "sepia":
            filters.append(f"colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131:{en}")
        elif kind == "blur":
            r = int(max(1, min(_num(prm.get("amount"), 8), min(W, H) / 8 - 1)))
            filters.append(f"boxblur={r}:2:{en}")
        elif kind == "vignette":
            filters.append(f"vignette=PI/4:{en}")
        elif kind == "invert":
            filters.append(f"negate:{en}")
        elif kind == "glitch":
            amt = int(max(1, min(60, _num(prm.get("amount"), 12))))
            filters.append(f"rgbashift=rh=-{amt}:bh={amt}:gv={amt // 3}:{en}")
    if zoom_terms or sx_terms:
        z = "max(1,1+" + "+".join(zoom_terms or ["0"]) + ")"
        cx = "0.5+" + "+".join(cx_terms or ["0"])
        cy = "0.5+" + "+".join(cy_terms or ["0"])
        sx = "+".join(sx_terms or ["0"])
        sy = "+".join(sy_terms or ["0"])
        filters.insert(0, f"zoompan=z='{z}':x='({cx})*iw-iw/zoom/2+{sx}':y='({cy})*ih-ih/zoom/2+{sy}'"
                          f":d=1:s={W}x{H}:fps={FPS}")
    if flash_terms:
        filters.append(f"eq=brightness='{'+'.join(flash_terms)}':eval=frame")
    return filters


# ---------------------------------------------------------------------------
# Full graph
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    args: list
    duration: float
    script: Path


def build_plan(p: dict, store: ProjectStore, pid: str, work: Path, audio: bool = True) -> Plan:
    if not p["clips"]:
        raise RenderError("The timeline is empty - add a video clip first.")
    work.mkdir(parents=True, exist_ok=True)
    W, H = canvas_size(p)
    fr = p["frame"]
    style = fr.get("style", "blur")
    style_opts = {k: fr.get(k) for k in ("scale", "offset_y", "color", "color2")}
    media_by_id = {m["id"]: m for m in p["media"]}
    clips = p["clips"]
    starts = clip_starts(clips)
    total = total_duration(p)

    inputs: list = []
    graph: list[str] = []
    n_in = 0

    def add_input(args: list) -> int:
        nonlocal n_in
        inputs.extend(args)
        n_in += 1
        return n_in - 1

    # Pieces of one take that follow each other without a transition (e.g. after cutting out
    # pauses) form a "run": the file is decoded once and only the kept frames are selected.
    # This keeps memory low even with hundreds of cuts.
    runs: list[list[int]] = []
    for i, c in enumerate(clips):
        prev = clips[runs[-1][-1]] if runs else None
        if prev and transition_duration(clips, i) == 0 and prev["media"] == c["media"] \
                and _num(c["in"]) >= _num(prev["out"]) - 0.001 and len(runs[-1]) < 400:
            runs[-1].append(i)
        else:
            runs.append([i])

    seq_v = None
    for r, run in enumerate(runs):
        first = clips[run[0]]
        m = media_by_id[first["media"]]
        src = media_path(store, pid, m)
        if not src.exists():
            raise RenderError(f"Video file is missing: {src}")
        lo = min(_num(clips[i]["in"]) for i in run)
        hi = max(_num(clips[i]["out"]) for i in run)
        L = sum(clip_length(clips[i]) for i in run)
        idx = add_input(["-ss", f"{lo:.3f}", "-t", f"{hi - lo + 0.1:.3f}", "-an", "-i", src])
        if len(run) == 1:
            graph.append(f"[{idx}:v]setpts=PTS-STARTPTS[r{r}]")
        else:
            keep = "+".join(f"between(t,{_num(clips[i]['in']) - lo:.4f},{_num(clips[i]['out']) - lo - 0.0005:.4f})"
                            for i in run)
            # new time = time in the file minus everything skipped before it
            shift, removed, pos = [], 0.0, None
            for i in run:
                a_i = _num(clips[i]["in"]) - lo
                if pos is not None:
                    removed = a_i - pos
                    shift.append(f"{removed:.4f}*gte(T,{a_i - 0.0005:.4f})" if removed else "")
                else:
                    shift.append(f"{a_i:.4f}")
                pos = _num(clips[i]["out"]) - lo
            # gaps add up: each term removes the gap before piece k once T passes it
            expr = "T-(" + "+".join(x for x in shift if x) + ")"
            graph.append(f"[{idx}:v]setpts=PTS-STARTPTS,select='{keep}',setpts='({expr})/TB'[r{r}]")
        info = MediaInfo(src, L, int(m.get("width") or W), int(m.get("height") or H), FPS,
                         bool(m.get("has_audio", True)), True)
        graph.append(reformat.build_filter(info, W, H, style, f"r{r}", f"f{r}", None,
                                           int(_num(fr.get("blur"), 20)), style_opts))
        graph.append(f"[f{r}]fps={FPS},format=yuv420p,setsar=1,tpad=stop_mode=clone:stop_duration=1,"
                     f"trim=duration={L:.3f},setpts=PTS-STARTPTS,settb=AVTB[v{r}]")
        if seq_v is None:
            seq_v = f"v{r}"
            continue
        i = run[0]
        d = transition_duration(clips, i)
        if d > 0:
            t = clips[i]["transition"]["type"]
            graph.append(f"[{seq_v}][v{r}]xfade=transition={t}:duration={d:.3f}:offset={starts[i]:.3f}[sv{r}]")
        else:
            graph.append(f"[{seq_v}][v{r}]concat=n=2:v=1:a=0[sv{r}]")
        seq_v = f"sv{r}"

    seq_a = None
    if audio:  # sound: one short input per piece (cheap), joined with tiny fades so cuts don't click
        for i, c in enumerate(clips):
            m = media_by_id[c["media"]]
            L = clip_length(c)
            if m.get("has_audio", True):
                aidx = add_input(["-ss", f"{_num(c['in']):.3f}", "-t", f"{L:.3f}", "-vn", "-i", media_path(store, pid, m)])
            else:
                aidx = add_input(["-f", "lavfi", "-t", f"{L:.3f}", "-i", "anullsrc=r=48000:cl=stereo"])
            vol = max(0.0, min(4.0, _num(c.get("volume"), 1.0)))
            declick = f",afade=t=in:d=0.01,afade=t=out:st={L - 0.01:.3f}:d=0.01" if L > 0.1 else ""
            graph.append(f"[{aidx}:a]asetpts=PTS-STARTPTS,aresample=48000,"
                         f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={vol:.2f},"
                         f"apad=whole_dur={L:.3f},atrim=duration={L:.3f}{declick}[a{i}]")
        pending: list[str] = []  # pieces joined with plain concat, flushed before a crossfade

        def flush(label: str) -> str:
            if len(pending) == 1:
                return pending[0]
            graph.append("".join(f"[{x}]" for x in pending) + f"concat=n={len(pending)}:v=0:a=1[{label}]")
            return label

        for i in range(len(clips)):
            d = transition_duration(clips, i)
            if i and d > 0:
                left = flush(f"ac{i}")
                graph.append(f"[{left}][a{i}]acrossfade=d={d:.3f}[ax{i}]")
                pending = [f"ax{i}"]
            else:
                pending.append(f"a{i}")
        seq_a = flush("aseq")

    cur = seq_v
    fx = effects_chain(p["effects"], W, H)
    if fx:
        graph.append(f"[{cur}]{','.join(fx)}[fx]")
        cur = "fx"

    extra_audio: list[str] = []  # labels mixed into the soundtrack
    for j, o in enumerate(x for x in p["overlays"] if x.get("type") in ("image", "video")):
        pw = max(2, int(_num(o.get("w"), 0.3) * W) // 2 * 2)
        a, b = _num(o.get("start")), _num(o.get("end"), total)
        x, y = _num(o.get("x"), 0.5) * W, _num(o.get("y"), 0.3) * H
        if o["type"] == "image":
            if not o.get("src") or not store.resolve(pid, o["src"]).exists():
                log.warning("Picture missing: %s", o.get("src"))
                continue
            k = add_input(["-loop", "1", "-t", f"{total + 1:.3f}", "-i", store.resolve(pid, o["src"])])
            graph.append(f"[{k}:v]scale={pw}:-2,format=rgba[img{j}]")
        else:
            m = media_by_id.get(o.get("media"))
            if not m or not media_path(store, pid, m).exists():
                log.warning("Video overlay file missing for %s", o.get("id"))
                continue
            L = max(0.05, _num(o.get("out")) - _num(o.get("in")))
            dec = ["-c:v", "libvpx-vp9"] if m.get("alpha") else []  # keeps the transparency of VTuber webm
            k = add_input([*dec, "-ss", f"{_num(o.get('in')):.3f}", "-t", f"{L:.3f}", "-i", media_path(store, pid, m)])
            graph.extend(video_overlay_chain(o, k, j, pw, a))
            vol = max(0.0, min(4.0, _num(o.get("volume"), 1.0)))
            if audio and m.get("has_audio") and vol > 0:
                graph.append(audio_chain(f"{k}:a", vol, a, 0, 0, f"ova{j}"))
                extra_audio.append(f"ova{j}")
        graph.append(f"[{cur}][img{j}]overlay=x='{x:.1f}-w/2':y='{y:.1f}-h/2':"
                     f"enable='between(t,{a:.3f},{b:.3f})':eof_action=pass:format=auto[ov{j}]")
        cur = f"ov{j}"

    ducked: list[str] = []  # music that dips under the voice
    if audio:
        mutes = [e for e in p["effects"] if e.get("type") in AUDIO_EFFECTS and _num(e.get("end")) > _num(e.get("start"))]
        if mutes:
            off = ",".join(f"volume=0:enable='between(t,{_num(e['start']):.3f},{_num(e['end']):.3f})'" for e in mutes)
            graph.append(f"[{seq_a}]{off}[muted]")
            seq_a = "muted"
        for j, e in enumerate(x for x in mutes if x["type"] == "bleep"):
            a, b = _num(e["start"]), _num(e["end"])
            f = max(200, min(4000, int(_num((e.get("params") or {}).get("tone"), 1000))))
            k = add_input(["-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=48000:duration={b - a:.3f}"])
            graph.append(audio_chain(f"{k}:a", 0.3, a, 0.005, 0.005, f"blp{j}", b - a))
            extra_audio.append(f"blp{j}")
        for j, it in enumerate(p.get("audio", [])):
            m = media_by_id.get(it["media"])
            if not m or not media_path(store, pid, m).exists():
                continue
            L = max(0.05, _num(it["out"]) - _num(it["in"]))
            k = add_input(["-ss", f"{_num(it['in']):.3f}", "-t", f"{L:.3f}", "-i", media_path(store, pid, m)])
            graph.append(audio_chain(f"{k}:a", max(0.0, min(4.0, _num(it.get("volume"), 1.0))), _num(it["start"]),
                                     _num(it.get("fade_in")), _num(it.get("fade_out")), f"aud{j}", L))
            (ducked if it.get("duck") else extra_audio).append(f"aud{j}")
        if ducked:
            outs = "".join(f"[sc{j}]" for j in range(len(ducked)))
            graph.append(f"[{seq_a}]asplit={len(ducked) + 1}[voice]{outs}")
            seq_a = "voice"
            for j, lab in enumerate(ducked):
                graph.append(f"[{lab}][sc{j}]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=400:"
                             f"makeup=1[{lab}d]")
                extra_audio.append(f"{lab}d")

    track = build_avatar_track(p, store, pid, work)
    if track:
        k = add_input(["-f", "concat", "-safe", "0", "-i", track.concat.resolve()])
        graph.append(f"[{k}:v]format=rgba[avt]")
        graph.append(f"[{cur}][avt]overlay=x='{track.x}':y='{track.y}':eof_action=pass:format=auto[av]")
        cur = "av"

    if p["captions"] or any(o.get("type") in ("text", "title") for o in p["overlays"]):
        (work / "captions.ass").write_text(build_ass(p), encoding="utf-8")
        fonts = work / "fonts"  # bundled fonts, next to the subtitle file (no path escaping needed)
        fonts.mkdir(exist_ok=True)
        for f in textfx.font_files():
            if not (fonts / f.name).exists():
                shutil.copyfile(f, fonts / f.name)
        graph.append(f"[{cur}]ass=captions.ass:fontsdir=fonts[cap]")
        cur = "cap"

    # (every piece is already 30 fps; a final fps filter here never ends after zoompan on some ffmpeg versions)
    graph.append(f"[{cur}]format=yuv420p[vout]")
    maps = ["-map", "[vout]"]
    if audio:
        if extra_audio:
            labels = "".join(f"[{x}]" for x in [seq_a, *extra_audio])
            graph.append(f"{labels}amix=inputs={1 + len(extra_audio)}:normalize=0:duration=first:"
                         f"dropout_transition=0[mixa]")
            seq_a = "mixa"
        a_out = f"[{seq_a}]"
        if p.get("export", {}).get("loudnorm", True):
            graph.append(f"[{seq_a}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
            a_out = "[aout]"
        maps += ["-map", a_out]
    script = work / ("graph.filter" if audio else "graph_frame.filter")
    script.write_text(";\n".join(graph), encoding="utf-8")
    args = [*inputs, *filter_script_args(Path(script.name)), *maps]
    return Plan(args, total, script)


def audio_chain(src: str, vol: float, start: float, fade_in: float, fade_out: float, label: str,
                length: Optional[float] = None) -> str:
    chain = f"[{src}]asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={vol:.2f}"
    if fade_in > 0:
        chain += f",afade=t=in:d={fade_in:.2f}"
    if fade_out > 0 and length:
        chain += f",afade=t=out:st={max(0.0, length - fade_out):.2f}:d={fade_out:.2f}"
    ms = int(round(start * 1000))
    return chain + (f",adelay={ms}:all=1" if ms > 0 else "") + f"[{label}]"


def video_overlay_chain(o: dict, k: int, j: int, pw: int, start: float) -> list[str]:
    """Webcam / VTuber / any video placed on top: size, green-screen removal, circle shape."""
    shape = o.get("shape", "rect")
    chain = f"[{k}:v]setpts=PTS-STARTPTS+{start:.3f}/TB,fps={FPS}"
    if shape == "circle":
        chain += f",crop='min(iw,ih)':'min(iw,ih)',scale={pw}:{pw}"
    else:
        chain += f",scale={pw}:-2"
    chroma = o.get("chroma") or "none"
    if chroma != "none":
        col = (o.get("chroma_color") if chroma == "custom" else CHROMA_COLORS.get(chroma)) or "#00FF00"
        sim = max(0.01, min(0.6, _num(o.get("chroma_sim"), 0.15)))
        chain += f",chromakey=0x{col.lstrip('#')}:{sim:.3f}:0.08"
    chain += ",format=yuva420p"
    if shape != "circle":
        return [chain + f"[img{j}]"]
    out = [chain + f"[vc{j}]",
           f"color=c=black:s={pw}x{pw}:d=0.04,format=gray,geq=lum='if(lte(hypot(X-W/2+0.5,Y-H/2+0.5),W/2-1),255,0)'[mask{j}]"]
    if chroma != "none":
        out += [f"[vc{j}]split[vc{j}a][vc{j}b]", f"[vc{j}a]alphaextract[al{j}]",
                f"[al{j}][mask{j}]blend=all_mode=multiply[am{j}]", f"[vc{j}b][am{j}]alphamerge[img{j}]"]
    else:
        out += [f"[vc{j}][mask{j}]alphamerge[img{j}]"]
    return out


def encode_args(p: dict, quality: str = "high") -> list:
    plat = PLATFORMS.get(CANVAS_PRESETS[p["preset"]]["platform"], PLATFORMS["tiktok"])
    if quality == "draft":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-c:a", "aac", "-b:a", "128k"]
    return ["-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-b:v", plat.video_bitrate,
            "-maxrate", plat.maxrate, "-bufsize", plat.bufsize, "-c:a", "aac", "-b:a", plat.audio_bitrate,
            "-ar", "48000", "-ac", "2"]


def _run(cmd: list, cwd: Path, duration: float, progress: Optional[Callable[[float], None]],
         cancel: Optional[threading.Event]) -> None:
    cmd = [str(c) for c in cmd]
    log.debug("RUN editor render: %s", subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    err_lines: list[str] = []

    def read_err():
        for line in proc.stderr:
            err_lines.append(line.rstrip())
            del err_lines[:-40]

    th = threading.Thread(target=read_err, daemon=True)
    th.start()
    # Safety net: stop ffmpeg if it stops making progress or runs far past the end of the video.
    state = {"t": time.time(), "secs": -1.0, "why": ""}

    def watchdog():
        while proc.poll() is None:
            time.sleep(2)
            if time.time() - state["t"] > STALL_SECONDS and proc.poll() is None:
                state["why"] = f"ffmpeg made no progress for {STALL_SECONDS} seconds"
                proc.kill()

    threading.Thread(target=watchdog, daemon=True).start()
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.terminate()
            break
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                secs = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                continue
            if secs > state["secs"]:
                state["t"], state["secs"] = time.time(), secs
            if duration > 0 and secs > duration * 1.5 + 10:
                state["why"] = "ffmpeg kept writing past the end of the video"
                proc.kill()
                break
            if progress and duration > 0:
                progress(max(0.0, min(1.0, secs / duration)))
    proc.wait()
    if state["why"]:
        log.debug("ffmpeg stderr:\n%s", "\n".join(err_lines))
        raise RenderError(f"Rendering was stopped: {state['why']}. Please report this with the log file.")
    th.join(timeout=2)
    if cancel is not None and cancel.is_set():
        raise RenderError("Render cancelled.")
    if proc.returncode != 0:
        log.debug("ffmpeg stderr:\n%s", "\n".join(err_lines))
        tail = "\n".join(line for line in err_lines[-12:] if line.strip())
        raise RenderError(f"ffmpeg failed (exit {proc.returncode}):\n{tail}")


def render_video(p: dict, store: ProjectStore, pid: str, out: Path, work: Path, quality: str = "high",
                 progress: Optional[Callable[[float], None]] = None,
                 cancel: Optional[threading.Event] = None) -> Path:
    plan = build_plan(p, store, pid, work)
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *plan.args,
           *encode_args(p, quality), "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-t", f"{plan.duration:.3f}",
           out]
    _run(cmd, work, plan.duration, progress, cancel)
    return out


def render_frame(p: dict, store: ProjectStore, pid: str, t: float, out: Path, work: Path) -> Path:
    plan = build_plan(p, store, pid, work, audio=False)
    t = max(0.0, min(t, max(0.0, plan.duration - 0.04)))
    out = Path(out).resolve()
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", *plan.args, "-ss", f"{t:.3f}", "-frames:v", "1",
           "-q:v", "3", out]
    _run(cmd, work, 0, None, None)
    if not out.exists():
        raise RenderError("No frame was produced at that time.")
    return out
