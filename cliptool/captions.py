"""TikTok-style captions: short bold word groups with the spoken word highlighted.

Captions are written as an .ass subtitle file which ffmpeg burns into the video.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from cliptool.transcribe import Segment, Transcript, Word

SPEAKER_PALETTE = ["#FFFFFF", "#7CF7FF", "#FFB3F0", "#B6FF8A", "#FFC27A", "#C9B8FF"]


def hex_to_ass(color: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> ASS '&HAABBGGRR'."""
    c = color.lstrip("#")
    if len(c) != 6:
        c = "FFFFFF"
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _inline_color(color: str) -> str:
    c = color.lstrip("#")
    return f"&H{c[4:6]}{c[2:4]}{c[0:2]}&".upper()


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _words_for_segment(seg: Segment) -> list[Word]:
    """Use real word timings; if missing, spread the words evenly over the segment."""
    if seg.words:
        return [w for w in seg.words if w.text.strip()]
    tokens = seg.text.split()
    if not tokens:
        return []
    step = seg.duration / len(tokens)
    return [Word(seg.start + i * step, seg.start + (i + 1) * step, " " + t) for i, t in enumerate(tokens)]


def group_words(words: list[Word], max_words: int = 3, max_gap: float = 0.6,
                max_chars: int = 22) -> list[list[Word]]:
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        text = w.text.strip()
        chars = sum(len(x.text.strip()) + 1 for x in cur) + len(text)
        if cur and (len(cur) >= max_words or w.start - cur[-1].end > max_gap or chars > max_chars):
            groups.append(cur)
            cur = []
        cur.append(w)
        if text[-1:] in ".?!,;:":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def build_ass(transcript: Transcript, width: int, height: int, cfg: dict,
              speaker_colors: Optional[dict[str, str]] = None) -> str:
    vertical = height > width
    base = min(width, height)
    font_size = cfg.get("font_size") or int(base * (0.085 if vertical else 0.065))
    outline = cfg.get("outline") or max(2, round(font_size * 0.09))
    position = cfg.get("position", "lower")
    alignment = {"top": 8, "middle": 5}.get(position, 2)
    margin_v = {"bottom": int(height * 0.08), "lower": int(height * (0.22 if vertical else 0.10)),
                "middle": 0, "top": int(height * 0.10)}.get(position, int(height * 0.2))
    primary = cfg.get("primary_color", "#FFFFFF")
    highlight = cfg.get("highlight_color", "#FFE000")
    upper = cfg.get("uppercase", True)
    pop = cfg.get("pop", True)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cfg.get('font', 'Arial')},{font_size},{hex_to_ass(primary)},{hex_to_ass(highlight)},{hex_to_ass(cfg.get('outline_color', '#000000'))},{hex_to_ass('#000000', 0x80)},-1,0,0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},{alignment},{int(width * 0.06)},{int(width * 0.06)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for seg in transcript.segments:
        seg_color = primary
        if speaker_colors and seg.speaker in speaker_colors:
            seg_color = speaker_colors[seg.speaker]
        words = _words_for_segment(seg)
        for group in group_words(words, int(cfg.get("max_words", 3))):
            texts = [ass_escape(w.text.strip().upper() if upper else w.text.strip()) for w in group]
            for i, w in enumerate(group):
                start = w.start
                end = group[i + 1].start if i + 1 < len(group) else max(w.end, w.start + 0.1)
                if end - start < 0.02:
                    continue
                pieces = []
                for j, t in enumerate(texts):
                    if j == i:
                        scale = r"\fscx112\fscy112" if pop else ""
                        pieces.append(f"{{\\c{_inline_color(highlight)}{scale}}}{t}"
                                      f"{{\\c{_inline_color(seg_color)}\\fscx100\\fscy100}}")
                    else:
                        pieces.append(t)
                line = f"{{\\c{_inline_color(seg_color)}}}" + " ".join(pieces)
                events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{line}")
    return header + "\n".join(events) + "\n"


def speaker_color_map(transcript: Transcript) -> dict[str, str]:
    return {spk: SPEAKER_PALETTE[i % len(SPEAKER_PALETTE)] for i, spk in enumerate(transcript.speakers())}


def write_ass(transcript: Transcript, width: int, height: int, cfg: dict, out: Path) -> Path:
    colors = speaker_color_map(transcript) if cfg.get("speaker_colors") else None
    out.write_text(build_ass(transcript, width, height, cfg, colors), encoding="utf-8")
    return out
