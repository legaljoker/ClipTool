"""Auto Edit: the passes a short-form video editor does by hand, done automatically.

Every pass works on the project (the same data the editor shows), so all results
can be changed afterwards in the editor and undone in one step.

Passes
- tighten:    remove dead air, "um/uh" and false starts / repeated words. Cuts are placed
              in the quiet part of each pause (measured from the audio, not only from the
              speech-to-text timings), keep a short natural pause, never clip the start of
              a word, and land on whole video frames.
- punch_ins:  alternate a slight zoom on every other piece of a jump cut, so the cut
              looks intentional instead of a glitch.
- emphasis:   short zoom punches on strong words.
- hook_title: a title banner over the first seconds.
- callouts:   "FOLLOW FOR MORE" style text when the speaker asks for it.
- bleeps:     censor swear words (sound + caption).
- cold_open:  repeat the strongest moment at the very start, then flash into the video.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from cliptool.editor.project import (clip_length, clip_starts, new_id, normalize, total_duration)
from cliptool.logger import get_logger
from cliptool.textutil import EMOTION_WORDS, hook_score, split_sentences

log = get_logger("autoedit")

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "ahh", "hmm", "hm", "mm", "mhm", "eh"}
# words that are often repeated on purpose ("very very", "no no no")
REPEAT_OK = {"very", "really", "so", "no", "yeah", "yes", "ha", "haha", "go", "bye", "now", "come", "oh",
             "wait", "stop", "please", "more", "again", "okay", "ok", "hey", "on", "far", "much", "long"}
SWEAR_STEMS = ("fuck", "shit", "bitch", "asshole", "bastard", "motherf", "bullshit", "cunt", "dickhead",
               "goddamn", "pussy", "dumbass", "jackass")
EMPHASIS_WORDS = (EMOTION_WORDS["excited"] | {"never", "always", "best", "worst", "biggest", "secret", "insane",
                                              "crazy", "huge", "massive", "literally", "everything", "nothing",
                                              "free", "million", "billion", "thousand", "hundred", "first",
                                              "last", "only", "every", "impossible", "perfect", "exactly"})
CTA_RULES = [
    (re.compile(r"\bfollow\b", re.I), "FOLLOW FOR MORE"),
    (re.compile(r"\bsubscribe\b", re.I), "SUBSCRIBE"),
    (re.compile(r"\blink in (?:my |the )?bio\b|\blink below\b|\blink in the description\b", re.I), "LINK IN BIO"),
    (re.compile(r"\b(?:comment|let me know)\b", re.I), "COMMENT BELOW 👇"),
    (re.compile(r"\bsave (?:this|it)\b", re.I), "SAVE THIS"),
    (re.compile(r"\bshare (?:this|it)\b", re.I), "SHARE THIS"),
    (re.compile(r"\blike (?:this|the) video\b|\bsmash (?:that|the) like\b", re.I), "LIKE THIS VIDEO ❤️"),
]


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower())


def is_swear(word: str) -> bool:
    w = norm(word)
    return any(w.startswith(s) for s in SWEAR_STEMS)


def mask_swear(word: str) -> str:
    m = re.match(r"^(\W*)(\w)(\w*)(\W*)$", word)
    if not m:
        return "*" * len(word)
    return m.group(1) + m.group(2) + "*" * len(m.group(3)) + m.group(4)


# ---------------------------------------------------------------------------
# Audio analysis
# ---------------------------------------------------------------------------

@dataclass
class Envelope:
    """Loudness of the sound every 10 ms (dB), and the level that counts as quiet."""
    db: np.ndarray
    hop: float
    quiet_db: float

    @classmethod
    def from_samples(cls, samples: np.ndarray, sr: int = 16000, hop: float = 0.01, win: float = 0.05) -> "Envelope":
        h, w = int(sr * hop), int(sr * win)
        if len(samples) < w:
            return cls(np.full(1, -90.0), hop, -40.0)
        sq = np.concatenate([[0.0], np.cumsum(samples.astype(np.float64) ** 2)])
        starts = np.arange(0, len(samples) - w + 1, h)
        rms = np.sqrt((sq[starts + w] - sq[starts]) / w)
        db = 20 * np.log10(np.maximum(rms, 1e-6))
        speech = np.percentile(db, 95)
        # a pause is anything ~24 dB under the speaking level (between -55 and -28 dB)
        quiet = float(min(-28.0, max(speech - 24.0, -55.0)))
        return cls(db, hop, quiet)

    def quiet(self, t0: float, t1: float) -> np.ndarray:
        a, b = max(0, int(t0 / self.hop)), max(0, int(t1 / self.hop))
        return self.db[a:b] < self.quiet_db

    def quietest(self, t: float, window: float = 0.08) -> float:
        """The quietest moment within +-window of t (best place for a cut inside speech)."""
        a, b = max(0, int((t - window) / self.hop)), max(1, int((t + window) / self.hop))
        seg = self.db[a:b]
        if not len(seg):
            return t
        return (a + int(np.argmin(seg))) * self.hop + self.hop / 2

    def longest_quiet(self, t0: float, t1: float) -> Optional[tuple[float, float]]:
        q = self.quiet(t0, t1)
        if not len(q) or not q.any():
            return None
        best, cur_start, best_run = None, None, 0
        for i, v in enumerate(np.append(q, False)):
            if v and cur_start is None:
                cur_start = i
            elif not v and cur_start is not None:
                if i - cur_start > best_run:
                    best_run, best = i - cur_start, (cur_start, i)
                cur_start = None
        base = max(0, int(t0 / self.hop))
        return ((base + best[0]) * self.hop, (base + best[1]) * self.hop) if best else None


# ---------------------------------------------------------------------------
# Finding what to cut
# ---------------------------------------------------------------------------

@dataclass
class Cut:
    start: float  # seconds in the source file
    end: float
    reason: str   # pause / filler / restart / start / end


def restart_indices(words: list) -> set[int]:
    """Indices of words that are a false start or an accidental repeat ("I I think", "we went, we went")."""
    drop: set[int] = set()
    toks = [norm(w.text) for w in words]
    i = 0
    while i < len(toks):
        hit = False
        if words[i].text.strip().endswith("-") and len(toks[i]) <= 12:  # cut-off word "w-"
            drop.add(i)
            i += 1
            continue
        for n in (4, 3, 2, 1):
            a, b = toks[i:i + n], toks[i + n:i + 2 * n]
            if len(b) < n or not all(a) or a != b:
                continue
            if n == 1 and a[0] in REPEAT_OK:
                continue
            if n <= 2 and all(x in REPEAT_OK for x in a):
                continue
            # only when said back to back (not "I said no. I said yes")
            if words[i + n].start - words[i + n - 1].end > 1.2:
                continue
            drop.update(range(i, i + n))
            i += n
            hit = True
            break
        if not hit:
            i += 1
    return drop


def find_cuts(words: list, clip_in: float, clip_out: float, env: Optional[Envelope], opts: dict,
              fps: float = 30.0) -> list[Cut]:
    """Pieces of [clip_in, clip_out] (source seconds) to remove."""
    keep = max(0.04, float(opts.get("keep_pause", 0.12)))
    min_gap = max(keep + 0.05, float(opts.get("min_gap", 0.3)))
    lead = max(0.04, keep * 0.45)  # kept before a word starts (protects the onset)
    tail = max(0.03, keep - lead)   # kept after a word ends
    ws = [w for w in words if clip_in <= (w.start + w.end) / 2 <= clip_out and w.text.strip()]
    dropped: dict[int, str] = {}
    if opts.get("fillers", True):
        for i, w in enumerate(ws):
            if norm(w.text) in FILLERS:
                dropped[i] = "filler"
    if opts.get("restarts", True):
        for i in restart_indices(ws):
            dropped.setdefault(i, "restart")
    kept = [w for i, w in enumerate(ws) if i not in dropped]
    cuts: list[Cut] = []
    if not kept:
        return cuts

    def add(a: float, b: float, reason: str):
        a, b = max(clip_in, a), min(clip_out, b)
        if b - a >= 0.06:
            cuts.append(Cut(a, b, reason))

    def onset_safe_end(t_word: float, floor: float) -> float:
        """Last moment before the next word that is still quiet (word timings can be early/late)."""
        if env is None:
            return t_word - lead
        q = env.quiet(max(floor, t_word - 0.25), t_word + 0.05)
        # walk back from the word start while it's loud: speech may begin before whisper says
        t = t_word
        idx = len(q) - 1 - int(0.05 / env.hop)
        while idx > 0 and not q[idx]:
            idx -= 1
            t -= env.hop
        return min(t_word, t) - lead

    def release_safe_start(t_word_end: float, ceil: float) -> float:
        if env is None:
            return t_word_end + tail
        q = env.quiet(t_word_end - 0.05, min(ceil, t_word_end + 0.4))
        t, idx = t_word_end, int(0.05 / env.hop)
        while idx < len(q) and not q[idx]:  # let the word ring out
            idx += 1
            t += env.hop
        return max(t_word_end, t) + tail

    # before the first word / after the last word
    first, last = kept[0], kept[-1]
    if first.start - clip_in > min_gap:
        add(clip_in, onset_safe_end(first.start, clip_in), "start")
    if clip_out - last.end > min_gap:
        add(release_safe_start(last.end, clip_out), clip_out, "end")
    # between kept words
    by_pos = {id(w): i for i, w in enumerate(ws)}
    for k1, k2 in zip(kept, kept[1:]):
        i1, i2 = by_pos[id(k1)], by_pos[id(k2)]
        removed = [dropped[j] for j in range(i1 + 1, i2) if j in dropped]
        gap = k2.start - k1.end
        if removed:
            reason = "filler" if "filler" in removed else "restart"
            a = release_safe_start(k1.end, k2.start)
            b = onset_safe_end(k2.start, k1.end)
            if env is not None:  # words said without a pause: cut where the sound dips most
                a, b = env.quietest(a), env.quietest(b)
            if b - a > 0.06:
                add(a, b, reason)
            continue
        if gap <= min_gap:
            continue
        a, b = k1.end, k2.start
        if env is not None:
            run = env.longest_quiet(a - 0.05, b + 0.05)
            if run is None or run[1] - run[0] < min_gap:
                continue  # not actually quiet (laughing, music, a word whisper missed)
            a, b = max(a, run[0]), min(b, run[1])
        add(a + tail, b - lead, "pause")
    # frame-accurate, merged
    q = 1.0 / (fps or 30.0)
    out: list[Cut] = []
    for c in sorted(cuts, key=lambda c: c.start):
        # round inwards to whole frames: a cut never grows into the speech around it
        c.start = round(math.ceil(c.start / q - 1e-6) * q, 3)
        c.end = round(math.floor(c.end / q + 1e-6) * q, 3)
        if c.end - c.start < q:
            continue
        if out and c.start <= out[-1].end + q:
            out[-1].end = max(out[-1].end, c.end)
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Timeline bookkeeping
# ---------------------------------------------------------------------------

def locate(p: dict, t: float) -> Optional[tuple[int, float]]:
    """(clip index, source time) shown at timeline time t."""
    clips = p["clips"]
    if not clips:
        return None
    starts = clip_starts(clips)
    for i in range(len(clips) - 1, -1, -1):
        if t >= starts[i] - 1e-6:
            u = min(t - starts[i], clip_length(clips[i]))
            return i, float(clips[i]["in"]) + u
    return 0, float(clips[0]["in"])


class TimeMap:
    """Maps old timeline times to new ones after clips were split/cut."""

    def __init__(self, old: dict, new: dict, origin: list[int]):
        self.old, self.new = old, new
        self.origin = origin  # new clip index -> old clip index
        self.new_starts = clip_starts(new["clips"])

    def __call__(self, t: float) -> tuple[float, bool]:
        """(new time, still exists). Removed moments map to where the cut is."""
        loc = locate(self.old, t)
        if loc is None:
            return t, False
        i, src = loc
        best = None
        for j, (c, s) in enumerate(zip(self.new["clips"], self.new_starts)):
            if self.origin[j] != i:
                continue
            if float(c["in"]) - 1e-6 <= src <= float(c["out"]) + 1e-6:
                return s + src - float(c["in"]), True
            if float(c["in"]) > src and best is None:
                best = s  # first piece after the removed bit
        if best is None:
            # after the last kept piece of this clip: end of that clip
            js = [j for j in range(len(self.new["clips"])) if self.origin[j] == i]
            if js:
                j = js[-1]
                return self.new_starts[j] + clip_length(self.new["clips"][j]), False
            # whole clip removed: start of the next one
            js = [j for j in range(len(self.new["clips"])) if self.origin[j] > i]
            return (self.new_starts[js[0]] if js else total_duration(self.new)), False
        return best, False


def remap_items(old: dict, new: dict, tm: TimeMap, drop_words: Callable[[float], bool]) -> None:
    """Move captions, effects, text and sound to match the new timeline."""
    caps = []
    for k in old["captions"]:
        k = copy.deepcopy(k)
        if k.get("words"):
            words = []
            for w in k["words"]:
                mid = (w["start"] + w["end"]) / 2
                if drop_words(mid):
                    continue
                s, ok1 = tm(w["start"])
                e, ok2 = tm(w["end"])
                if not (ok1 or ok2) or e - s < 0.02:
                    continue
                words.append({**w, "start": round(s, 3), "end": round(e, 3)})
            if not words:
                continue
            k["words"] = words
            k["start"], k["end"] = words[0]["start"], max(words[-1]["end"], words[0]["start"] + 0.2)
            k["text"] = " ".join(w["text"] for w in words)
        else:
            k["start"], _ = tm(k["start"])
            k["end"], _ = tm(k["end"])
            if k["end"] - k["start"] < 0.1:
                continue
        caps.append(k)
    new["captions"] = caps
    for key in ("effects", "overlays"):
        items = []
        for it in old[key]:
            it = copy.deepcopy(it)
            if it.get("type") == "video":  # fixed length: just move it
                it["start"] = round(tm(it["start"])[0], 3)
                it["end"] = round(it["start"] + float(it["out"]) - float(it["in"]), 3)
            else:
                it["start"], it["end"] = round(tm(it["start"])[0], 3), round(tm(it["end"])[0], 3)
                if it["end"] - it["start"] < 0.15:
                    continue
            items.append(it)
        new[key] = items
    for a in new["audio"]:
        a["start"] = round(tm(a["start"])[0], 3)


def apply_cuts(p: dict, cuts: dict[int, list[Cut]]) -> tuple[dict, TimeMap]:
    """Split clips around the cuts. cuts: old clip index -> cuts in source seconds."""
    new = copy.deepcopy(p)
    clips, origin = [], []
    for i, c in enumerate(p["clips"]):
        pieces, pos = [], float(c["in"])
        for cut in sorted(cuts.get(i, []), key=lambda x: x.start):
            if cut.start > pos + 0.05:
                pieces.append((pos, cut.start))
            pos = max(pos, cut.end)
        if float(c["out"]) > pos + 0.05:
            pieces.append((pos, float(c["out"])))
        for n, (a, b) in enumerate(pieces):
            piece = copy.deepcopy(c)
            piece.update({"in": round(a, 3), "out": round(b, 3)})
            if n > 0:
                piece.update({"id": new_id("c"), "transition": None, "jump": True})
            clips.append(piece)
            origin.append(i)
    new["clips"] = clips
    tm = TimeMap(p, new, origin)

    def removed(t: float) -> bool:
        loc = locate(p, t)
        if loc is None:
            return False
        i, src = loc
        return any(c.start <= src <= c.end for c in cuts.get(i, []))

    remap_items(p, new, tm, removed)
    return new, tm


def shift_after(p: dict, t0: float, delta: float) -> None:
    for key in ("captions", "effects", "overlays", "audio"):
        for it in p[key]:
            if float(it.get("start", 0)) >= t0 - 1e-6:
                it["start"] = round(float(it["start"]) + delta, 3)
                it["end"] = round(float(it["end"]) + delta, 3)
                if it.get("words"):
                    it["words"] = [{**w, "start": round(w["start"] + delta, 3), "end": round(w["end"] + delta, 3)}
                                   for w in it["words"]]


def caption_words(p: dict) -> list[dict]:
    out = []
    for k in p["captions"]:
        for w in k.get("words") or []:
            out.append({**w, "cap": k["id"]})
    return sorted(out, key=lambda w: w["start"])


def _overlaps(items, a, b, pred=lambda it: True) -> bool:
    return any(pred(it) and float(it["start"]) < b and float(it["end"]) > a for it in items)


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------

def tighten(p: dict, transcripts: dict, envelopes: dict, opts: dict) -> tuple[dict, dict]:
    """transcripts / envelopes: media id -> Transcript / Envelope. Returns (new project, stats)."""
    cuts: dict[int, list[Cut]] = {}
    media = {m["id"]: m for m in p["media"]}
    for i, c in enumerate(p["clips"]):
        tr = transcripts.get(c["media"])
        if tr is None:
            continue
        words = tr.all_words()
        if not words:
            continue
        fps = float(media.get(c["media"], {}).get("fps") or 30.0)
        cuts[i] = find_cuts(words, float(c["in"]), float(c["out"]), envelopes.get(c["media"]), opts, fps)
    stats = {"pause": 0, "filler": 0, "restart": 0, "start": 0, "end": 0, "seconds": 0.0}
    for lst in cuts.values():
        for cut in lst:
            stats[cut.reason] = stats.get(cut.reason, 0) + 1
            stats["seconds"] += cut.end - cut.start
    before = total_duration(p)
    new, _ = apply_cuts(p, cuts)
    stats["seconds"] = round(before - total_duration(new), 2)
    stats["before"], stats["after"] = round(before, 2), round(total_duration(new), 2)
    return normalize(new), stats


def punch_ins(p: dict, amount: float = 1.12) -> int:
    """Alternate a slight zoom across jump cuts (pieces of one take cut back to back)."""
    p["effects"] = [e for e in p["effects"] if e.get("auto") != "punch"]
    starts = clip_starts(p["clips"])
    n, zoomed = 0, False
    for i, c in enumerate(p["clips"]):
        prev = p["clips"][i - 1] if i else None
        jump = bool(prev and prev["media"] == c["media"] and not (c.get("transition") or {}).get("type")
                    and float(c["in"]) >= float(prev["out"]) - 0.02)
        zoomed = (not zoomed) if jump else False
        if zoomed:
            a, b = starts[i], starts[i] + clip_length(c)
            p["effects"].append({"id": new_id("e"), "type": "zoom_in", "start": round(a, 3), "end": round(b, 3),
                                 "params": {"amount": amount, "x": 0.5, "y": 0.42, "ramp": 0.01}, "auto": "punch"})
            n += 1
    return n


def emphasis(p: dict, every: float = 5.0, picks: Optional[list[float]] = None) -> int:
    """Zoom punches on strong words (or at the given timeline times)."""
    p["effects"] = [e for e in p["effects"] if e.get("auto") != "emphasis"]
    total = total_duration(p)
    if picks is None:
        picks = []
        last = -every
        for w in caption_words(p):
            t = norm(w["text"])
            strong = t in EMPHASIS_WORDS or w["text"].strip().endswith("!") or bool(re.fullmatch(r"\d{2,}", t))
            if strong and w["start"] - last >= every:
                picks.append(w["start"])
                last = w["start"]
    n = 0
    for t in picks:
        a, b = max(0.0, t - 0.12), min(total, t + 1.1)
        if b - a < 0.4 or _overlaps(p["effects"], a, b, lambda e: e.get("type") in ("zoom_in", "zoom_out", "shake")):
            continue
        p["effects"].append({"id": new_id("e"), "type": "zoom_in", "start": round(a, 3), "end": round(b, 3),
                             "params": {"amount": 1.22, "x": 0.5, "y": 0.42, "ramp": 0.18}, "auto": "emphasis"})
        n += 1
    return n


def suggest_hook(p: dict) -> str:
    """A short title from the most hook-like sentence near the start."""
    text = " ".join(k["text"] for k in sorted(p["captions"], key=lambda k: k["start"]))
    sentences = split_sentences(text)[:8]
    if not sentences:
        return ""
    best = max(sentences, key=lambda s: (hook_score(s), -sentences.index(s)))
    words = best.strip().rstrip(".!?").split()
    return " ".join(words[:5])


def fit_title(text: str, subtitle: str, W: int, H: int, size: float = 80) -> tuple[str, str, float]:
    """Shrink a title until it fits the width of the frame."""
    from cliptool.editor import textfx
    text = text.upper()
    k = min(W, H) / 1080
    while size > 36:
        s = size * k
        wide = max(textfx.measure(text, textfx.TITLE_FONT, s),
                   textfx.measure(subtitle.upper(), textfx.SUB_FONT, s * 0.38, True))
        if wide + 0.16 * s <= 0.86 * W:
            break
        size -= 4
    return text, subtitle, size


def hook_title(p: dict, text: Optional[str] = None, subtitle: str = "", length: float = 3.2) -> Optional[dict]:
    from cliptool.editor.project import CANVAS_PRESETS
    p["overlays"] = [o for o in p["overlays"] if o.get("auto") != "hook"]
    text = (text or suggest_hook(p)).strip().rstrip(".…")
    if not text:
        return None
    pr = CANVAS_PRESETS[p["preset"]]
    text, subtitle, size = fit_title(text, subtitle, pr["width"], pr["height"])
    end = min(length, max(1.0, total_duration(p)))
    o = {"id": new_id("o"), "type": "title", "template": "bar", "text": text, "subtitle": subtitle.upper(),
         "x": 0.5, "y": 0.2, "size": size, "start": 0.0, "end": round(end, 3), "auto": "hook"}
    p["overlays"].append(o)
    return o


def callouts(p: dict) -> list[dict]:
    p["overlays"] = [o for o in p["overlays"] if o.get("auto") != "callout"]
    added: list[dict] = []
    for k in sorted(p["captions"], key=lambda k: k["start"]):
        for rx, label in CTA_RULES:
            if not rx.search(k["text"]):
                continue
            if any(abs(o["start"] - k["start"]) < 4 for o in added):
                break
            o = {"id": new_id("o"), "type": "text", "style": "highlighter", "text": label, "x": 0.5, "y": 0.36,
                 "size": 70, "anim_in": "pop", "anim_out": "fade", "anim_dur": 0.3,
                 "start": round(k["start"], 3), "end": round(max(k["end"], k["start"] + 2.5), 3), "auto": "callout"}
            p["overlays"].append(o)
            added.append(o)
            break
    return added


def bleeps(p: dict) -> int:
    """Bleep effect over each swear word and mask it in the captions."""
    p["effects"] = [e for e in p["effects"] if e.get("auto") != "bleep"]
    n = 0
    for k in p["captions"]:
        changed = False
        for w in k.get("words") or []:
            if is_swear(w["text"]):
                p["effects"].append({"id": new_id("e"), "type": "bleep", "start": round(max(0, w["start"] - 0.03), 3),
                                     "end": round(w["end"] + 0.03, 3), "params": {}, "auto": "bleep"})
                w["text"] = mask_swear(w["text"])
                changed, n = True, n + 1
        if changed:
            k["text"] = " ".join(w["text"] for w in k["words"])
        elif not k.get("words") and any(is_swear(x) for x in k["text"].split()):
            k["text"] = " ".join(mask_swear(x) if is_swear(x) else x for x in k["text"].split())
    return n


def moments(p: dict, lo: float = 2.0, hi: float = 5.0, skip_start: Optional[float] = None) -> list[tuple]:
    """Quotable caption runs, best first: (start, end, text, score)."""
    total = total_duration(p)
    skip = min(6.0, total * 0.2) if skip_start is None else skip_start
    caps = sorted(p["captions"], key=lambda k: k["start"])
    out = []
    for i in range(len(caps)):
        a = caps[i]["start"]
        if a < skip:
            continue
        text, b = "", a
        for k in caps[i:]:
            if k["end"] - a > hi:
                break
            text += " " + k["text"]
            b = k["end"]
        if b - a < lo:
            continue
        emo = sum(1 for t in re.findall(r"[a-z']+", text.lower()) if t in EMPHASIS_WORDS)
        score = hook_score(text) + 1.5 * emo + text.count("!") + 0.5 * text.count("?")
        out.append((a, b, text.strip(), score))
    out.sort(key=lambda m: -m[3])
    return out


def best_moment(p: dict, lo: float = 2.0, hi: float = 5.0) -> Optional[tuple[float, float]]:
    """The most quotable caption run after the first few seconds (for a cold open)."""
    ms = moments(p, lo, hi)
    return (ms[0][0], ms[0][1]) if ms and ms[0][3] >= 1.5 else None


def hook_candidates(p: dict, count: int, length: float) -> list[tuple]:
    """Different strong moments (not overlapping) to try as the opening."""
    picked: list[tuple] = []
    for m in moments(p, min(1.5, length), length + 1.5, skip_start=1.0):
        if all(m[1] <= x[0] or m[0] >= x[1] for x in picked):
            picked.append(m)
        if len(picked) >= count:
            break
    return picked


def remove_cold_open(p: dict) -> float:
    """Take out a cold open added earlier. Returns how much the video got shorter."""
    n = 0
    while n < len(p["clips"]) and p["clips"][n].get("cold_open"):
        n += 1
    if not n or n >= len(p["clips"]):
        return 0.0
    length = clip_starts(p["clips"])[n]
    p["clips"] = p["clips"][n:]
    p["clips"][0]["transition"] = None
    for key in ("captions", "effects", "overlays", "audio"):
        keep = []
        for it in p[key]:
            if float(it["start"]) >= length - 0.01:
                it["start"] = round(float(it["start"]) - length, 3)
                it["end"] = round(float(it["end"]) - length, 3)
                if it.get("words"):
                    it["words"] = [{**w, "start": round(w["start"] - length, 3), "end": round(w["end"] - length, 3)}
                                   for w in it["words"]]
                keep.append(it)
            elif it.get("auto") == "hook":
                keep.append(it)
        p[key] = keep
    return round(length, 3)


def cold_open(p: dict, start: Optional[float] = None, end: Optional[float] = None) -> Optional[tuple[float, float]]:
    """Copy a moment to the very start, then flash into the video.

    start/end are times on the timeline without a cold open (one added before is replaced).
    """
    removed = remove_cold_open(p)
    if removed and start is not None and end is not None and start >= removed:
        start, end = start - removed, end - removed
    if start is None or end is None:
        pick = best_moment(p)
        if not pick:
            return None
        start, end = pick
    start, end = max(0.0, float(start)), min(total_duration(p), float(end))
    if end - start < 0.8:
        return None
    pieces = []
    t = start
    while t < end - 0.02:
        loc = locate(p, t)
        if loc is None:
            break
        i, src = loc
        c = p["clips"][i]
        take = min(float(c["out"]) - src, end - t)
        if take <= 0.02:
            t += 0.02
            continue
        pieces.append({**copy.deepcopy(c), "id": new_id("c"), "in": round(src, 3), "out": round(src + take, 3),
                       "transition": None, "cold_open": True})
        t += take
    if not pieces:
        return None
    length = sum(clip_length(c) for c in pieces)
    copies = []
    for k in p["captions"]:
        if k["start"] >= start - 0.01 and k["end"] <= end + 0.3:
            nk = copy.deepcopy(k)
            nk["id"] = new_id("k")
            off = -start
            nk["start"], nk["end"] = round(k["start"] + off, 3), round(min(k["end"], end) + off, 3)
            if nk.get("words"):
                nk["words"] = [{**w, "start": round(w["start"] + off, 3), "end": round(min(w["end"], end) + off, 3)}
                               for w in nk["words"] if w["start"] < end]
            copies.append(nk)
    fx_copies = []  # zooms and bleeps inside the teaser come along
    for e in p["effects"]:
        if e["start"] >= start - 0.01 and e["end"] <= end + 0.01:
            fx_copies.append({**copy.deepcopy(e), "id": new_id("e"), "start": round(e["start"] - start, 3),
                              "end": round(e["end"] - start, 3)})
    flash = 0.3
    first = p["clips"][0]
    first["transition"] = {"type": "fadewhite", "duration": flash}
    p["clips"] = pieces + p["clips"]
    shift = round(length - min(flash, 0.45 * min(clip_length(pieces[-1]), clip_length(first))), 3)
    shift_after(p, 0.0, shift)
    for k in copies:  # the teaser's captions end before the first real caption appears
        k["end"] = min(k["end"], shift)
        if k.get("words"):
            k["words"] = [{**w, "end": min(w["end"], shift)} for w in k["words"] if w["start"] < shift - 0.05]
    p["captions"].extend(k for k in copies if k["end"] - k["start"] > 0.1)
    p["effects"].extend(fx_copies)
    return start, end


# ---------------------------------------------------------------------------
# Optional plan from Claude
# ---------------------------------------------------------------------------

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "hook_title": {"type": "string", "description": "2-6 word on-screen title for the first seconds"},
        "hook_subtitle": {"type": "string"},
        "cold_open": {"type": "object", "properties": {"start": {"type": "number"}, "end": {"type": "number"}},
                      "required": ["start", "end"], "additionalProperties": False},
        "emphasis_times": {"type": "array", "items": {"type": "number"}},
        "callouts": {"type": "array", "items": {
            "type": "object", "properties": {"time": {"type": "number"}, "text": {"type": "string"}},
            "required": ["time", "text"], "additionalProperties": False}},
        "notes": {"type": "string"},
    },
    "required": ["hook_title", "emphasis_times", "callouts", "notes"],
    "additionalProperties": False,
}


def timed_captions(p: dict, limit: int = 300_000) -> str:
    lines = [f"[{k['start']:.2f}-{k['end']:.2f}] {k['text']}" for k in sorted(p["captions"], key=lambda k: k["start"])]
    return "\n".join(lines)[:limit]


def claude_plan(p: dict, cfg: dict, want_cold_open: bool) -> Optional[dict]:
    from cliptool import ai
    if not ai.ai_available() or not p["captions"]:
        return None
    prompt = (
        "Plan the finishing touches for this short vertical video (TikTok / Shorts / Reels). Times are seconds "
        f"on the edited timeline, which is {total_duration(p):.1f}s long.\n\n"
        "- hook_title: a punchy 2-6 word title shown over the first 3 seconds that makes people stay.\n"
        "- emphasis_times: up to one moment every 5 seconds where a quick zoom punch adds energy "
        "(the start time of the strong word).\n"
        "- callouts: short on-screen text (max 4 words, may include one emoji) for calls to action or "
        "key numbers/claims; use sparingly.\n"
        + ("- cold_open: a 2-5 second quotable moment (not from the first 20% of the video) to repeat as a "
           "teaser at the very start.\n" if want_cold_open else "")
        + "- notes: one or two sentences on why.\n\nTranscript:\n" + timed_captions(p))
    try:
        return ai.ask_json(prompt, PLAN_SCHEMA, cfg)
    except Exception as exc:
        log.warning("Claude could not plan the edit (%s); using the built-in rules.", exc)
        return None


def cut_timeline(p: dict, start: float, end: float) -> tuple[dict, float]:
    """Remove a part of the edited video (timeline seconds). Returns (new project, seconds removed)."""
    starts = clip_starts(p["clips"])
    cuts: dict[int, list[Cut]] = {}
    for i, (c, s) in enumerate(zip(p["clips"], starts)):
        e = s + clip_length(c)
        a, b = max(start, s), min(end, e)
        if b - a > 0.01:
            cuts[i] = [Cut(float(c["in"]) + a - s, float(c["in"]) + b - s, "manual")]
    before = total_duration(p)
    new, _ = apply_cuts(p, cuts)
    new = normalize(new)
    return new, round(before - total_duration(new), 3)
