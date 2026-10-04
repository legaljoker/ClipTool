"""Auto silence-cutter: removes dead air from raw recordings."""
from __future__ import annotations

import re
import shutil
from bisect import bisect_right
from pathlib import Path
from typing import Optional

from cliptool.ffmpeg_utils import (INTERMEDIATE_AUDIO, INTERMEDIATE_VIDEO, ffmpeg, filter_script_args,
                                   probe, run)
from cliptool.logger import get_logger
from cliptool.transcribe import Segment, Transcript, Word

log = get_logger("silence")

Range = tuple[float, float]

_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


def parse_silencedetect(stderr: str, duration: float) -> list[Range]:
    silences: list[Range] = []
    start: Optional[float] = None
    for line in stderr.splitlines():
        m = _START_RE.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _END_RE.search(line)
        if m and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    if start is not None:  # file ends while silent
        silences.append((start, duration))
    return silences


def detect_silences(path: Path, threshold_db: float = -35, min_silence: float = 0.6) -> list[Range]:
    info = probe(path)
    if not info.has_audio:
        return []
    proc = ffmpeg(["-i", path, "-vn", "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence}",
                   "-f", "null", "-"], desc="detect silence")
    return parse_silencedetect(proc.stderr, info.duration)


def keep_ranges(silences: list[Range], duration: float, padding: float = 0.15,
                min_keep: float = 0.25) -> list[Range]:
    """Turn a list of silent ranges into the list of ranges to keep."""
    keep: list[Range] = []
    cursor = 0.0
    for s, e in sorted(silences):
        cut_start, cut_end = s + padding, e - padding
        if cut_end - cut_start <= 0.05:
            continue
        if cut_start > cursor:
            keep.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < duration:
        keep.append((cursor, duration))
    # merge tiny gaps / drop tiny pieces
    merged: list[Range] = []
    for s, e in keep:
        if merged and s - merged[-1][1] < 0.05:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(round(s, 3), round(e, 3)) for s, e in merged if e - s >= min_keep] or [(0.0, duration)]


def original_to_cut(t: float, keep: list[Range]) -> Optional[float]:
    """Position of original time `t` in the cut video (None if it was removed)."""
    acc = 0.0
    for s, e in keep:
        if t < s:
            return None
        if t <= e:
            return acc + (t - s)
        acc += e - s
    return None


def cut_to_original(t: float, keep: list[Range]) -> float:
    """Position of cut-video time `t` in the original recording."""
    acc = 0.0
    for s, e in keep:
        length = e - s
        if t <= acc + length:
            return s + (t - acc)
        acc += length
    return keep[-1][1] if keep else t


def remap_transcript(tr: Transcript, keep: list[Range]) -> Transcript:
    """Re-time a transcript of the original so it matches the silence-cut video."""
    starts = [s for s, _ in keep]
    offsets, acc = [], 0.0
    for s, e in keep:
        offsets.append(acc)
        acc += e - s

    def clamp(t: float) -> Optional[float]:
        i = bisect_right(starts, t) - 1
        if i < 0:
            return offsets[0] if offsets else None
        s, e = keep[i]
        return offsets[i] + (min(t, e) - s)

    segs = []
    for seg in tr.segments:
        words = []
        for w in seg.words:
            mid = (w.start + w.end) / 2
            if original_to_cut(mid, keep) is None and original_to_cut(w.start, keep) is None \
                    and original_to_cut(w.end, keep) is None:
                continue
            ws, we = clamp(w.start), clamp(w.end)
            if ws is None or we is None:
                continue
            words.append(Word(ws, max(we, ws + 0.02), w.text, w.prob))
        if seg.words and not words:
            continue
        s, e = clamp(seg.start), clamp(seg.end)
        if s is None or e is None:
            continue
        if words:
            s, e = min(s, words[0].start), max(e, words[-1].end)
        segs.append(Segment(s, max(e, s + 0.05), seg.text if not seg.words else
                            "".join(w.text for w in words).strip(), words, seg.speaker, seg.emotion))
    return Transcript(segs, tr.language, tr.source)


def _render_keep(src: Path, out: Path, keep: list[Range], has_audio: bool, work_dir: Path) -> None:
    parts = []
    labels = []
    for i, (s, e) in enumerate(keep):
        parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")
            labels.append(f"[a{i}]")
    parts.append("".join(labels) + f"concat=n={len(keep)}:v=1:a={1 if has_audio else 0}"
                 + ("[v][a]" if has_audio else "[v]"))
    script = work_dir / f"{src.stem}_silencecut.filter"
    script.write_text(";\n".join(parts), encoding="utf-8")
    maps = ["-map", "[v]"] + (["-map", "[a]"] if has_audio else [])
    ffmpeg(["-i", src, *filter_script_args(script), *maps, *INTERMEDIATE_VIDEO,
            *(INTERMEDIATE_AUDIO if has_audio else []), "-movflags", "+faststart", out],
           desc=f"remove silence from {src.name}")


def cut_silence(src: Path, out: Path, cfg: dict, work_dir: Path) -> tuple[Path, list[Range], dict]:
    """Remove dead air. Returns (output file, kept ranges, stats)."""
    info = probe(src)
    silences = detect_silences(src, cfg.get("threshold_db", -35), cfg.get("min_silence", 0.6))
    keep = keep_ranges(silences, info.duration, cfg.get("padding", 0.15))
    kept = sum(e - s for s, e in keep)
    stats = {"original": info.duration, "kept": kept, "removed": max(0.0, info.duration - kept),
             "cuts": max(0, len(keep) - 1), "exact_map": True}
    log.info("Silence cut %s: removing %.1fs of dead air in %d places (%.1fs -> %.1fs)",
             src.name, stats["removed"], stats["cuts"], info.duration, kept)
    if stats["cuts"] == 0 and keep[0][0] < 0.05:
        shutil.copyfile(src, out)
        return out, keep, stats

    if cfg.get("use_auto_editor") and shutil.which("auto-editor"):
        margin = cfg.get("padding", 0.15)
        run(["auto-editor", str(src), "--margin", f"{margin}s", "--edit",
             f"audio:threshold={cfg.get('threshold_db', -35)}dB", "--no-open", "-o", str(out)],
            desc="auto-editor")
        # auto-editor picks its own cut points, so our time map is only an estimate;
        # the pipeline re-transcribes the result instead of re-timing the transcript.
        stats["exact_map"] = False
        stats["kept"] = probe(out).duration
        stats["removed"] = max(0.0, info.duration - stats["kept"])
        return out, keep, stats

    _render_keep(src, out, keep, info.has_audio, work_dir)
    return out, keep, stats
