"""Speech-to-text with Whisper, plus the Transcript data model used everywhere else."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from cliptool.ffmpeg_utils import fmt_time
from cliptool.logger import get_logger

log = get_logger("transcribe")


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: Optional[str] = None
    emotion: Optional[str] = None

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Transcript:
    segments: list[Segment]
    language: str = ""
    source: str = ""

    # ---- (de)serialisation -------------------------------------------------
    def to_dict(self) -> dict:
        return {"language": self.language, "source": self.source,
                "segments": [asdict(s) for s in self.segments]}

    @classmethod
    def from_dict(cls, data: dict) -> "Transcript":
        segs = []
        for s in data.get("segments", []):
            words = [Word(**w) for w in s.get("words", [])]
            segs.append(Segment(start=s["start"], end=s["end"], text=s["text"], words=words,
                                speaker=s.get("speaker"), emotion=s.get("emotion")))
        return cls(segments=segs, language=data.get("language", ""), source=data.get("source", ""))

    def save_json(self, path: Path) -> Path:
        path.write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load_json(cls, path: Path) -> "Transcript":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # ---- helpers -----------------------------------------------------------
    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    @property
    def duration(self) -> float:
        return self.segments[-1].end if self.segments else 0.0

    def all_words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    def speakers(self) -> list[str]:
        seen: list[str] = []
        for s in self.segments:
            if s.speaker and s.speaker not in seen:
                seen.append(s.speaker)
        return seen

    def slice(self, start: float, end: float, offset: float = 0.0) -> "Transcript":
        """Part of the transcript between start and end, re-timed so `start` becomes `offset`."""
        shift = offset - start
        out: list[Segment] = []
        for seg in self.segments:
            if seg.end <= start or seg.start >= end:
                continue
            words = [Word(max(w.start, start) + shift, min(w.end, end) + shift, w.text, w.prob)
                     for w in seg.words if w.end > start and w.start < end
                     and (min(w.end, end) - max(w.start, start)) >= 0.5 * max(0.01, w.end - w.start)]
            text = "".join(w.text for w in words).strip() if seg.words else seg.text
            if not text:
                continue
            out.append(Segment(max(seg.start, start) + shift, min(seg.end, end) + shift, text,
                               words, seg.speaker, seg.emotion))
        return Transcript(out, self.language, self.source)

    @staticmethod
    def concat(parts: Iterable[tuple["Transcript", float]]) -> "Transcript":
        """Join transcripts; each part is (transcript already starting at 0, offset)."""
        segs: list[Segment] = []
        lang = ""
        for tr, offset in parts:
            lang = lang or tr.language
            for s in tr.segments:
                segs.append(Segment(s.start + offset, s.end + offset, s.text,
                                    [Word(w.start + offset, w.end + offset, w.text, w.prob) for w in s.words],
                                    s.speaker, s.emotion))
        return Transcript(segs, lang, "combined")

    # ---- exports -----------------------------------------------------------
    def to_srt(self) -> str:
        lines = []
        for i, s in enumerate(self.segments, 1):
            who = f"[{s.speaker}] " if s.speaker else ""
            lines += [str(i), f"{_srt_time(s.start)} --> {_srt_time(s.end)}", who + s.text.strip(), ""]
        return "\n".join(lines)

    def to_vtt(self) -> str:
        lines = ["WEBVTT", ""]
        for s in self.segments:
            who = f"<v {s.speaker}>" if s.speaker else ""
            lines += [f"{_srt_time(s.start).replace(',', '.')} --> {_srt_time(s.end).replace(',', '.')}",
                      who + s.text.strip(), ""]
        return "\n".join(lines)

    def to_txt(self) -> str:
        lines = []
        for s in self.segments:
            tags = ", ".join(x for x in (s.speaker, s.emotion) if x)
            tags = f" ({tags})" if tags else ""
            lines.append(f"[{fmt_time(s.start)} - {fmt_time(s.end)}]{tags} {s.text.strip()}")
        return "\n".join(lines)

    def save_all(self, folder: Path, stem: str) -> dict[str, Path]:
        folder.mkdir(parents=True, exist_ok=True)
        out = {
            "json": self.save_json(folder / f"{stem}.json"),
            "srt": folder / f"{stem}.srt",
            "vtt": folder / f"{stem}.vtt",
            "txt": folder / f"{stem}.txt",
        }
        out["srt"].write_text(self.to_srt(), encoding="utf-8")
        out["vtt"].write_text(self.to_vtt(), encoding="utf-8")
        out["txt"].write_text(self.to_txt(), encoding="utf-8")
        return out


def _srt_time(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

def available_engines() -> list[str]:
    engines = []
    try:
        import faster_whisper  # noqa: F401
        engines.append("faster-whisper")
    except ImportError:
        pass
    try:
        import whisper  # noqa: F401
        engines.append("openai-whisper")
    except ImportError:
        pass
    return engines


_model_cache: dict = {}


def _transcribe_faster_whisper(path: Path, cfg: dict) -> Transcript:
    from faster_whisper import WhisperModel

    key = ("fw", cfg["model"], cfg["device"], cfg["compute_type"])
    if key not in _model_cache:
        log.info("Loading Whisper model '%s' (first time downloads it, please wait)...", cfg["model"])
        _model_cache[key] = WhisperModel(cfg["model"], device=cfg["device"], compute_type=cfg["compute_type"])
    model = _model_cache[key]
    seg_iter, info = model.transcribe(str(path), language=cfg.get("language") or None,
                                      word_timestamps=True, vad_filter=True)
    segments = []
    for s in seg_iter:
        words = [Word(float(w.start), float(w.end), w.word, float(getattr(w, "probability", 1.0)))
                 for w in (s.words or [])]
        segments.append(Segment(float(s.start), float(s.end), s.text.strip(), words))
        log.debug("  %s  %s", fmt_time(s.start), s.text.strip())
    return Transcript(segments, info.language or "", str(path))


def _transcribe_openai_whisper(path: Path, cfg: dict) -> Transcript:
    import whisper

    key = ("ow", cfg["model"])
    if key not in _model_cache:
        log.info("Loading Whisper model '%s'...", cfg["model"])
        _model_cache[key] = whisper.load_model(cfg["model"])
    result = _model_cache[key].transcribe(str(path), language=cfg.get("language") or None,
                                          word_timestamps=True)
    segments = []
    for s in result.get("segments", []):
        words = [Word(float(w["start"]), float(w["end"]), w["word"], float(w.get("probability", 1.0)))
                 for w in s.get("words", [])]
        segments.append(Segment(float(s["start"]), float(s["end"]), s["text"].strip(), words))
    return Transcript(segments, result.get("language", ""), str(path))


def file_fingerprint(path: Path, extra: str = "") -> str:
    st = Path(path).stat()
    raw = f"{Path(path).resolve()}|{st.st_size}|{int(st.st_mtime)}|{extra}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def transcribe(path: Path, cfg: dict, cache_dir: Optional[Path] = None) -> Transcript:
    """Transcribe a video/audio file. Results are cached so re-runs are instant."""
    cache_file = None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        fp = file_fingerprint(path, f"{cfg.get('model')}|{cfg.get('language')}")
        cache_file = cache_dir / f"{Path(path).stem}_{fp}.transcript.json"
        if cache_file.exists():
            log.info("Using cached transcript for %s", Path(path).name)
            return Transcript.load_json(cache_file)

    engine = cfg.get("engine", "auto")
    engines = available_engines()
    if engine == "auto":
        if not engines:
            raise RuntimeError("No transcription engine installed. Run:  pip install faster-whisper")
        engine = engines[0]
    log.info("Transcribing %s with %s (%s model)...", Path(path).name, engine, cfg.get("model"))
    if engine == "faster-whisper":
        tr = _transcribe_faster_whisper(path, cfg)
    elif engine == "openai-whisper":
        tr = _transcribe_openai_whisper(path, cfg)
    else:
        raise ValueError(f"Unknown transcription engine: {engine}")
    log.info("  -> %d segments, language '%s'", len(tr.segments), tr.language)
    if cache_file:
        tr.save_json(cache_file)
    return tr
