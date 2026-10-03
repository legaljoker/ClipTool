"""Avatar overlays: show a picture of whoever is talking, with the right expression.

Folder layout (one folder per person, inside the 'avatars' folder):

    avatars/
      Dave/
        neutral.png        <- required (or at least one picture)
        happy.png  sad.png  mad.png  angry.png  excited.png  surprised.png  scared.png
        happy_talk.png     <- optional "mouth open" version; alternates while talking
        idle.png           <- optional, shown in 'all' layout while this person is quiet

Map detected speakers to folders in config.yaml (speakers.names: {SPEAKER_1: Dave})
or name folders SPEAKER_1, SPEAKER_2...  Transparent PNGs look best.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from cliptool.emotions import FALLBACKS
from cliptool.ffmpeg_utils import ffmpeg
from cliptool.logger import get_logger
from cliptool.transcribe import Transcript

log = get_logger("avatars")

IMAGE_EXTS = (".png", ".webp", ".jpg", ".jpeg")


@dataclass
class TimelineEntry:
    start: float
    end: float
    speaker: str
    name: str
    expression: str
    text: str = ""


def build_timeline(transcript: Transcript, names: dict[str, str]) -> list[TimelineEntry]:
    out = []
    for s in transcript.segments:
        spk = s.speaker or "SPEAKER_1"
        out.append(TimelineEntry(round(s.start, 3), round(s.end, 3), spk, names.get(spk, spk),
                                 s.emotion or "neutral", s.text.strip()))
    return out


def save_timeline(timeline: list[TimelineEntry], path: Path) -> Path:
    path.write_text(json.dumps([asdict(t) for t in timeline], indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def load_timeline(path: Path) -> list[TimelineEntry]:
    return [TimelineEntry(**d) for d in json.loads(Path(path).read_text(encoding="utf-8"))]


def find_person_folder(avatars_dir: Path, name: str, speaker: str) -> Optional[Path]:
    for candidate in (name, speaker):
        for p in (avatars_dir / candidate, avatars_dir / candidate.replace(" ", "_")):
            if p.is_dir():
                return p
    # case-insensitive match
    if avatars_dir.is_dir():
        for p in avatars_dir.iterdir():
            if p.is_dir() and p.name.lower() in (name.lower(), speaker.lower()):
                return p
    return None


def _find_image(folder: Path, stem: str) -> Optional[Path]:
    for ext in IMAGE_EXTS:
        p = folder / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def pick_image(folder: Path, expression: str, talking: bool) -> Optional[Path]:
    chain = [expression] + FALLBACKS.get(expression, []) + ["neutral"]
    for emo in chain:
        if talking:
            img = _find_image(folder, f"{emo}_talk")
            if img:
                return img
        img = _find_image(folder, emo)
        if img:
            return img
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() in IMAGE_EXTS:
            return p
    return None


class ImageCache:
    """Scales every picture to the same square box (concat needs equal sizes)."""

    def __init__(self, work: Path, size: int):
        self.work = work
        self.size = size
        self.done: dict[tuple, Path] = {}
        work.mkdir(parents=True, exist_ok=True)

    def get(self, img: Optional[Path], dim: bool = False) -> Path:
        key = (str(img), dim)
        if key in self.done:
            return self.done[key]
        out = self.work / f"img_{len(self.done):03d}.png"
        s = self.size
        if img is None:
            ffmpeg(["-f", "lavfi", "-i", f"color=c=black@0.0:s={s}x{s},format=rgba", "-frames:v", "1", out],
                   desc="blank avatar frame")
        else:
            vf = (f"scale={s}:{s}:force_original_aspect_ratio=decrease,format=rgba,"
                  f"pad={s}:{s}:(ow-iw)/2:(oh-ih)/2:color=black@0.0")
            if dim:
                vf += ",colorchannelmixer=aa=0.55"
            ffmpeg(["-i", img, "-vf", vf, "-frames:v", "1", out], desc=f"prepare avatar {img.name}")
        self.done[key] = out
        return out


def _write_concat(entries: list[tuple[Path, float]], path: Path) -> Path:
    lines = ["ffconcat version 1.0"]
    for img, dur in entries:
        if dur <= 0.001:
            continue
        lines += [f"file '{img.as_posix()}'", f"duration {dur:.3f}"]
    if entries:
        lines.append(f"file '{entries[-1][0].as_posix()}'")  # concat demuxer quirk: repeat last
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _talk_frames(start: float, end: float, talk: Path, idle: Path, flap: float) -> list[tuple[Path, float]]:
    if talk == idle or flap <= 0:
        return [(talk, end - start)]
    out, t, on = [], start, True
    while t < end - 1e-6:
        d = min(flap, end - t)
        out.append((talk if on else idle, d))
        t += d
        on = not on
    return out


@dataclass
class AvatarTrack:
    concat_file: Path
    x: int
    y: int


def build_tracks(timeline: list[TimelineEntry], duration: float, width: int, height: int,
                 avatars_dir: Path, cfg: dict, work: Path) -> list[AvatarTrack]:
    size = int(min(width, height) * float(cfg.get("size", 0.3))) // 2 * 2
    margin = int(min(width, height) * float(cfg.get("margin", 0.04)))
    flap = float(cfg.get("talk_flap_seconds", 0.15))
    layout = cfg.get("layout", "active")
    cache = ImageCache(work / "avatar_frames", size)
    blank = cache.get(None)

    people: list[tuple[str, str]] = []
    for t in timeline:
        if (t.speaker, t.name) not in people:
            people.append((t.speaker, t.name))
    folders = {}
    for spk, name in people:
        folder = find_person_folder(avatars_dir, name, spk)
        if folder is None:
            log.warning("No avatar folder for %s (%s) in %s - they will have no picture.", name, spk, avatars_dir)
        folders[spk] = folder
    if not any(folders.values()):
        return []

    def position(slot: int, n_slots: int) -> tuple[int, int]:
        pos = cfg.get("position", "top-left")
        step = size + margin // 2
        horizontal = "left" in pos or "right" in pos
        x = margin if "left" in pos else width - size - margin if "right" in pos else (width - size) // 2
        y = margin if "top" in pos else height - size - margin if "bottom" in pos else (height - size) // 2
        if n_slots > 1:
            if horizontal and "right" in pos:
                x -= slot * step
            elif horizontal:
                x += slot * step
            else:
                x += int((slot - (n_slots - 1) / 2) * step)
        return x, y

    tracks: list[AvatarTrack] = []
    timeline = sorted(timeline, key=lambda t: t.start)

    def entries_for(predicate, idle_for) -> list[tuple[Path, float]]:
        entries: list[tuple[Path, float]] = []
        cursor = 0.0
        for t in timeline:
            if not predicate(t):
                continue
            s, e = max(t.start, cursor), min(t.end, duration)
            if e <= s:
                continue
            if s > cursor:
                if entries and s - cursor < 0.4:  # tiny pause: keep the last picture up
                    entries[-1] = (entries[-1][0], entries[-1][1] + s - cursor)
                else:
                    entries.append((idle_for(None), s - cursor))
            folder = folders.get(t.speaker)
            if folder is None:
                entries.append((idle_for(None), e - s))
            else:
                talk = cache.get(pick_image(folder, t.expression, talking=True))
                still = cache.get(pick_image(folder, t.expression, talking=False))
                entries.extend(_talk_frames(s, e, talk, still, flap))
            cursor = e
        if cursor < duration:
            entries.append((idle_for(None), duration - cursor))
        return entries

    if layout == "all":
        n = len(people)
        for slot, (spk, _name) in enumerate(people):
            folder = folders.get(spk)
            if folder is None:
                continue
            idle_img = _find_image(folder, "idle") or pick_image(folder, "neutral", talking=False)
            idle = cache.get(idle_img, dim=bool(cfg.get("dim_idle", True)))
            entries = entries_for(lambda t, spk=spk: t.speaker == spk, lambda _x, idle=idle: idle)
            x, y = position(slot, n)
            tracks.append(AvatarTrack(_write_concat(entries, work / f"avatar_{spk}.ffconcat"), x, y))
    else:
        entries = entries_for(lambda t: True, lambda _x: blank)
        x, y = position(0, 1)
        tracks.append(AvatarTrack(_write_concat(entries, work / "avatar_active.ffconcat"), x, y))
    return tracks
