"""End-to-end tests: real ffmpeg renders on small synthetic videos (Whisper is stubbed)."""
import json
import subprocess
from pathlib import Path

from cliptool import silence
from cliptool.ffmpeg_utils import probe
from cliptool.pipeline import JobOptions, batch, run_job

from conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


def _videos(job, platform):
    return sorted((job.job_dir / "videos" / platform).glob("*.mp4"))


def make_avatar(folder: Path, name: str, color: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={color}:s=200x200", "-frames:v", "1", str(folder / f"{name}.png")], check=True)


def test_silence_cut_removes_gaps(media_dir, tmp_path, cfg):
    out, keep, stats = silence.cut_silence(media_dir / "wide.mp4", tmp_path / "cut.mp4", cfg["silence"], tmp_path)
    assert stats["cuts"] >= 4
    assert abs(probe(out).duration - stats["kept"]) < 0.5
    assert probe(out).duration < 15


def test_quick_short_with_captions(media_dir, cfg, fake_whisper):
    opts = JobOptions(name="quick", mode="each", silence_cut=True, captions=True, metadata=True,
                      platforms=["tiktok", "youtube"], reformat_mode="smart")
    job = run_job([media_dir / "wide.mp4"], opts, cfg)
    assert not job.errors, job.errors
    tk = probe(_videos(job, "tiktok")[0])
    assert (tk.width, tk.height) == (1080, 1920) and tk.has_audio
    yt = probe(_videos(job, "youtube")[0])
    assert (yt.width, yt.height) == (1920, 1080)
    report = job.report_file.read_text()
    assert "Silence removed" in report and "Title 1:" in report
    assert (job.job_dir / "videos" / "tiktok" / "wide.txt").exists()
    assert (job.job_dir / "transcripts" / "wide.srt").exists()


def test_clip_finder_exports_clips_and_report(media_dir, cfg, fake_whisper):
    cfg["clips"].update(min_length=4, max_length=8, count=2)
    opts = JobOptions(name="clips", mode="clips", find_clips=True, captions=True, platforms=["tiktok"],
                      reformat_mode="blur")
    job = run_job([media_dir / "wide.mp4"], opts, cfg)
    assert not job.errors, job.errors
    clips = _videos(job, "tiktok")
    assert len(clips) == 2
    for c in clips:
        assert 3.5 <= probe(c).duration <= 9
    report = job.report_file.read_text()
    assert "BEST CLIPS" in report and "#1" in report and "Cut it yourself" in report
    assert (job.job_dir / "best_clips.csv").exists()


def test_bestof_combines_sources(media_dir, cfg, fake_whisper):
    cfg["bestof"].update(target_length=10, moment_min=2, moment_max=5, max_per_source=2)
    opts = JobOptions(name="best", mode="bestof", captions=True, platforms=["youtube_shorts"],
                      reformat_mode="crop")
    job = run_job([media_dir / "wide.mp4", media_dir / "second.mp4", media_dir / "tall.mp4"], opts, cfg)
    assert not job.errors, job.errors
    out = _videos(job, "youtube_shorts")[0]
    assert probe(out).duration <= 13
    assert "BEST-OF COMPILATION" in job.report_file.read_text()


def test_longform_with_chapters(media_dir, cfg, fake_whisper):
    opts = JobOptions(name="long", mode="longform", metadata=True, platforms=["youtube"])
    job = run_job([media_dir / "wide.mp4", media_dir / "second.mp4", media_dir / "tall.mp4"], opts, cfg)
    assert not job.errors, job.errors
    out = probe(_videos(job, "youtube")[0])
    assert (out.width, out.height) == (1920, 1080)
    assert abs(out.duration - 39) < 1.5
    report = job.report_file.read_text()
    chapters = [line.strip() for line in report.splitlines() if line.strip()[:3] in ("00:", "01:")]
    assert [c[:5] for c in chapters[:3]] == ["00:00", "00:18", "00:30"]
    assert len(set(chapters[:3])) == 3


def test_speakers_expressions_and_avatars(media_dir, cfg, fake_whisper, tmp_path):
    av = Path(cfg["paths"]["avatars_dir"])
    make_avatar(av / "Dave", "neutral", "red")
    make_avatar(av / "Dave", "happy", "yellow")
    make_avatar(av / "Dave", "happy_talk", "orange")
    make_avatar(av / "Dave", "angry", "purple")
    cfg["speakers"]["names"] = {"SPEAKER_1": "Dave"}
    opts = JobOptions(name="avatars", mode="each", avatars=True, captions=True, platforms=["tiktok"])
    job = run_job([media_dir / "second.mp4"], opts, cfg)
    assert not job.errors, job.errors
    out = _videos(job, "tiktok")[0]
    assert probe(out).duration > 11
    timeline = json.loads(next((job.job_dir / "timelines").glob("*.json")).read_text())
    assert {t["expression"] for t in timeline} & {"happy", "angry", "excited", "mad"}
    # the avatar box (top-left) should be coloured, not the test pattern, while someone talks
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "1.0", "-i", str(out), "-frames:v", "1",
                    "-vf", "crop=40:40:80:80,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(frame)],
                   check=True)
    r, g, b = frame.read_bytes()[:3]
    assert r > 150 and b < 120, (r, g, b)  # red/orange/yellow family = avatar visible


def test_analyze_only_writes_report_without_videos(media_dir, cfg, fake_whisper):
    cfg["clips"].update(min_length=4, max_length=8, count=3)
    opts = JobOptions(name="analyze", mode="analyze", find_clips=True, metadata=True, speakers=True,
                      platforms=["tiktok"])
    job = run_job([media_dir / "wide.mp4"], opts, cfg)
    assert not job.errors, job.errors
    assert not (job.job_dir / "videos").exists()
    text = job.report_file.read_text()
    assert "BEST CLIP:" in text and "WHO SAID WHAT" in text and "TikTok drafts" in text


def test_batch_drop_folder(media_dir, cfg, fake_whisper, tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir()
    for n in ("wide.mp4", "tall.mp4"):
        (drop / n).write_bytes((media_dir / n).read_bytes())
    opts = JobOptions(mode="each", captions=True, platforms=["tiktok", "instagram_reels"])
    results = batch(drop, opts, cfg)
    assert len(results) == 2 and all(not r.errors for r in results)
    assert (drop / "processed" / "wide.mp4").exists()
    for r in results:
        assert len(_videos(r, "tiktok")) == 1 and len(_videos(r, "instagram_reels")) == 1


def test_filter_script_option_works_with_installed_ffmpeg(tmp_path):
    from cliptool.ffmpeg_utils import ffmpeg, filter_script_args

    script = tmp_path / "graph.filter"
    script.write_text("[0:v]scale=32:32[v]", encoding="utf-8")
    ffmpeg(["-f", "lavfi", "-i", "testsrc2=d=0.2", *filter_script_args(script), "-map", "[v]",
            "-f", "null", "-"], desc="filter script test")


def test_whisper_gets_ffmpeg_decoded_audio(media_dir, monkeypatch):
    """faster-whisper must receive samples, not a path (its PyAV decoder breaks on some versions)."""
    import sys
    import types

    import numpy as np

    from cliptool import transcribe as tmod

    seen = {}

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, **kwargs):
            seen["audio"] = audio
            w = types.SimpleNamespace(start=0.0, end=0.5, word=" hi", probability=0.9)
            seg = types.SimpleNamespace(start=0.0, end=0.5, text=" hi", words=[w])
            return iter([seg]), types.SimpleNamespace(language="en")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    tmod._model_cache.clear()
    tr = tmod.transcribe(media_dir / "second.mp4", {"engine": "faster-whisper", "model": "tiny",
                                                    "device": "cpu", "compute_type": "int8"})
    assert isinstance(seen["audio"], np.ndarray) and seen["audio"].dtype == np.float32
    assert abs(len(seen["audio"]) / 16000 - 12) < 0.2
    assert tr.segments[0].text == "hi"
    tmod._model_cache.clear()
