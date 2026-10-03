"""Platform presets and the final renderer.

The final render does reframing, avatar overlays, burned-in captions, loudness
normalisation and platform encoding in a single ffmpeg pass, so quality is only
lost once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cliptool import reformat
from cliptool.avatars import TimelineEntry, build_tracks
from cliptool.captions import write_ass
from cliptool.ffmpeg_utils import ffmpeg, probe
from cliptool.logger import get_logger
from cliptool.transcribe import Transcript

log = get_logger("export")


@dataclass(frozen=True)
class Platform:
    key: str
    label: str
    width: int  # 0 = keep source size
    height: int
    video_bitrate: str
    maxrate: str
    bufsize: str
    audio_bitrate: str
    max_duration: Optional[float]
    max_fps: int = 60

    @property
    def vertical(self) -> bool:
        return self.height > self.width


PLATFORMS: dict[str, Platform] = {
    "tiktok": Platform("tiktok", "TikTok", 1080, 1920, "8M", "10M", "16M", "192k", 600),
    "youtube_shorts": Platform("youtube_shorts", "YouTube Shorts", 1080, 1920, "10M", "12M", "20M", "192k", 180),
    "instagram_reels": Platform("instagram_reels", "Instagram Reels", 1080, 1920, "8M", "10M", "16M", "192k", 180),
    "youtube": Platform("youtube", "YouTube (1080p)", 1920, 1080, "12M", "16M", "24M", "256k", None),
    "youtube_4k": Platform("youtube_4k", "YouTube (4K)", 3840, 2160, "45M", "55M", "90M", "320k", None),
    "source": Platform("source", "Same size as source", 0, 0, "", "", "", "192k", None),
}


def get_platform(key: str) -> Platform:
    if key not in PLATFORMS:
        raise ValueError(f"Unknown platform '{key}'. Choose from: {', '.join(PLATFORMS)}")
    return PLATFORMS[key]


@dataclass
class RenderOptions:
    platform: str = "source"
    reformat_mode: str = "smart"
    blur_strength: int = 20
    transcript: Optional[Transcript] = None  # captions are burned in when set
    captions_cfg: Optional[dict] = None
    timeline: Optional[list[TimelineEntry]] = None  # avatars are overlaid when set
    avatars_dir: Optional[Path] = None
    avatars_cfg: Optional[dict] = None
    loudnorm: bool = True
    preset: str = "medium"


def render(src: Path, out: Path, opts: RenderOptions, work_dir: Path) -> Path:
    src, out, work_dir = Path(src).resolve(), Path(out).resolve(), Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    plat = get_platform(opts.platform)
    info = probe(src)
    tw, th = (plat.width, plat.height) if plat.width else (info.width // 2 * 2, info.height // 2 * 2)
    fps = 60 if info.fps > 45 and plat.max_fps >= 60 else 30
    tag = re.sub(r"[^A-Za-z0-9_-]", "_", f"{out.stem}_{plat.key}")  # safe inside filter args

    keys = None
    if opts.reformat_mode == "smart" and (tw, th) != (info.width, info.height):
        keys = reformat.analyze_smart(src, info, tw, th)
    graph = [reformat.build_filter(info, tw, th, opts.reformat_mode, "0:v", "base", keys, opts.blur_strength)]
    cur = "base"
    inputs: list = ["-i", src]

    if opts.timeline and opts.avatars_dir:
        tracks = build_tracks(opts.timeline, info.duration, tw, th, Path(opts.avatars_dir),
                              opts.avatars_cfg or {}, work_dir / f"{tag}_avatars")
        for i, tr in enumerate(tracks, start=1):
            inputs += ["-f", "concat", "-safe", "0", "-i", tr.concat_file.resolve()]
            graph.append(f"[{i}:v]format=rgba[av{i}];"
                         f"[{cur}][av{i}]overlay={tr.x}:{tr.y}:eof_action=pass:format=auto[ov{i}]")
            cur = f"ov{i}"
        if tracks:
            log.info("  + avatar overlay (%d track%s)", len(tracks), "s" if len(tracks) > 1 else "")

    if opts.transcript is not None and opts.transcript.segments:
        ass_name = f"{tag}_captions.ass"
        write_ass(opts.transcript, tw, th, opts.captions_cfg or {}, work_dir / ass_name)
        graph.append(f"[{cur}]ass={ass_name}[cap]")
        cur = "cap"
        log.info("  + captions")

    graph.append(f"[{cur}]fps={fps},format=yuv420p[vout]")
    script = work_dir / f"{tag}.filter"
    script.write_text(";\n".join(graph), encoding="utf-8")

    args = [*inputs, "-filter_complex_script", script.name, "-map", "[vout]"]
    if info.has_audio:
        args += ["-map", "0:a:0"]
        if opts.loudnorm:
            args += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
        args += ["-c:a", "aac", "-b:a", plat.audio_bitrate, "-ar", "48000", "-ac", "2"]
    args += ["-c:v", "libx264", "-preset", opts.preset, "-profile:v", "high"]
    if plat.video_bitrate:
        args += ["-b:v", plat.video_bitrate, "-maxrate", plat.maxrate, "-bufsize", plat.bufsize]
    else:
        args += ["-crf", "18"]
    args += ["-movflags", "+faststart", out]

    log.info("Rendering %s for %s (%dx%d)...", out.name, plat.label, tw, th)
    ffmpeg(args, desc=f"render {out.name}", cwd=work_dir)
    if plat.max_duration and info.duration > plat.max_duration:
        log.warning("  %s is %.0fs long - longer than %s allows (%.0fs).", out.name, info.duration,
                    plat.label, plat.max_duration)
    return out
