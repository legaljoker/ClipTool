"""Speaker identification ("diarization"): who is talking when.

Two engines:
  pyannote - very accurate; needs `pip install pyannote.audio` and a free
             Hugging Face token (see README).
  builtin  - no extra installs; groups transcript segments by voice
             characteristics (MFCC timbre + pitch). Works well for 2-3 people
             with clearly different voices in a clean recording.
Speakers are labelled SPEAKER_1, SPEAKER_2... in order of first appearance;
map them to names / avatar folders in config.yaml -> speakers.names.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np

from cliptool.logger import get_logger
from cliptool.transcribe import Segment, Transcript

log = get_logger("speakers")


# ---------------------------------------------------------------------------
# Audio features
# ---------------------------------------------------------------------------

def _mel_filterbank(sr: int, n_fft: int, n_mels: int = 26) -> np.ndarray:
    def hz_to_mel(f):
        return 2595 * np.log10(1 + f / 700)

    def mel_to_hz(m):
        return 700 * (10 ** (m / 2595) - 1)

    mels = np.linspace(hz_to_mel(60), hz_to_mel(sr / 2 * 0.95), n_mels + 2)
    bins = np.floor((n_fft + 1) * mel_to_hz(mels) / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for i in range(1, n_mels + 1):
        a, b, c = bins[i - 1], bins[i], bins[i + 1]
        for k in range(a, b):
            fb[i - 1, k] = (k - a) / max(1, b - a)
        for k in range(b, c):
            fb[i - 1, k] = (c - k) / max(1, c - b)
    return fb


def mfcc(samples: np.ndarray, sr: int, n_mfcc: int = 13) -> np.ndarray:
    """MFCC frames (n_frames x n_mfcc) with 25ms windows and 10ms hop."""
    win, hop = int(0.025 * sr), int(0.010 * sr)
    if len(samples) < win:
        return np.zeros((0, n_mfcc))
    emphasized = np.append(samples[0], samples[1:] - 0.97 * samples[:-1])
    n_frames = 1 + (len(emphasized) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = emphasized[idx] * np.hamming(win)
    n_fft = 512
    power = np.abs(np.fft.rfft(frames, n_fft)) ** 2 / n_fft
    energy = power.sum(axis=1)
    keep = energy > np.percentile(energy, 30)  # ignore quiet frames between words
    if keep.sum() >= 5:
        power = power[keep]
    fb = _mel_filterbank(sr, n_fft)
    logmel = np.log(power @ fb.T + 1e-10)
    n = logmel.shape[1]
    dct = np.cos(np.pi / n * (np.arange(n)[:, None] + 0.5) * np.arange(n_mfcc)[None, :])
    return logmel @ dct


def pitch(samples: np.ndarray, sr: int) -> float:
    """Median fundamental frequency (Hz) using autocorrelation on voiced frames."""
    win = int(0.04 * sr)
    hop = int(0.02 * sr)
    lo, hi = int(sr / 400), int(sr / 70)
    f0s = []
    for s in range(0, max(0, len(samples) - win), hop):
        fr = samples[s:s + win]
        if np.sqrt(np.mean(fr ** 2)) < 0.01:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[win - 1:]
        if ac[0] <= 0 or hi >= len(ac):
            continue
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] / ac[0] > 0.3:
            f0s.append(sr / lag)
    return float(np.median(f0s)) if f0s else 0.0


def segment_features(samples: np.ndarray, sr: int, seg: Segment) -> Optional[np.ndarray]:
    a, b = int(seg.start * sr), int(seg.end * sr)
    chunk = samples[a:b]
    if len(chunk) < int(0.3 * sr):
        return None
    m = mfcc(chunk, sr)
    if len(m) < 5:
        return None
    f0 = pitch(chunk[: sr * 6], sr)
    return np.concatenate([m[:, 1:].mean(axis=0), m[:, 1:].std(axis=0),
                           [np.log(f0) * 4 if f0 > 0 else 0.0]])


# ---------------------------------------------------------------------------
# Clustering (small numpy k-means + silhouette to guess the speaker count)
# ---------------------------------------------------------------------------

def kmeans(x: np.ndarray, k: int, iters: int = 50, seed: int = 0) -> tuple[np.ndarray, float]:
    rng = np.random.default_rng(seed)
    centers = [x[rng.integers(len(x))]]
    for _ in range(1, k):  # k-means++ init
        d = np.min([((x - c) ** 2).sum(axis=1) for c in centers], axis=0)
        probs = d / d.sum() if d.sum() > 0 else None
        centers.append(x[rng.choice(len(x), p=probs)])
    c = np.array(centers)
    labels = np.zeros(len(x), dtype=int)
    for _ in range(iters):
        dist = ((x[:, None, :] - c[None, :, :]) ** 2).sum(axis=2)
        new = dist.argmin(axis=1)
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
        for i in range(k):
            if (labels == i).any():
                c[i] = x[labels == i].mean(axis=0)
    inertia = float(((x - c[labels]) ** 2).sum())
    return labels, inertia


def silhouette(x: np.ndarray, labels: np.ndarray) -> float:
    k = len(set(labels.tolist()))
    if k < 2 or k >= len(x):
        return -1.0
    if len(x) > 800:  # sample to keep memory/time small on long recordings
        pick = np.random.default_rng(0).choice(len(x), 800, replace=False)
        x, labels = x[pick], labels[pick]
    sq = (x ** 2).sum(axis=1)
    d = np.sqrt(np.maximum(sq[:, None] + sq[None, :] - 2 * x @ x.T, 0))
    scores = []
    for i in range(len(x)):
        same = labels == labels[i]
        same[i] = False
        if not same.any():
            scores.append(0.0)
            continue
        a = d[i, same].mean()
        b = min(d[i, labels == other].mean() for other in set(labels.tolist()) if other != labels[i])
        scores.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return float(np.mean(scores))


def cluster_speakers(feats: np.ndarray, num: Optional[int], max_speakers: int) -> np.ndarray:
    if len(feats) < 2:
        return np.zeros(len(feats), dtype=int)
    x = (feats - feats.mean(axis=0)) / (feats.std(axis=0) + 1e-6)
    if num:
        return kmeans(x, min(num, len(x)))[0]
    best_labels, best_score = np.zeros(len(x), dtype=int), 0.12  # below this, assume one speaker
    for k in range(2, min(max_speakers, len(x) - 1) + 1):
        labels, _ = min((kmeans(x, k, seed=s) for s in range(4)), key=lambda r: r[1])
        sc = silhouette(x, labels)
        log.debug("  k=%d silhouette=%.3f", k, sc)
        if sc > best_score:
            best_labels, best_score = labels, sc
    return best_labels


def _smooth_labels(labels: list[int], segs: list[Segment]) -> list[int]:
    """Very short isolated segments usually belong to the surrounding speaker."""
    out = labels[:]
    for i in range(1, len(out) - 1):
        if out[i - 1] == out[i + 1] != out[i] and segs[i].duration < 1.2:
            out[i] = out[i - 1]
    return out


def diarize_builtin(transcript: Transcript, samples: np.ndarray, sr: int,
                    num: Optional[int], max_speakers: int) -> Transcript:
    feats, idx = [], []
    for i, seg in enumerate(transcript.segments):
        f = segment_features(samples, sr, seg)
        if f is not None:
            feats.append(f)
            idx.append(i)
    labels_all = [-1] * len(transcript.segments)
    if feats:
        labels = cluster_speakers(np.array(feats), num, max_speakers)
        for i, lab in zip(idx, labels):
            labels_all[i] = int(lab)
    # unlabeled (too short) segments inherit the previous label
    last = next((lab for lab in labels_all if lab >= 0), 0)
    for i, lab in enumerate(labels_all):
        if lab < 0:
            labels_all[i] = last
        last = labels_all[i]
    labels_all = _smooth_labels(labels_all, transcript.segments)
    return _apply_labels(transcript, labels_all)


def _apply_labels(transcript: Transcript, labels: list) -> Transcript:
    order: dict = {}
    for lab in labels:
        if lab not in order:
            order[lab] = f"SPEAKER_{len(order) + 1}"
    for seg, lab in zip(transcript.segments, labels):
        seg.speaker = order[lab]
    return transcript


def diarize_pyannote(transcript: Transcript, wav_path: Path, token: str,
                     num: Optional[int], max_speakers: int) -> Transcript:
    from pyannote.audio import Pipeline

    pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=token)
    kwargs = {"num_speakers": num} if num else {"max_speakers": max_speakers}
    result = pipe(str(wav_path), **kwargs)
    turns = [(t.start, t.end, spk) for t, _, spk in result.itertracks(yield_label=True)]
    labels = []
    for seg in transcript.segments:
        overlap: dict = {}
        for s, e, spk in turns:
            o = min(seg.end, e) - max(seg.start, s)
            if o > 0:
                overlap[spk] = overlap.get(spk, 0.0) + o
        labels.append(max(overlap, key=overlap.get) if overlap else (labels[-1] if labels else "?"))
    return _apply_labels(transcript, labels)


def pyannote_available() -> bool:
    try:
        import pyannote.audio  # noqa: F401
        return True
    except ImportError:
        return False


def identify_speakers(transcript: Transcript, wav_path: Path, samples: np.ndarray, sr: int,
                      cfg: dict) -> Transcript:
    engine = cfg.get("engine", "auto")
    token = cfg.get("huggingface_token") or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN")
    num, max_spk = cfg.get("num_speakers"), int(cfg.get("max_speakers", 4))
    if engine in ("auto", "pyannote") and pyannote_available() and token:
        try:
            log.info("Identifying speakers with pyannote...")
            return diarize_pyannote(transcript, wav_path, token, num, max_spk)
        except Exception as exc:
            log.warning("pyannote failed (%s); falling back to built-in speaker detection.", exc)
    elif engine == "pyannote":
        log.warning("pyannote not available (install pyannote.audio and set a Hugging Face token); "
                    "using built-in speaker detection.")
    log.info("Identifying speakers (built-in voice clustering)...")
    tr = diarize_builtin(transcript, samples, sr, num, max_spk)
    log.info("  -> %d speaker(s) found", len(tr.speakers()))
    return tr


def speaker_stats(transcript: Transcript) -> dict[str, float]:
    stats: dict[str, float] = {}
    for s in transcript.segments:
        if s.speaker:
            stats[s.speaker] = stats.get(s.speaker, 0.0) + s.duration
    return stats
