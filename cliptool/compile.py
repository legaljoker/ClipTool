"""Combining videos: best-of compilations and long videos made from many shorts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cliptool import reformat
from cliptool.clipfinder import Clip
from cliptool.ffmpeg_utils import INTERMEDIATE_AUDIO, INTERMEDIATE_VIDEO, ffmpeg, fmt_time, probe
from cliptool.logger import get_logger

log = get_logger("compile")


def normalize(src: Path, out: Path, width: int, height: int, mode: str = "blur", fps: int = 30,
              start: float | None = None, end: float | None = None) -> Path:
    """Re-encode `src` (optionally only start..end) to a fixed canvas/fps/audio format for joining."""
    info = probe(src)
    keys = reformat.analyze_smart(src, info, width, height) if mode == "smart" else None
    if keys and start:  # keyframes are in source time; the trimmed stream starts at 0
        keys = [(max(0.0, t - start), x) for t, x in keys]
    chain = reformat.build_filter(info, width, height, mode, "0:v", "v0", keys)
    graph = f"{chain};[v0]fps={fps},format=yuv420p[v]"
    args: list = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", src]
    if not info.has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    if end is not None:
        args += ["-t", f"{end - (start or 0):.3f}"]
    args += ["-filter_complex", graph, "-map", "[v]", "-map", "0:a:0" if info.has_audio else "1:a:0",
             *INTERMEDIATE_VIDEO, *INTERMEDIATE_AUDIO, "-shortest", "-movflags", "+faststart", out]
    ffmpeg(args, desc=f"normalize {Path(src).name}")
    return out


def concat(files: list[Path], out: Path, work_dir: Path) -> Path:
    """Join files that share the same format (as made by normalize)."""
    lst = work_dir / f"{out.stem}_concat.txt"
    lst.write_text("\n".join(f"file '{Path(f).resolve().as_posix()}'" for f in files) + "\n", encoding="utf-8")
    ffmpeg(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out],
           desc=f"join {len(files)} videos")
    return out


@dataclass
class Moment:
    source: Path  # file the moment is cut from
    clip: Clip
    out_offset: float = 0.0  # where it lands in the compilation


def select_bestof(per_source: dict[Path, list[Clip]], target: float, max_per_source: int,
                  keep_source_order: bool = True) -> list[Moment]:
    """Pick the strongest moments across all sources until the target length is reached."""
    pool: list[Moment] = []
    for src, clips in per_source.items():
        for c in sorted(clips, key=lambda c: -c.score)[:max_per_source]:
            pool.append(Moment(src, c))
    pool.sort(key=lambda m: -m.clip.score)
    chosen: list[Moment] = []
    total = 0.0
    for m in pool:
        if total + m.clip.duration > target + 2:
            continue
        chosen.append(m)
        total += m.clip.duration
        if total >= target - 3:
            break
    if not chosen and pool:  # every moment longer than target: take best one, trimmed
        m = pool[0]
        m.clip.end = m.clip.start + target
        chosen = [m]
    if keep_source_order:
        order = {src: i for i, src in enumerate(per_source)}
        chosen.sort(key=lambda m: (order[m.source], m.clip.start))
    offset = 0.0
    for m in chosen:
        m.out_offset = offset
        offset += m.clip.duration
    return chosen


def chapters_text(entries: list[tuple[float, str]]) -> str:
    """YouTube chapter list. YouTube needs the first chapter at 0:00 and >= 10s chapters."""
    lines = []
    last = -999.0
    for t, title in entries:
        if lines and t - last < 10:
            continue
        stamp = fmt_time(0 if not lines else t)
        lines.append(f"{stamp} {title}")
        last = t
    return "\n".join(lines)
