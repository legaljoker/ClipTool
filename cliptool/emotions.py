"""Expression detection: tags each spoken segment as neutral / happy / excited /
sad / mad / angry / surprised / scared so the matching avatar picture can be shown.

Engines:
  transformer - text emotion model from Hugging Face (needs `pip install transformers torch`)
  builtin     - emotion word lists + punctuation + how loud/energetic the voice is
Both are combined with voice energy: a loud negative line becomes 'angry' rather
than 'mad', a loud positive one 'excited' rather than 'happy'.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

import numpy as np

from cliptool.logger import get_logger
from cliptool.textutil import emotion_counts
from cliptool.transcribe import Transcript

log = get_logger("emotions")

EXPRESSIONS = ["neutral", "happy", "excited", "sad", "mad", "angry", "surprised", "scared"]

# Which picture to use when a person doesn't have a picture for an expression.
FALLBACKS = {
    "excited": ["happy"],
    "angry": ["mad"],
    "mad": ["angry"],
    "surprised": ["excited", "happy"],
    "scared": ["sad", "surprised"],
    "sad": [],
    "happy": [],
}

_TRANSFORMER_MAP = {"joy": "happy", "anger": "angry", "sadness": "sad", "fear": "scared",
                    "surprise": "surprised", "disgust": "mad", "neutral": "neutral"}

_pipe = None


def transformer_available() -> bool:
    try:
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _transformer_labels(texts: list[str], model: str) -> list[tuple[str, float]]:
    global _pipe
    if _pipe is None:
        from transformers import pipeline
        log.info("Loading emotion model %s (first time downloads it)...", model)
        _pipe = pipeline("text-classification", model=model, top_k=1)
    out = []
    for res in _pipe(texts, truncation=True):
        best = res[0] if isinstance(res, list) else res
        out.append((_TRANSFORMER_MAP.get(best["label"], "neutral"), float(best["score"])))
    return out


def classify_text(text: str) -> tuple[str, float]:
    counts = emotion_counts(text)
    if text.count("!") >= 1:
        counts["excited"] += 0.5 * text.count("!")
    if "?!" in text or "!?" in text:
        counts["surprised"] += 1
    if not counts or max(counts.values()) <= 0:
        return "neutral", 0.0
    emo, n = counts.most_common(1)[0]
    words = max(1, len(text.split()))
    return emo, min(1.0, n / words * 6)


def voice_energy(samples: np.ndarray, sr: int, start: float, end: float) -> float:
    chunk = samples[int(start * sr):int(end * sr)]
    if len(chunk) == 0:
        return -90.0
    return float(20 * np.log10(np.sqrt(np.mean(chunk ** 2)) + 1e-10))


def adjust_for_energy(emo: str, energy_z: float) -> str:
    if energy_z > 0.8:
        return {"mad": "angry", "happy": "excited", "neutral": "neutral"}.get(emo, emo)
    if energy_z < -0.8:
        return {"angry": "mad", "excited": "happy"}.get(emo, emo)
    return emo


def tag_expressions(transcript: Transcript, cfg: dict,
                    audio: Optional[tuple[np.ndarray, int]] = None) -> Transcript:
    segs = transcript.segments
    if not segs:
        return transcript
    engine = cfg.get("engine", "auto")
    labels: list[tuple[str, float]]
    if engine in ("auto", "transformer") and transformer_available():
        try:
            labels = _transformer_labels([s.text for s in segs], cfg.get("transformer_model"))
            log.info("Expressions detected with transformer model.")
        except Exception as exc:
            log.warning("Emotion model failed (%s); using built-in word lists.", exc)
            labels = [classify_text(s.text) for s in segs]
    else:
        labels = [classify_text(s.text) for s in segs]

    # per-speaker loudness baseline so a naturally loud person isn't always 'angry'
    z = [0.0] * len(segs)
    if audio is not None:
        samples, sr = audio
        energies = [voice_energy(samples, sr, s.start, s.end) for s in segs]
        by_spk: dict = {}
        for i, s in enumerate(segs):
            by_spk.setdefault(s.speaker, []).append(energies[i])
        stats = {k: (np.mean(v), np.std(v) + 1e-6) for k, v in by_spk.items()}
        z = [(energies[i] - stats[s.speaker][0]) / stats[s.speaker][1] for i, s in enumerate(segs)]

    for seg, (emo, _conf), ez in zip(segs, labels, z):
        seg.emotion = adjust_for_energy(emo, ez)
    summary = Counter(s.emotion for s in segs)
    log.info("  expressions: %s", ", ".join(f"{k} x{v}" for k, v in summary.most_common()))
    return transcript
