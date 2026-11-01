"""Text styles, text animations and motion-title templates.

Everything here is mirrored in static/js/textfx.js so the live preview and
the final render agree (tests/test_editor.py checks the title layouts match).
Text is drawn by libass (ffmpeg's subtitle renderer) using the fonts bundled
in static/fonts.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from cliptool.captions import _inline_color, ass_escape, ass_time

FONTS_DIR = Path(__file__).parent / "static" / "fonts"

# family name (as libass / the browser see it) -> {weight: file}
FONT_FILES: dict[str, dict[int, str]] = {
    "Montserrat": {400: "Montserrat_400Regular.ttf", 700: "Montserrat_700Bold.ttf"},
    "Montserrat Black": {400: "Montserrat_900Black.ttf"},
    "Montserrat Light": {400: "Montserrat_300Light.ttf"},
    "Anton": {400: "Anton_400Regular.ttf"},
    "Bangers": {400: "Bangers_400Regular.ttf"},
    "Great Vibes": {400: "GreatVibes_400Regular.ttf"},
    "Press Start 2P": {400: "PressStart2P_400Regular.ttf"},
    "Share Tech Mono": {400: "ShareTechMono_400Regular.ttf"},
}
SYSTEM_FONTS = ["Arial", "Impact", "Verdana", "Georgia", "Comic Sans MS", "Segoe UI", "Times New Roman"]

# Text looks. outline_width / shadow are in px for a 1080-wide frame.
TEXT_STYLES: dict[str, dict] = {
    "plain": {"label": "Plain", "font": "Montserrat", "color": "#FFFFFF", "outline": "#000000", "outline_width": 2,
              "shadow": 2},
    "bold": {"label": "Bold", "font": "Montserrat Black", "color": "#FFFFFF", "outline": "#000000",
             "outline_width": 6, "shadow": 3},
    "clipped": {"label": "Clipped", "font": "Bangers", "italic": True, "uppercase": True, "color": "#C8FF3A",
                "outline": "#142000", "outline_width": 5, "shadow": 4, "size_scale": 1.15},
    "highlighter": {"label": "Highlighter", "font": "Montserrat", "bold": True, "color": "#111111",
                    "box": "#D7F23A", "outline_width": 14},
    "hackerman": {"label": "Hackerman", "font": "Share Tech Mono", "color": "#3BFF6B", "glow": "#00FF55",
                  "outline_width": 3},
    "intuition": {"label": "Intuition", "font": "Montserrat Light", "color": "#FFFFFF", "outline_width": 0,
                  "shadow": 2},
    "meme": {"label": "Meme", "font": "Anton", "uppercase": True, "color": "#FFFFFF", "outline": "#000000",
             "outline_width": 7},
    "outline": {"label": "Outline", "font": "Montserrat Black", "color": "#FFFFFF", "hollow": True,
                "outline_width": 3},
    "pixel": {"label": "Pixel", "font": "Press Start 2P", "color": "#FFFFFF", "outline": "#000000",
              "outline_width": 5, "size_scale": 0.7},
    "signature": {"label": "Signature", "font": "Great Vibes", "color": "#FFFFFF", "outline_width": 0,
                  "shadow": 3, "size_scale": 1.35},
    "neon": {"label": "Neon", "font": "Montserrat", "bold": True, "color": "#FFFFFF", "glow": "#FF2BD6",
             "outline_width": 4},
}

ANIM_IN = {"none": "None", "fade": "Fade", "pop": "Pop", "slide_up": "Slide up", "slide_down": "Slide down",
           "slide_left": "Slide in from right", "slide_right": "Slide in from left", "zoom": "Zoom",
           "blur": "Blur in", "typewriter": "Typewriter"}
ANIM_OUT = {"none": "None", "fade": "Fade", "pop": "Pop", "slide_up": "Slide up", "slide_down": "Slide down",
            "slide_left": "Slide out left", "slide_right": "Slide out right", "zoom": "Zoom", "blur": "Blur out"}

TITLE_TEMPLATES: dict[str, dict] = {
    "bar": {"label": "Bar", "accent": "#FFFFFF", "color": "#000000"},
    "slant": {"label": "Slanted bar", "accent": "#FFFFFF", "color": "#000000"},
    "box": {"label": "Accent box", "accent": "#2BE02B", "color": "#FFFFFF"},
    "circle": {"label": "Accent circle", "accent": "#2BE02B", "color": "#FFFFFF"},
    "underline": {"label": "Underline", "accent": "#FFFFFF", "color": "#FFFFFF"},
    "line": {"label": "Line in", "accent": "#FFFFFF", "color": "#FFFFFF"},
}
TITLE_FONT, SUB_FONT = "Montserrat Black", "Montserrat"
INTRO = 0.4  # seconds each title part takes to animate in
OUTRO = 0.35


def meta() -> dict:
    return {"text_styles": TEXT_STYLES, "anim_in": ANIM_IN, "anim_out": ANIM_OUT, "title_templates": TITLE_TEMPLATES,
            "fonts": list(FONT_FILES) + SYSTEM_FONTS, "bundled_fonts": FONT_FILES}


# ---------------------------------------------------------------------------
# Measuring text (same fonts as the browser uses)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _pil_font(family: str, bold: bool, size: int):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    files = FONT_FILES.get(family)
    if not files:
        return None
    f = files.get(700 if bold else 400) or next(iter(files.values()))
    try:
        return ImageFont.truetype(str(FONTS_DIR / f), size)
    except OSError:
        return None


def measure(text: str, family: str, size: float, bold: bool = False) -> float:
    font = _pil_font(family, bold, max(1, int(round(size))))
    if font is None:
        return len(text) * size * 0.62
    return float(font.getlength(text))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _n(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _clamp(v, a, b):
    return max(a, min(b, v))


def text_px(o: dict, W: int, H: int) -> float:
    st = TEXT_STYLES.get(o.get("style") or "bold", TEXT_STYLES["bold"])
    return _n(o.get("size"), 70) * min(W, H) / 1080 * st.get("size_scale", 1.0)


def style_tags(style_id: str, o: dict, size_px: float, W: int, H: int) -> tuple[str, str]:
    """(ASS style name, override tags) for a text style."""
    st = TEXT_STYLES.get(style_id, TEXT_STYLES["bold"])
    k = min(W, H) / 1080
    color = o.get("color") or st["color"]
    w = _n(o.get("outline_width"), st.get("outline_width", 0)) * k
    font = (o.get("font") or st["font"]).replace("{", "").replace("}", "").replace("\\", "")
    tags = (f"\\fn{font}\\fs{size_px:.0f}\\b{1 if st.get('bold') else 0}\\i{1 if st.get('italic') else 0}"
            f"\\1c{_inline_color(color)}")
    style = "Txt"
    if st.get("hollow"):
        tags += f"\\1a&HFF&\\3c{_inline_color(color)}\\bord{max(1, w):.1f}"
    elif st.get("box"):
        style = "Box"
        tags += f"\\3c{_inline_color(o.get('accent') or st['box'])}\\bord{w:.1f}"
    elif st.get("glow"):
        tags += f"\\3c{_inline_color(o.get('accent') or st['glow'])}\\bord{w:.1f}\\blur{max(2, w * 2):.1f}"
    else:
        tags += f"\\3c{_inline_color(o.get('outline') or st.get('outline', '#000000'))}\\bord{w:.1f}"
    shad = _n(st.get("shadow"), 0) * k
    tags += f"\\shad{shad:.1f}\\4c&H000000&\\4a&H80&" if shad and not st.get("box") else "\\shad0"
    return style, tags


def display_text(o: dict) -> str:
    st = TEXT_STYLES.get(o.get("style") or "bold", TEXT_STYLES["bold"])
    t = str(o.get("text") or "")
    return t.upper() if st.get("uppercase") else t


# ---------------------------------------------------------------------------
# Text animations -> ASS (intro / hold / outro events)
# ---------------------------------------------------------------------------

def slide_offset(kind: str, W: int, H: int) -> tuple[float, float]:
    """Where a slide starts (intro) relative to the resting position."""
    dv, dh = 0.18 * H, 0.3 * W
    return {"slide_up": (0, dv), "slide_down": (0, -dv), "slide_left": (dh, 0), "slide_right": (-dh, 0)}.get(kind, (0, 0))


def exit_offset(kind: str, W: int, H: int) -> tuple[float, float]:
    dv, dh = 0.18 * H, 0.3 * W
    return {"slide_up": (0, -dv), "slide_down": (0, dv), "slide_left": (-dh, 0), "slide_right": (dh, 0)}.get(kind, (0, 0))


def _anim_tags(kind: str, X: float, Y: float, d_ms: int, intro: bool, W: int, H: int) -> tuple[str, Optional[str]]:
    """(tags, position-tag) for one intro/outro segment."""
    pos = f"\\pos({X:.0f},{Y:.0f})"
    if kind == "fade":
        return (f"\\fad({d_ms},0)" if intro else f"\\fad(0,{d_ms})"), pos
    if kind == "pop":
        if intro:
            a = int(d_ms * 0.7)
            return f"\\fscx0\\fscy0\\t(0,{a},\\fscx110\\fscy110)\\t({a},{d_ms},\\fscx100\\fscy100)", pos
        a = int(d_ms * 0.3)
        return f"\\t(0,{a},\\fscx110\\fscy110)\\t({a},{d_ms},\\fscx0\\fscy0)", pos
    if kind.startswith("slide_"):
        if intro:
            dx, dy = slide_offset(kind, W, H)
            return f"\\fad({d_ms},0)", f"\\move({X + dx:.0f},{Y + dy:.0f},{X:.0f},{Y:.0f},0,{d_ms})"
        dx, dy = exit_offset(kind, W, H)
        return f"\\fad(0,{d_ms})", f"\\move({X:.0f},{Y:.0f},{X + dx:.0f},{Y + dy:.0f},0,{d_ms})"
    if kind == "zoom":
        if intro:
            return f"\\fscx250\\fscy250\\t(0,{d_ms},\\fscx100\\fscy100)\\fad({d_ms},0)", pos
        return f"\\t(0,{d_ms},\\fscx250\\fscy250)\\fad(0,{d_ms})", pos
    if kind == "blur":
        if intro:
            return f"\\blur20\\t(0,{d_ms},\\blur0)\\fad({d_ms},0)", pos
        return f"\\t(0,{d_ms},\\blur20)\\fad(0,{d_ms})", pos
    return "", pos


def text_events(o: dict, W: int, H: int) -> list[str]:
    """ASS Dialogue lines for a styled, animated text overlay."""
    text = display_text(o).strip()
    if not text:
        return []
    a, b = _n(o.get("start")), _n(o.get("end"))
    if b - a < 0.05:
        return []
    X, Y = _n(o.get("x"), 0.5) * W, _n(o.get("y"), 0.2) * H
    size = text_px(o, W, H)
    style, base = style_tags(o.get("style") or "bold", o, size, W, H)
    lines = [ass_escape(line) for line in text.splitlines()]
    body = "\\N".join(lines)
    dur = _clamp(_n(o.get("anim_dur"), 0.4), 0.05, 3.0)
    ain, aout = o.get("anim_in") or "none", o.get("anim_out") or "none"
    d_in = min(dur, (b - a) / 2) if ain != "none" else 0.0
    d_out = min(dur, (b - a) / 2) if aout != "none" else 0.0
    ev = []

    def line(s, e, tags, txt=body):
        if e - s >= 0.005:
            ev.append(f"Dialogue: 2,{ass_time(s)},{ass_time(e)},{style},,0,0,0,,{{\\q2\\an5{base}{tags}}}{txt}")

    if d_in:
        if ain == "typewriter":
            n = len(text.replace("\n", ""))
            step = d_in / max(1, n)
            flat = text
            for k in range(1, n + 1):
                # keep layout fixed: hidden chars stay, just invisible
                shown, hidden = _split_visible(flat, k)
                txt = ass_escape(shown).replace("\n", "\\N") + "{\\alpha&HFF&}" + ass_escape(hidden).replace("\n", "\\N")
                line(a + (k - 1) * step, a + k * step if k < n else a + d_in, f"\\pos({X:.0f},{Y:.0f})", txt)
        else:
            tags, pos = _anim_tags(ain, X, Y, int(d_in * 1000), True, W, H)
            line(a, a + d_in, pos + tags)
    line(a + d_in, b - d_out, f"\\pos({X:.0f},{Y:.0f})")
    if d_out:
        tags, pos = _anim_tags(aout, X, Y, int(d_out * 1000), False, W, H)
        line(b - d_out, b, pos + tags)
    return ev


def _split_visible(text: str, k: int) -> tuple[str, str]:
    count, i = 0, 0
    while i < len(text) and count < k:
        if text[i] != "\n":
            count += 1
        i += 1
    return text[:i], text[i:]


# ---------------------------------------------------------------------------
# Motion titles (title + subtitle with animated shapes)
# ---------------------------------------------------------------------------

def title_layout(o: dict, W: int, H: int, measure_fn: Callable[[str, str, float, bool], float] = measure) -> dict:
    """Parts of a motion title in canvas pixels. Mirrored by titleLayout() in textfx.js."""
    tpl_id = o.get("template") if o.get("template") in TITLE_TEMPLATES else "bar"
    tpl = TITLE_TEMPLATES[tpl_id]
    s = round(_n(o.get("size"), 70) * min(W, H) / 1080)
    ss = round(s * 0.38)
    title = str(o.get("text") or "").upper()
    sub = str(o.get("subtitle") or "").upper()
    tw = measure_fn(title, TITLE_FONT, s, False)
    sw = measure_fn(sub, SUB_FONT, ss, True) if sub else 0.0
    accent = o.get("accent") or tpl["accent"]
    color = o.get("color") or tpl["color"]
    white = "#FFFFFF"
    parts: list[dict] = []
    rot = 0.0

    def T(text, font, size, bold, x, y, col, anim, delay, align="center"):
        parts.append({"kind": "text", "text": text, "font": font, "size": size, "bold": bold, "x": x, "y": y,
                      "color": col, "anim": anim, "delay": delay, "align": align})

    if tpl_id in ("bar", "slant"):
        pad = 0.35 * s
        bw, bh = tw + 2 * pad, 1.25 * s
        gw = max(bw, sw)
        gh = bh + (0.25 * s + ss if sub else 0)
        bx = (gw - bw) / 2
        parts.append({"kind": "rect", "x": bx, "y": 0, "w": bw, "h": bh, "color": accent, "anim": "wipe", "delay": 0})
        T(title, TITLE_FONT, s, False, gw / 2, bh / 2, color, "fade", 0.15)
        if sub:
            T(sub, SUB_FONT, ss, True, gw / 2, bh + 0.25 * s + ss / 2, white, "slide_up", 0.3)
        if tpl_id == "slant":
            rot = -5.0
    elif tpl_id in ("box", "circle"):
        block = (ss + 0.12 * s + s) if sub else 1.1 * s
        tx = block + 0.3 * s
        gw, gh = tx + max(tw, sw), block
        if tpl_id == "box":
            parts.append({"kind": "rect", "x": 0, "y": 0, "w": block, "h": block, "color": accent, "anim": "pop",
                          "delay": 0})
        else:
            parts.append({"kind": "circle", "x": block / 2, "y": block / 2, "r": block / 2, "color": accent,
                          "anim": "pop", "delay": 0})
        if sub:
            T(sub, SUB_FONT, ss, True, tx, ss / 2, white, "slide_left", 0.15, "left")
            T(title, TITLE_FONT, s, False, tx, ss + 0.12 * s + s / 2, color, "slide_left", 0.25, "left")
        else:
            T(title, TITLE_FONT, s, False, tx, block / 2, color, "slide_left", 0.15, "left")
    elif tpl_id == "underline":
        gw = max(tw, sw)
        lh = max(3.0, 0.07 * s)
        ly = 1.08 * s
        gh = ly + lh + (0.25 * s + ss if sub else 0)
        T(title, TITLE_FONT, s, False, gw / 2, s / 2, color, "fade", 0.1)
        parts.append({"kind": "rect", "x": 0, "y": ly, "w": gw, "h": lh, "color": accent, "anim": "wipe", "delay": 0})
        if sub:
            T(sub, SUB_FONT, ss, True, gw / 2, ly + lh + 0.25 * s + ss / 2, white, "slide_up", 0.3)
    else:  # line
        L = 0.22 * W
        lh = max(3.0, 0.05 * s)
        tx = L + 0.25 * s
        gw = tx + max(tw, sw)
        gh = s + (0.15 * s + ss if sub else 0)
        parts.append({"kind": "rect", "x": 0, "y": s / 2 - lh / 2, "w": L, "h": lh, "color": accent, "anim": "wipe",
                      "delay": 0})
        T(title, TITLE_FONT, s, False, tx, s / 2, color, "slide_left", 0.15, "left")
        if sub:
            T(sub, SUB_FONT, ss, True, tx, s + 0.15 * s + ss / 2, white, "fade", 0.3, "left")
    X, Y = _n(o.get("x"), 0.5) * W, _n(o.get("y"), 0.3) * H
    return {"x0": X - gw / 2, "y0": Y - gh / 2, "w": gw, "h": gh, "rot": rot, "cx": X, "cy": Y, "parts": parts,
            "size": s}


def _circle_path(r: float) -> str:
    k = 0.5523 * r
    return (f"m {r:.1f} 0 b {r + k:.1f} 0 {2 * r:.1f} {r - k:.1f} {2 * r:.1f} {r:.1f} "
            f"b {2 * r:.1f} {r + k:.1f} {r + k:.1f} {2 * r:.1f} {r:.1f} {2 * r:.1f} "
            f"b {r - k:.1f} {2 * r:.1f} 0 {r + k:.1f} 0 {r:.1f} b 0 {r - k:.1f} {r - k:.1f} 0 {r:.1f} 0")


def title_events(o: dict, W: int, H: int) -> list[str]:
    a, b = _n(o.get("start")), _n(o.get("end"))
    if b - a < 0.1 or not str(o.get("text") or "").strip():
        return []
    lay = title_layout(o, W, H)
    ev = []
    rot = f"\\frz{-lay['rot']:.2f}\\org({lay['cx']:.0f},{lay['cy']:.0f})" if lay["rot"] else ""
    d_ms = int(INTRO * 1000)
    out_ms = int(min(OUTRO, (b - a) / 3) * 1000)
    for part in lay["parts"]:
        s = min(b - 0.05, a + part["delay"])
        x, y = lay["x0"] + part["x"], lay["y0"] + part["y"]
        anim = part["anim"]
        if part["kind"] in ("rect", "circle"):
            col = _inline_color(part["color"])
            if part["kind"] == "rect":
                w, h = part["w"], part["h"]
                shape = f"m 0 0 l {w:.1f} 0 {w:.1f} {h:.1f} 0 {h:.1f}"
                cx, cy = x + w / 2, y + h / 2
            else:
                r = part["r"]
                shape = _circle_path(r)
                cx, cy = x, y
                x, y = x - r, y - r
            if anim == "wipe":
                tags = f"\\an7\\pos({x:.1f},{y:.1f})\\fscx0\\t(0,{d_ms},\\fscx100)\\fad(0,{out_ms})"
            else:  # pop
                a1 = int(d_ms * 0.7)
                tags = (f"\\an5\\pos({cx:.1f},{cy:.1f})\\fscx0\\fscy0\\t(0,{a1},\\fscx110\\fscy110)"
                        f"\\t({a1},{d_ms},\\fscx100\\fscy100)\\fad(0,{out_ms})")
            ev.append(f"Dialogue: 2,{ass_time(s)},{ass_time(b)},Shape,,0,0,0,,"
                      f"{{{tags}{rot}\\bord0\\shad0\\1c{col}\\p1}}{shape}{{\\p0}}")
        else:
            an = 4 if part["align"] == "left" else 5
            fade_in = d_ms if anim in ("fade", "slide_up", "slide_left") else 0
            if anim == "slide_up":
                posm = f"\\move({x:.1f},{y + 0.6 * part['size']:.1f},{x:.1f},{y:.1f},0,{d_ms})"
            elif anim == "slide_left":
                posm = f"\\move({x + 0.8 * part['size']:.1f},{y:.1f},{x:.1f},{y:.1f},0,{d_ms})"
            else:
                posm = f"\\pos({x:.1f},{y:.1f})"
            tags = (f"\\q2\\an{an}{posm}\\fn{part['font']}\\fs{part['size']:.0f}\\b{1 if part['bold'] else 0}"
                    f"\\1c{_inline_color(part['color'])}\\bord0\\shad0\\fad({fade_in},{out_ms})")
            ev.append(f"Dialogue: 3,{ass_time(s)},{ass_time(b)},Txt,,0,0,0,,{{{tags}{rot}}}{ass_escape(part['text'])}")
    return ev


def font_files() -> list[Path]:
    return sorted(FONTS_DIR.glob("*.ttf"))
