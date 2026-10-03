import shutil
import subprocess
from pathlib import Path

import pytest

from cliptool.config import Config, DEFAULTS, deep_merge
from cliptool.transcribe import Segment, Transcript, Word

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

PHRASES = [
    "Here's the secret nobody tells you!",
    "I love this so much, it's amazing.",
    "Why does this always happen to me?",
    "That is so annoying and I hate it!",
    "Okay so the next step is simple.",
    "Wow, I can't believe it worked!",
]


def make_video(path: Path, seconds: int = 18, size: str = "640x360") -> Path:
    """Test pattern with 2s tone bursts separated by 1s of silence (one burst per phrase)."""
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
        "-f", "lavfi", "-i",
        f"aevalsrc='0.5*sin(2*PI*(220+110*floor(t/6))*t)*lt(mod(t,3),2)':s=48000:d={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True)
    return path


def fake_transcript(source: str = "", seconds: int = 18) -> Transcript:
    segs = []
    for i in range(seconds // 3):
        start = i * 3.0
        words_txt = PHRASES[i % len(PHRASES)].split()
        step = 1.9 / len(words_txt)
        words = [Word(start + j * step, start + (j + 1) * step, " " + w) for j, w in enumerate(words_txt)]
        segs.append(Segment(start, start + 1.9, " ".join(words_txt), words))
    return Transcript(segs, "en", source)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    if shutil.which("ffmpeg"):
        make_video(d / "wide.mp4")
        make_video(d / "second.mp4", 12)
        make_video(d / "tall.mp4", 9, "360x640")
    return d


@pytest.fixture
def cfg(tmp_path):
    c = Config(deep_merge(DEFAULTS, {}))
    c["paths"] = {k: str(tmp_path / k) for k in ("input_dir", "output_dir", "avatars_dir", "log_dir")}
    c["export"]["preset"] = "ultrafast"
    c["clips"]["use_ai"] = False
    c["metadata"]["use_ai"] = False
    return c


@pytest.fixture
def fake_whisper(monkeypatch):
    """Replace Whisper with a canned transcript matching the synthetic videos."""
    from cliptool import pipeline
    from cliptool.ffmpeg_utils import probe

    def _fake(path, cfg, cache_dir=None):
        return fake_transcript(str(path), int(round(probe(path).duration)))

    monkeypatch.setattr(pipeline, "transcribe", _fake)
