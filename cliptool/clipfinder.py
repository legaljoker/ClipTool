"""Clip finder: scans a transcript and suggests the strongest moments.

Every possible window of sentences between min_length and max_length seconds gets
scored on things that tend to make short-form clips work: a strong opening hook,
emotion and energy, questions/exclamations, talking pace, loudness peaks, how
on-topic it is, and whether it starts and ends on a full sentence. The best
non-overlapping windows win. If Claude is configured, it re-ranks the shortlist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from cliptool.textutil import content_words, emotion_counts, hook_score, top_keywords
from cliptool.transcribe import Transcript, Word


@dataclass
class Unit:
    start: float
    end: float
    text: str
    n_words: int


@dataclass
class Clip:
    start: float
    end: float
    score: float
    text: str
    hook: str
    reasons: list[str] = field(default_factory=list)
    title: str = ""
    source: str = ""
    rank: int = 0
    original_start: Optional[float] = None
    original_end: Optional[float] = None
    output_files: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


def build_units(transcript: Transcript, max_unit: float = 12.0, gap: float = 1.0) -> list[Unit]:
    """Split the transcript into sentence-sized pieces."""
    units: list[Unit] = []
    for seg in transcript.segments:
        if not seg.words:
            units.append(Unit(seg.start, seg.end, seg.text.strip(), len(seg.text.split())))
            continue
        cur: list[Word] = []
        for w in seg.words:
            if cur and (w.start - cur[-1].end > gap or w.end - cur[0].start > max_unit):
                units.append(_unit(cur))
                cur = []
            cur.append(w)
            if w.text.strip()[-1:] in ".?!":
                units.append(_unit(cur))
                cur = []
        if cur:
            units.append(_unit(cur))
    return [u for u in units if u.text]


def _unit(words: list[Word]) -> Unit:
    return Unit(words[0].start, words[-1].end, "".join(w.text for w in words).strip(), len(words))


def loudness_curve(samples: np.ndarray, sr: int, hop: float = 0.5) -> np.ndarray:
    n = int(sr * hop)
    if n <= 0 or len(samples) < n:
        return np.zeros(0)
    frames = samples[: len(samples) // n * n].reshape(-1, n)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-10)
    return 20 * np.log10(rms)


def _window_loudness(curve: Optional[np.ndarray], start: float, end: float, hop: float = 0.5):
    if curve is None or len(curve) == 0:
        return None
    a, b = int(start / hop), max(int(start / hop) + 1, int(end / hop))
    seg = curve[a:b]
    if len(seg) == 0:
        return None
    voiced = seg[seg > np.percentile(curve, 20)]
    return float(np.mean(voiced if len(voiced) else seg)), float(np.percentile(seg, 90))


def score_window(units: list[Unit], keywords: set[str], curve: Optional[np.ndarray],
                 global_loud: Optional[float]) -> tuple[float, list[str]]:
    text = " ".join(u.text for u in units)
    start, end = units[0].start, units[-1].end
    dur = max(0.1, end - start)
    n_words = sum(u.n_words for u in units)
    reasons: list[tuple[float, str]] = []

    hook = min(1.0, hook_score(units[0].text) / 2.5)
    if len(units) > 1:
        hook = max(hook, 0.7 * min(1.0, hook_score(units[1].text) / 2.5))
    reasons.append((hook * 2.0, f"strong opening hook: \"{units[0].text[:70]}\""))

    emo = emotion_counts(text)
    emo_total = sum(emo.values())
    emo_score = min(1.0, emo_total / max(1, n_words) * 12)
    if emo_total:
        top = ", ".join(e for e, _ in emo.most_common(2))
        reasons.append((emo_score * 1.5, f"emotional language ({top})"))

    punch = text.count("!") + text.count("?")
    punch_score = min(1.0, punch / max(1, len(units)) * 1.5)
    reasons.append((punch_score * 1.0, f"{punch} questions/exclamations keep it punchy"))

    wps = n_words / dur
    pace = max(0.0, 1.0 - abs(wps - 2.9) / 2.0)
    reasons.append((pace * 1.0, f"good talking pace ({wps:.1f} words/sec)"))

    cws = content_words(text)
    topic = min(1.0, (sum(1 for w in cws if w in keywords) / max(1, len(cws))) * 4) if cws else 0.0
    reasons.append((topic * 1.0, "stays on the main topic of the video"))

    gaps = sum(max(0.0, b.start - a.end - 1.0) for a, b in zip(units, units[1:]))
    gap_penalty = min(1.5, gaps / dur * 6)

    complete = 0.0
    if units[-1].text[-1:] in ".!?":
        complete += 0.5
    if units[0].text[:1].isupper():
        complete += 0.3
    reasons.append((complete, "starts and ends on complete sentences"))

    laugh = sum(text.lower().count(x) for x in ("haha", "lol", "[laughter]", "(laughs)"))
    if laugh:
        reasons.append((min(1.0, laugh * 0.5), "laughter"))

    loud_score = 0.0
    lw = _window_loudness(curve, start, end)
    if lw and global_loud is not None:
        mean, peak = lw
        loud_score = float(np.clip((mean - global_loud) / 6 + 0.3, 0, 1)) * 0.6 \
            + float(np.clip((peak - global_loud) / 10, 0, 1)) * 0.4
        reasons.append((loud_score * 1.2, "high vocal energy / loud moments"))

    total = sum(r[0] for r in reasons) - gap_penalty
    nice = [r for s, r in sorted(reasons, key=lambda x: -x[0]) if s >= 0.35][:3]
    return total, nice


def find_clips(transcript: Transcript, min_len: float = 30, max_len: float = 60, count: int = 5,
               audio: Optional[tuple[np.ndarray, int]] = None,
               reranker: Optional[Callable[[list[Clip], Transcript, int], list[Clip]]] = None,
               allow_short: bool = True) -> list[Clip]:
    units = build_units(transcript)
    if not units:
        return []
    keywords = set(" ".join(top_keywords(transcript.text, 15)).split())
    curve = loudness_curve(*audio) if audio else None
    global_loud = None
    if curve is not None and len(curve):
        voiced = curve[curve > np.percentile(curve, 20)]
        global_loud = float(np.mean(voiced)) if len(voiced) else float(np.mean(curve))

    cands: list[Clip] = []
    for i in range(len(units)):
        for j in range(i, len(units)):
            dur = units[j].end - units[i].start
            if dur > max_len:
                break
            if dur < min_len:
                continue
            window = units[i:j + 1]
            score, reasons = score_window(window, keywords, curve, global_loud)
            cands.append(Clip(window[0].start, window[-1].end, score,
                              " ".join(u.text for u in window), window[0].text, reasons,
                              source=transcript.source))

    if not cands and allow_short:
        # Video shorter than min_len: suggest the whole thing.
        score, reasons = score_window(units, keywords, curve, global_loud)
        cands.append(Clip(units[0].start, units[-1].end, score, " ".join(u.text for u in units),
                          units[0].text, reasons + ["whole video is shorter than the minimum clip length"],
                          source=transcript.source))

    cands.sort(key=lambda c: -c.score)
    shortlist = pick_non_overlapping(cands, max(count * 3, count))
    if reranker and shortlist:
        try:
            shortlist = reranker(shortlist, transcript, count)
        except Exception as exc:  # AI is optional: never let it break the run
            from cliptool.logger import get_logger
            get_logger("clips").warning("AI re-ranking skipped: %s", exc)
    best = shortlist[:count]
    for rank, clip in enumerate(best, 1):
        clip.rank = rank
    return best


def pick_non_overlapping(cands: list[Clip], count: int, max_overlap: float = 0.2) -> list[Clip]:
    chosen: list[Clip] = []
    for c in cands:
        ok = True
        for o in chosen:
            inter = min(c.end, o.end) - max(c.start, o.start)
            if inter > max_overlap * min(c.duration, o.duration):
                ok = False
                break
        if ok:
            chosen.append(c)
            if len(chosen) >= count:
                break
    return chosen
