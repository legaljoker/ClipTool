"""Unit tests for the pure-python parts (no ffmpeg needed)."""
import numpy as np

from cliptool import captions, clipfinder, compile as comp, emotions, metadata, reformat, silence, speakers
from cliptool.ffmpeg_utils import MediaInfo
from cliptool.transcribe import Segment, Transcript, Word

from conftest import fake_transcript


def test_parse_silencedetect():
    err = ("[silencedetect @ 0x1] silence_start: 1.5\n[silencedetect @ 0x1] silence_end: 3.25 | "
           "silence_duration: 1.75\n[silencedetect @ 0x1] silence_start: 9.0\n")
    assert silence.parse_silencedetect(err, 10.0) == [(1.5, 3.25), (9.0, 10.0)]


def test_keep_ranges_and_time_maps():
    keep = silence.keep_ranges([(2.0, 4.0), (6.0, 8.0)], 10.0, padding=0.1)
    assert keep == [(0.0, 2.1), (3.9, 6.1), (7.9, 10.0)]
    assert silence.original_to_cut(1.0, keep) == 1.0
    assert silence.original_to_cut(3.0, keep) is None
    assert abs(silence.original_to_cut(4.0, keep) - 2.2) < 1e-9
    assert abs(silence.cut_to_original(2.2, keep) - 4.0) < 1e-9


def test_remap_transcript_drops_words_in_silence():
    tr = Transcript([Segment(0, 5, "a b", [Word(0.5, 1.0, " a"), Word(2.5, 3.0, " b"), Word(4.2, 4.6, " c")])])
    keep = [(0.0, 2.0), (4.0, 5.0)]
    out = silence.remap_transcript(tr, keep)
    texts = [w.text.strip() for w in out.segments[0].words]
    assert texts == ["a", "c"]
    assert abs(out.segments[0].words[1].start - 2.2) < 1e-6


def test_transcript_slice_and_concat():
    tr = fake_transcript(seconds=12)
    part = tr.slice(3.0, 6.0)
    assert part.segments[0].start == 0.0
    assert part.segments[0].text.startswith("I love")
    joined = Transcript.concat([(part, 0.0), (part, 3.0)])
    assert len(joined.segments) == 2 and joined.segments[1].start == 3.0
    assert "-->" in tr.to_srt() and tr.to_vtt().startswith("WEBVTT")


def test_ass_captions_highlight_each_word():
    tr = fake_transcript(seconds=6)
    ass = captions.build_ass(tr, 1080, 1920, {"max_words": 3})
    assert "PlayResY: 1920" in ass
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue")]
    assert len(dialogues) == len(tr.all_words())
    assert "HERE'S" in ass and "\\c&H00E0FF&" in ass  # highlight colour in BGR


def test_group_words_breaks_on_punctuation():
    words = [Word(i, i + 0.5, t) for i, t in enumerate([" one,", " two", " three", " four", " five."])]
    groups = captions.group_words(words, 3)
    assert [len(g) for g in groups] == [1, 3, 1]


def test_clip_finder_prefers_hooky_emotional_windows():
    tr = fake_transcript(seconds=60)
    clips = clipfinder.find_clips(tr, 5, 10, 3)
    assert len(clips) == 3
    assert all(5 <= c.duration <= 10 for c in clips)
    assert clips[0].score >= clips[-1].score
    for a in clips:
        for b in clips:
            if a is not b:
                assert min(a.end, b.end) - max(a.start, b.start) <= 0.2 * min(a.duration, b.duration)


def test_clip_finder_short_video_returns_whole():
    clips = clipfinder.find_clips(fake_transcript(seconds=9), 30, 60, 3)
    assert len(clips) == 1


def test_bestof_selection_hits_target():
    tr = fake_transcript(seconds=60)
    moments = clipfinder.find_clips(tr, 2, 6, 6)
    chosen = comp.select_bestof({"a.mp4": moments}, 12, 3)
    total = sum(m.clip.duration for m in chosen)
    assert 0 < total <= 14
    assert chosen[0].out_offset == 0.0


def test_chapters_first_at_zero_and_spaced():
    text = comp.chapters_text([(0, "Intro"), (5, "Too close"), (65, "Part two")])
    assert text.splitlines() == ["00:00 Intro", "01:05 Part two"]


def test_emotion_text_classifier():
    assert emotions.classify_text("I hate this, it's terrible and awful")[0] == "angry"
    assert emotions.classify_text("I love it, so happy")[0] == "happy"
    assert emotions.classify_text("The meeting is at noon")[0] == "neutral"
    assert emotions.adjust_for_energy("mad", 1.5) == "angry"


def test_metadata_drafts():
    tr = fake_transcript(seconds=18)
    md = metadata.builtin_metadata(tr, "youtube_shorts", 5, 8)
    assert len(md.titles) == 5
    assert "#shorts" in md.hashtags
    assert all(len(t) <= 73 for t in md.titles)


def test_reformat_filters():
    info = MediaInfo(path=None, duration=10, width=1920, height=1080, fps=30, has_audio=True, has_video=True)
    f = reformat.build_filter(info, 1080, 1920, "crop", "0:v", "out")
    assert "crop=606:1080:656:0" in f
    f = reformat.build_filter(info, 1080, 1920, "blur", "0:v", "out")
    assert "boxblur" in f and "overlay" in f
    keys = reformat.plan_camera([(0, 0.2), (0.5, 0.2), (1.0, 0.8), (1.5, 0.8), (2.0, 0.8)], 1920, 606, window=1)
    assert len(keys) == 2 and keys[0][1] == 81.0
    f = reformat.build_filter(info, 1080, 1920, "smart", "0:v", "out", keys)
    assert "clip((t-1.00)" in f


def test_speaker_clustering_separates_two_voices():
    rng = np.random.default_rng(1)
    a = rng.normal(0, 0.3, (12, 6)) + 3
    b = rng.normal(0, 0.3, (12, 6)) - 3
    labels = speakers.cluster_speakers(np.vstack([a, b]), None, 4)
    assert len(set(labels[:12])) == 1 and len(set(labels[12:])) == 1 and labels[0] != labels[12]


def test_builtin_diarization_on_two_synthetic_voices():
    sr = 16000
    t = np.arange(int(sr * 2)) / sr

    def voice(f0):  # harmonic-rich "voice" with a distinct pitch and timbre
        return sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8)).astype(np.float32) * 0.2

    low, high = voice(110), voice(260)
    samples = np.concatenate([low, high, low, high, low, high])
    segs = [Segment(i * 2, i * 2 + 2, f"line {i}") for i in range(6)]
    tr = speakers.diarize_builtin(Transcript(segs), samples, sr, None, 4)
    assert [s.speaker for s in tr.segments] == ["SPEAKER_1", "SPEAKER_2"] * 3
