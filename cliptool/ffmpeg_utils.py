"""Thin helpers around the ffmpeg / ffprobe command line tools."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from cliptool.logger import get_logger

log = get_logger("ffmpeg")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".mts", ".ts"}


class FFmpegError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegError(
            "ffmpeg was not found. Install it first:\n"
            "  Windows: winget install Gyan.FFmpeg   (then reopen the terminal)\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg"
        )
    return path


def ffprobe_bin() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise FFmpegError("ffprobe was not found (it ships with ffmpeg - reinstall ffmpeg).")
    return path


def run(cmd: Sequence[str], desc: str = "", cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    log.debug("RUN %s: %s", desc, subprocess.list2cmdline(cmd))
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        log.debug("ffmpeg stderr:\n%s", proc.stderr)
        raise FFmpegError(f"{desc or 'ffmpeg'} failed (exit {proc.returncode}):\n{tail}")
    return proc


def ffmpeg(args: Sequence[str], desc: str = "", cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", *args], desc=desc, cwd=cwd)


_script_option: Optional[str] = None


def filter_script_args(script: Path) -> list[str]:
    """Arguments that load a filter graph from a file.

    ffmpeg 7+ uses `-/filter_complex FILE`; ffmpeg 9 removed the older
    `-filter_complex_script FILE`, and ffmpeg 6 and older only know that one.
    The installed ffmpeg is tested once to see which it accepts.
    """
    global _script_option
    if _script_option is None:
        with tempfile.TemporaryDirectory() as tmp:
            test = Path(tmp) / "test.filter"
            test.write_text("[0:v]null[v]", encoding="utf-8")
            proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error",
                                   "-f", "lavfi", "-i", "nullsrc=s=16x16:d=0.1", "-/filter_complex", str(test),
                                   "-map", "[v]", "-f", "null", "-"], capture_output=True)
        _script_option = "-/filter_complex" if proc.returncode == 0 else "-filter_complex_script"
        log.debug("ffmpeg filter script option: %s", _script_option)
    return [_script_option, str(script)]


def decode_audio(path: Path, sample_rate: int = 16000) -> np.ndarray:
    """Decode any audio/video file to mono float32 samples using ffmpeg."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
           "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"]
    log.debug("RUN decode audio: %s", subprocess.list2cmdline(cmd))
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise FFmpegError(f"Could not read the audio of {Path(path).name}:\n" + "\n".join(err))
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


@dataclass
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    has_video: bool

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width


def _parse_rate(rate: str) -> float:
    try:
        if "/" in rate:
            num, den = rate.split("/")
            return float(num) / float(den) if float(den) else 0.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path) -> MediaInfo:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    proc = run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                str(path)], desc=f"probe {path.name}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    vstreams = [s for s in streams if s.get("codec_type") == "video"
                and s.get("disposition", {}).get("attached_pic", 0) == 0]
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    width = height = 0
    fps = 0.0
    if vstreams:
        v = vstreams[0]
        width, height = int(v.get("width", 0)), int(v.get("height", 0))
        rotation = 0
        for side in v.get("side_data_list", []) or []:
            if "rotation" in side:
                rotation = int(side["rotation"])
        rotation = int(v.get("tags", {}).get("rotate", rotation))
        if abs(rotation) % 180 == 90:  # phone videos are often stored sideways
            width, height = height, width
        fps = _parse_rate(v.get("avg_frame_rate", "0/0")) or _parse_rate(v.get("r_frame_rate", "0/0"))
    duration = float(data.get("format", {}).get("duration") or 0.0)
    if not duration and vstreams:
        duration = float(vstreams[0].get("duration") or 0.0)
    return MediaInfo(path=path, duration=duration, width=width, height=height,
                     fps=fps if 1 <= fps <= 240 else 30.0, has_audio=bool(astreams),
                     has_video=bool(vstreams))


def has_filter(name: str) -> bool:
    try:
        proc = run([ffmpeg_bin(), "-hide_banner", "-filters"], desc="list filters")
    except FFmpegError:
        return False
    return any(line.split()[1:2] == [name] for line in proc.stdout.splitlines() if line.strip())


def extract_audio(src: Path, out_wav: Path, sample_rate: int = 16000) -> Path:
    ffmpeg(["-i", src, "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", out_wav],
           desc=f"extract audio from {Path(src).name}")
    return out_wav


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a 16-bit PCM wav into float32 mono samples in [-1, 1]."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sr


def list_videos(folder: Path) -> list[Path]:
    folder = Path(folder)
    if folder.is_file():
        return [folder]
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def fmt_time(seconds: float, millis: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if millis:
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    return f"{h:02d}:{m:02d}:{int(s):02d}" if h else f"{m:02d}:{int(s):02d}"


# Encoding settings for intermediate files: near-lossless and quick.
INTERMEDIATE_VIDEO = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p"]
INTERMEDIATE_AUDIO = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def cut(src: Path, start: float, end: float, out: Path) -> Path:
    """Frame-accurate cut (re-encodes)."""
    info = probe(src)
    dur = f"{max(0.05, end - start):.3f}"
    args = ["-ss", f"{start:.3f}", "-i", src]
    if info.has_audio:
        args += ["-map", "0:v:0", "-map", "0:a:0"]
    else:  # add a silent track so every clip can be joined later
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0"]
    args += ["-t", dur, *INTERMEDIATE_VIDEO, *INTERMEDIATE_AUDIO]
    ffmpeg([*args, "-movflags", "+faststart", out], desc=f"cut {Path(src).name} {start:.1f}-{end:.1f}")
    return out
