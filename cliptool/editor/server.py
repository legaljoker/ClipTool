"""Local web server for the ClipTool editor (opens in your browser, only reachable from this PC)."""
from __future__ import annotations

import copy
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

from cliptool import __version__, settings
from cliptool.config import Config
from cliptool.editor import render
from cliptool.editor.project import (ProjectStore, clip_starts, find, media_path, meta, new_id, normalize,
                                     total_duration)
from cliptool.ffmpeg_utils import (VIDEO_EXTENSIONS, FFmpegError, ffmpeg, list_videos, probe)
from cliptool.logger import get_logger

log = get_logger("editor")

STATIC = Path(__file__).parent / "static"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"}
BROWSER_FRIENDLY = {".mp4", ".m4v", ".mov", ".webm"}
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


# ---------------------------------------------------------------------------
# Background jobs (render, transcribe, proxies)
# ---------------------------------------------------------------------------

class Job:
    def __init__(self, kind: str, project: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.project = project
        self.status = "running"
        self.progress = 0.0
        self.message = "Starting..."
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.cancel = threading.Event()
        self.started = time.time()

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "project": self.project, "status": self.status,
                "progress": round(self.progress, 3), "message": self.message, "result": self.result,
                "error": self.error, "elapsed": round(time.time() - self.started, 1)}


class Jobs:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def start(self, kind: str, project: str, fn: Callable[[Job], Optional[dict]]) -> Job:
        job = Job(kind, project)
        with self.lock:
            self.jobs[job.id] = job

        def runner():
            try:
                job.result = fn(job) or {}
                job.status = "done"
                job.progress = 1.0
                job.message = job.message if job.message != "Starting..." else "Done"
            except Exception as exc:  # report to the UI
                job.status = "cancelled" if job.cancel.is_set() else "error"
                job.error = str(exc)
                job.message = "Cancelled" if job.cancel.is_set() else f"Failed: {exc}"
                log.error("%s job failed: %s", kind, exc)
                log.debug(traceback.format_exc())

        threading.Thread(target=runner, daemon=True).start()
        return job

    def get(self, jid: str) -> Optional[Job]:
        return self.jobs.get(jid)


# ---------------------------------------------------------------------------
# App logic
# ---------------------------------------------------------------------------

class EditorApp:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = ProjectStore(cfg.resolve_dir("projects_dir"))
        self.jobs = Jobs()
        self.locks: dict[str, threading.Lock] = {}

    def lock(self, pid: str) -> threading.Lock:
        return self.locks.setdefault(pid, threading.Lock())

    # ---- media ---------------------------------------------------------
    def add_media(self, pid: str, path: Path, rel: Optional[str] = None, recording: bool = False) -> dict:
        if recording:
            path = self._fix_recording(path)
            rel = f"media/{path.name}"
        info = probe(path)
        if not info.has_video and not info.has_audio:
            raise ValueError(f"{path.name} has no video or sound")
        kind = "video" if info.has_video else "audio"
        mid = new_id("m")
        d = self.store.dir(pid)
        thumb = d / "cache" / f"{mid}.jpg"
        try:
            if kind == "video":
                ffmpeg(["-ss", f"{min(1.0, info.duration / 3):.2f}", "-i", path, "-frames:v", "1",
                        "-vf", "scale=240:-2", "-q:v", "4", thumb], desc="thumbnail")
            else:  # waveform picture for the timeline
                thumb = thumb.with_suffix(".png")
                ffmpeg(["-i", path, "-filter_complex", "aformat=channel_layouts=mono,showwavespic=s=1200x80:colors=#2bd9c5",
                        "-frames:v", "1", thumb], desc="waveform")
        except FFmpegError:
            thumb = None
        entry = {"id": mid, "kind": kind, "name": path.name, "path": rel or str(path.resolve()),
                 "duration": round(info.duration, 3), "width": info.width, "height": info.height,
                 "has_audio": info.has_audio, "fps": round(info.fps, 2),
                 "alpha": kind == "video" and not recording and _has_alpha(path),
                 "thumb": f"cache/{thumb.name}" if thumb else None, "proxy": None, "recording": recording}
        with self.lock(pid):
            p = self.store.load(pid)
            p["media"].append(entry)
            self.store.save(pid, p)
        if kind == "video" and not entry["alpha"] and (path.suffix.lower() not in BROWSER_FRIENDLY
                                                         or self._needs_proxy(path)):
            job = self.jobs.start("proxy", pid, lambda job: self._make_proxy(pid, mid, path, job))
            return {**entry, "proxy_job": job.id}
        return entry

    @staticmethod
    def _fix_recording(raw: Path) -> Path:
        """Browser recordings have no length/seek index; rewrite them so players and ffmpeg can seek."""
        fixed = _unique(raw.with_name(raw.stem + "_rec.webm"))
        try:
            ffmpeg(["-fflags", "+genpts", "-i", raw, "-c", "copy", fixed], desc="fix recording")
        except FFmpegError:
            ffmpeg(["-i", raw, "-c:v", "libvpx-vp9", "-b:v", "2M", "-deadline", "realtime", "-cpu-used", "8",
                    "-c:a", "libopus", fixed], desc="re-encode recording")
        raw.unlink(missing_ok=True)
        return fixed

    @staticmethod
    def _needs_proxy(path: Path) -> bool:
        try:
            proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=codec_name", "-of", "csv=p=0", str(path)],
                                  capture_output=True, text=True)
            return proc.stdout.strip() not in ("h264", "vp8", "vp9", "av1")
        except OSError:
            return False

    def _make_proxy(self, pid: str, mid: str, path: Path, job: Job) -> dict:
        job.message = f"Making a preview copy of {path.name}..."
        rel = f"cache/{mid}_proxy.mp4"
        ffmpeg(["-i", path, "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                self.store.dir(pid) / rel], desc="preview proxy")
        with self.lock(pid):
            p = self.store.load(pid)
            m = find(p["media"], mid)
            if m:
                m["proxy"] = rel
                self.store.save(pid, p)
        return {"media": mid, "proxy": rel}

    # ---- transcription ---------------------------------------------------
    def transcribe(self, pid: str, opts: dict, job: Job) -> dict:
        from cliptool import emotions, speakers
        from cliptool.ffmpeg_utils import extract_audio, read_wav
        from cliptool.transcribe import transcribe

        p = self.store.load(pid)
        if not p["clips"]:
            raise ValueError("Add a clip to the timeline first.")
        cache = self.cfg.resolve_dir("output_dir") / ".transcript_cache"
        work = self.store.dir(pid) / "cache"
        media_by_id = {m["id"]: m for m in p["media"]}
        starts = clip_starts(p["clips"])
        per_media: dict = {}
        captions: list[dict] = []
        speaker_ids: list[str] = []
        for n, (clip, start) in enumerate(zip(p["clips"], starts)):
            m = media_by_id[clip["media"]]
            src = media_path(self.store, pid, m)
            job.message = f"Transcribing clip {n + 1} of {len(p['clips'])} ({m['name']})..."
            job.progress = n / max(1, len(p["clips"]))
            if m["id"] not in per_media:
                tr = transcribe(src, self.cfg["transcription"], cache)
                if (opts.get("speakers") or opts.get("expressions")) and m.get("has_audio", True):
                    wav = extract_audio(src, work / f"{m['id']}.wav")
                    audio = read_wav(wav)
                    if opts.get("speakers"):
                        job.message = f"Working out who is talking in {m['name']}..."
                        scfg = dict(self.cfg["speakers"])
                        if opts.get("num_speakers"):
                            scfg["num_speakers"] = int(opts["num_speakers"])
                        speakers.identify_speakers(tr, wav, *audio, scfg)
                    if opts.get("expressions"):
                        emotions.tag_expressions(tr, self.cfg["emotions"], audio)
                per_media[m["id"]] = tr
            part = per_media[m["id"]].slice(float(clip["in"]), float(clip["out"]), offset=start)
            for seg in part.segments:
                for chunk in _split_segment(seg):
                    if chunk["speaker"] and chunk["speaker"] not in speaker_ids:
                        speaker_ids.append(chunk["speaker"])
                    captions.append(chunk)
        job.message = "Creating captions..."
        with self.lock(pid):
            p = self.store.load(pid)
            # SPEAKER_1 -> Person 1, creating people as needed
            mapping = {}
            for i, spk in enumerate(speaker_ids):
                while len(p["people"]) <= i:
                    k = len(p["people"])
                    p["people"].append({"id": new_id("p"), "name": f"Person {k + 1}",
                                        "color": meta()["person_colors"][k % 6], "avatars": {}})
                mapping[spk] = p["people"][i]["id"]
            if opts.get("replace", True):
                p["captions"] = []
            for c in captions:
                p["captions"].append({"id": new_id("k"), "start": c["start"], "end": c["end"], "text": c["text"],
                                      "words": c["words"], "person": mapping.get(c["speaker"]),
                                      "expression": c["expression"] or "neutral"})
            if not opts.get("speakers") and len(p["people"]) == 1:
                for c in p["captions"]:
                    c["person"] = c["person"] or p["people"][0]["id"]
            self.store.save(pid, p)
        job.message = f"Added {len(captions)} captions."
        return {"captions": len(captions), "people": len(speaker_ids)}

    # ---- Auto Edit -------------------------------------------------------
    def media_analysis(self, p: dict, pid: str, job: Job, envelopes: bool = True) -> tuple[dict, dict]:
        """Speech-to-text (cached) and loudness for every video used on the timeline."""
        from cliptool.editor.autoedit import Envelope
        from cliptool.ffmpeg_utils import decode_audio
        from cliptool.transcribe import transcribe

        cache = self.cfg.resolve_dir("output_dir") / ".transcript_cache"
        media = {m["id"]: m for m in p["media"]}
        used = [mid for mid in dict.fromkeys(c["media"] for c in p["clips"]) if media[mid].get("has_audio", True)]
        trs, envs = {}, {}
        for n, mid in enumerate(used):
            if job.cancel.is_set():
                raise RuntimeError("Cancelled")
            src = media_path(self.store, pid, media[mid])
            job.message = f"Listening to {media[mid]['name']} ({n + 1} of {len(used)})..."
            job.progress = 0.05 + 0.5 * n / max(1, len(used))
            trs[mid] = transcribe(src, self.cfg["transcription"], cache)
            if envelopes:
                envs[mid] = Envelope.from_samples(decode_audio(src))
        return trs, envs

    def auto_edit(self, pid: str, opts: dict, job: Job) -> dict:
        from cliptool import ai
        from cliptool.editor import autoedit as ae
        from cliptool.editor import sfx

        pr = settings.prefs()
        o = {"tighten": True, "fillers": pr["remove_fillers"], "restarts": pr["remove_restarts"],
             "keep_pause": pr["tighten_keep_pause"], "min_gap": pr["tighten_min_gap"], "captions": True,
             "speakers": False, "punch_ins": pr["punch_ins"], "emphasis": True, "hook_title": True,
             "hook_text": "", "callouts": True, "bleep": pr["bleep_swears"], "cold_open": False, "sfx": True,
             "music": "", "use_ai": True, **opts}
        p = self.store.load(pid)
        if not p["clips"]:
            raise ValueError("Add a video to the timeline first.")
        report: list[str] = []
        before = total_duration(p)
        if o["tighten"]:
            trs, envs = self.media_analysis(p, pid, job)
            job.message, job.progress = "Cutting pauses and filler words...", 0.6
            p, st = ae.tighten(p, trs, envs, o)
            parts = [f"{st['pause'] + st['start'] + st['end']} pauses"]
            if o["fillers"]:
                parts.append(f"{st['filler']} um/uh")
            if o["restarts"]:
                parts.append(f"{st['restart']} repeats/false starts")
            report.append(f"✂️ Tightened: removed {st['seconds']:.1f}s ({', '.join(parts)}). "
                          f"{st['before']:.1f}s → {st['after']:.1f}s.")
        with self.lock(pid):
            self.store.save(pid, p)
        if o["captions"] and not p["captions"]:
            job.message, job.progress = "Writing captions...", 0.7
            res = self.transcribe(pid, {"speakers": o["speakers"], "expressions": True}, job)
            report.append(f"💬 Added {res['captions']} captions.")
            p = self.store.load(pid)
        plan = None
        if o["use_ai"] and ai.ai_available() and p["captions"] and (o["hook_title"] or o["emphasis"] or o["cold_open"]):
            job.message, job.progress = "Claude is planning the edit...", 0.8
            plan = ae.claude_plan(p, self.cfg["ai"], bool(o["cold_open"]))
            if plan:
                report.append("🤖 Claude planned the title, zooms and callouts." +
                              (f" {plan.get('notes', '')}" if plan.get("notes") else ""))
        job.message, job.progress = "Adding zooms, titles and sounds...", 0.9
        if o["punch_ins"]:
            n = ae.punch_ins(p)
            if n:
                report.append(f"🔍 {n} punch-in zooms hide the jump cuts.")
        if o["emphasis"]:
            n = ae.emphasis(p, picks=(plan or {}).get("emphasis_times") if plan else None)
            if n:
                report.append(f"💥 {n} zoom punches on strong words.")
        added_callouts = []
        if o["callouts"]:
            added_callouts = ae.callouts(p)
            for c in (plan or {}).get("callouts", [])[:4]:
                t = float(c.get("time", 0))
                if any(abs(x["start"] - t) < 3 for x in added_callouts):
                    continue
                item = {"id": new_id("o"), "type": "text", "style": "highlighter", "text": str(c["text"])[:40],
                        "x": 0.5, "y": 0.36, "size": 70, "anim_in": "pop", "anim_out": "fade", "anim_dur": 0.3,
                        "start": round(t, 3), "end": round(t + 2.5, 3), "auto": "callout"}
                p["overlays"].append(item)
                added_callouts.append(item)
            if added_callouts:
                report.append(f"📣 {len(added_callouts)} call-out texts: " +
                              ", ".join(x["text"] for x in added_callouts) + ".")
        if o["bleep"]:
            n = ae.bleeps(p)
            report.append(f"🙊 Bleeped {n} swear words." if n else "🙊 No swear words found.")
        cold = None
        if o["cold_open"]:
            rng = (plan or {}).get("cold_open") or {}
            cold = ae.cold_open(p, rng.get("start"), rng.get("end"))
            report.append(f"⏪ Cold open: the moment at {cold[0]:.1f}-{cold[1]:.1f}s now also plays first."
                          if cold else "⏪ No strong moment found for a cold open.")
        title = None
        if o["hook_title"]:
            text = o["hook_text"] or (plan or {}).get("hook_title") or None
            title = ae.hook_title(p, text, (plan or {}).get("hook_subtitle", "") if not o["hook_text"] else "")
            if title:
                report.append(f"🏷️ Hook title: \"{title['text']}\".")
        if o["sfx"]:
            p["audio"] = [a for a in p["audio"] if a.get("auto") != "sfx"]
            n = 0
            if title:
                sfx.add_sound(p, self.store, pid, "whoosh", 0.05, 0.45, "sfx", anchor="start")
                n += 1
            if cold:
                starts = clip_starts(p["clips"])
                idx = next((i for i, c in enumerate(p["clips"]) if not c.get("cold_open")), None)
                if idx:
                    sfx.add_sound(p, self.store, pid, "whoosh", starts[idx], 0.5, "sfx", anchor="peak")
                    n += 1
            for c in added_callouts:
                sfx.add_sound(p, self.store, pid, "pop", c["start"], 0.5, "sfx")
                n += 1
            for e in p["effects"]:
                if e.get("auto") == "emphasis":
                    sfx.add_sound(p, self.store, pid, "swoosh_fast", e["start"], 0.25, "sfx")
                    n += 1
            if n:
                report.append(f"🔊 {n} sound effects on the title, zooms and call-outs.")
        if o["music"]:
            m = find(p["media"], o["music"])
            if m:
                total = total_duration(p)
                p["audio"] = [a for a in p["audio"] if a.get("auto") != "music"]
                length = min(float(m["duration"]), total)
                p["audio"].append({"id": new_id("a"), "media": m["id"], "name": m["name"], "in": 0.0, "out": length,
                                   "start": 0.0, "end": length, "volume": pr["music_volume"], "fade_in": 0.5,
                                   "fade_out": 1.5, "duck": True, "auto": "music"})
                report.append("🎵 Background music added; it dips automatically while someone talks.")
        with self.lock(pid):
            self.store.save(pid, p)
        after = total_duration(self.store.load(pid))
        report.append(f"Length: {before:.1f}s → {after:.1f}s.")
        (self.store.dir(pid) / "auto_edit_report.txt").write_text(
            "ClipTool Auto Edit - " + datetime.now().strftime("%Y-%m-%d %H:%M") + "\n\n" + "\n".join(report) + "\n",
            encoding="utf-8")
        job.message = "Auto Edit finished."
        return {"report": report, "plan": plan}

    def generate_sound(self, pid: str, opts: dict, job: Job) -> dict:
        from cliptool.editor import sfx
        kind = "music" if opts.get("kind") == "music" else "sfx"
        job.message = "Composing music (this can take a minute)..." if kind == "music" else "Creating the sound..."
        f = sfx.generate(kind, opts.get("prompt", ""), float(opts.get("seconds") or (30 if kind == "music" else 2)),
                         self.store.dir(pid) / "media")
        entry = self.add_media(pid, f, f"media/{f.name}")
        return {"media": entry}

    # ---- checks and versions ---------------------------------------------
    def export_path(self, pid: str, p: dict, suffix: str = "") -> Path:
        """File name from the pattern in Settings, e.g. {project}_{preset}_{date}."""
        pattern = settings.prefs()["export_name_pattern"] or "{project}_{preset}_{date}"
        now = datetime.now()
        folder = self.store.dir(pid) / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        for n in range(1, 1000):
            try:
                name = pattern.format(project=p["name"], preset=p["preset"], date=now.strftime("%Y-%m-%d"),
                                      time=now.strftime("%H-%M-%S"), n=n)
            except (KeyError, IndexError, ValueError):
                name = f"{p['name']}_{p['preset']}_{now.strftime('%Y-%m-%d_%H-%M-%S')}"
            name = re.sub(r"[^A-Za-z0-9_\-]+", "_", name + suffix).strip("_") or "video"
            out = folder / f"{name}.mp4"
            if not out.exists():
                return out
            if "{n}" not in pattern:
                pattern += "_{n}"
        return folder / f"video_{uuid.uuid4().hex[:6]}.mp4"

    def qa(self, pid: str, job: Job) -> dict:
        from cliptool.editor import qa
        p = self.store.load(pid)
        exports = sorted((self.store.dir(pid) / "exports").glob("*.mp4"), key=lambda f: f.stat().st_mtime)
        saved = (self.store.dir(pid) / "project.json").stat().st_mtime
        video = exports[-1] if exports and exports[-1].stat().st_mtime > saved else None
        if video is None and p["clips"]:
            job.message = "Making a quick test render..."
            video = self.store.dir(pid) / "cache" / "qa_check.mp4"
            work = self.store.dir(pid) / "cache" / f"qa_{job.id}"

            def prog(f: float):
                job.progress = 0.8 * f
                job.message = f"Making a quick test render... {int(f * 100)}%"
            try:
                render.render_video(p, self.store, pid, video, work, "draft", prog, job.cancel)
            finally:
                shutil.rmtree(work, ignore_errors=True)
        job.message, job.progress = "Checking picture, sound and captions...", 0.85
        res = qa.run(p, self.store, pid, video)
        job.message = f"{len(res['issues'])} things to look at" if res["issues"] else "No problems found"
        return res

    def hook_versions(self, pid: str, opts: dict, job: Job) -> dict:
        from cliptool.editor import autoedit as ae
        p = self.store.load(pid)
        if not p["captions"]:
            raise ValueError("Add captions first (Captions tab or Auto Edit): hooks are picked from what is said.")
        ae.remove_cold_open(p)  # each version gets its own teaser
        count = max(2, min(6, int(opts.get("count", 3))))
        length = max(1.5, min(8.0, float(opts.get("length", 3))))
        cands = ae.hook_candidates(p, count, length)
        if not cands:
            raise ValueError("No strong moments found to use as hooks.")
        files = []
        for n, (a, b, text, _) in enumerate(cands):
            if job.cancel.is_set():
                raise RuntimeError("Cancelled")
            q = copy.deepcopy(p)
            ae.cold_open(q, a, b)
            out = self.export_path(pid, q, f"_hook{n + 1}")
            work = self.store.dir(pid) / "cache" / f"hook_{job.id}_{n}"

            def prog(f: float, n=n):
                job.progress = (n + f) / len(cands)
                job.message = f"Rendering hook version {n + 1} of {len(cands)}... {int(f * 100)}%"
            try:
                render.render_video(q, self.store, pid, out, work, opts.get("quality", "high"), prog, job.cancel)
            finally:
                shutil.rmtree(work, ignore_errors=True)
            files.append({"name": out.name, "hook": text, "start": a, "end": b})
        lines = [f"{f['name']}: opens with “{f['hook']}” (from {f['start']:.1f}s)" for f in files]
        (self.store.dir(pid) / "exports" / "hook_versions.txt").write_text(
            "Hook versions - post each one and keep the opening that holds viewers longest.\n\n" +
            "\n".join(lines) + "\n", encoding="utf-8")
        job.message = f"Made {len(files)} versions."
        return {"files": files}

    # ---- rendering -------------------------------------------------------
    def render(self, pid: str, opts: dict, job: Job) -> dict:
        p = self.store.load(pid)
        out = self.export_path(pid, p)
        work = self.store.dir(pid) / "cache" / f"render_{job.id}"
        job.message = "Rendering..."

        def prog(f: float):
            job.progress = f
            job.message = f"Rendering... {int(f * 100)}%"

        try:
            render.render_video(p, self.store, pid, out, work, opts.get("quality", "high"), prog, job.cancel)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        job.message = f"Saved {out.name}"
        return {"file": f"exports/{out.name}", "name": out.name, "folder": str(out.parent)}

    def frame(self, pid: str, t: float) -> Path:
        p = self.store.load(pid)
        work = self.store.dir(pid) / "cache" / "frame"
        out = self.store.dir(pid) / "cache" / "preview_frame.jpg"
        return render.render_frame(p, self.store, pid, t, out, work)


def _split_segment(seg) -> list[dict]:
    """Break a transcript segment into caption-sized chunks (about one breath each)."""
    words = [w for w in seg.words if w.text.strip()]
    if not words:
        return [{"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text.strip(), "words": None,
                 "speaker": seg.speaker, "expression": seg.emotion}]
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= 8 or (w.text.strip()[-1:] in ".?!" and len(cur) >= 3) or cur[-1].end - cur[0].start > 3.5:
            chunks.append(cur)
            cur = []
    if cur:
        if chunks and len(cur) <= 2:
            chunks[-1].extend(cur)
        else:
            chunks.append(cur)
    out = []
    for ch in chunks:
        out.append({"start": round(ch[0].start, 3), "end": round(ch[-1].end, 3),
                    "text": " ".join(w.text.strip() for w in ch),
                    "words": [{"start": round(w.start, 3), "end": round(w.end, 3), "text": w.text.strip()}
                              for w in ch],
                    "speaker": seg.speaker, "expression": seg.emotion})
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    app: EditorApp
    server_version = f"ClipToolEditor/{__version__}"

    def log_message(self, fmt, *args):  # keep the console quiet
        log.debug("http: " + fmt, *args)

    # ---- helpers ---------------------------------------------------------
    def send_json(self, data, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str):
        self.send_json({"error": message}, status)

    def read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def save_body(self, dest: Path) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            remaining = n
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)

    def send_file(self, path: Path, cache: bool = False):
        if not path.is_file():
            return self.send_error_json(404, "not found")
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):  # suffix range
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-cache")
        self.end_headers()
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = fh.read(min(1 << 18, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser stopped loading (normal while scrubbing video)

    # ---- routing ---------------------------------------------------------
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method: str):
        url = urlparse(self.path)
        parts = [unquote(x) for x in url.path.strip("/").split("/") if x]
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            # Only the editor itself may call this server, not other web pages open in a browser.
            return self.send_error_json(403, "forbidden origin")
        try:
            if method == "GET" and (not parts or parts[0] in ("index.html",)):
                return self.send_file(STATIC / "index.html")
            if method == "GET" and parts[0] == "static":
                f = (STATIC / "/".join(parts[1:])).resolve()
                if STATIC.resolve() not in f.parents:
                    return self.send_error_json(403, "forbidden")
                return self.send_file(f)
            if parts[0] == "files" and method == "GET" and len(parts) >= 3:
                return self.send_file(self.app.store.resolve(parts[1], "/".join(parts[2:])))
            if parts[0] == "api":
                return self.api(method, parts[1:], q)
            self.send_error_json(404, "not found")
        except FileNotFoundError as exc:
            self.send_error_json(404, str(exc))
        except (ValueError, KeyError) as exc:
            self.send_error_json(400, str(exc))
        except Exception as exc:
            log.error("Editor error: %s", exc)
            log.debug(traceback.format_exc())
            self.send_error_json(500, str(exc))

    def api(self, method: str, parts: list[str], q: dict):
        app = self.app
        store = app.store
        if parts == ["changelog"]:
            from cliptool.config import RESOURCE_ROOT
            f = RESOURCE_ROOT / "CHANGELOG.md"
            return self.send_json({"text": f.read_text(encoding="utf-8") if f.exists() else "", "version": __version__})
        if parts == ["meta"]:
            from cliptool.editor import sfx
            return self.send_json({**meta(), "version": __version__, "uitest": bool(os.environ.get("CLIPTOOL_UITEST")),
                                   "sfx": sfx.meta(), "ai": bool(settings.get_key("anthropic")),
                                   "eleven": bool(settings.get_key("elevenlabs")), "settings": settings.prefs()})
        if len(parts) == 2 and parts[0] == "sfx" and method == "GET":
            from cliptool.editor import sfx
            return self.send_file(sfx.library_file(Path(parts[1]).stem))
        if parts == ["uitest"] and method == "POST" and os.environ.get("CLIPTOOL_UITEST"):
            # automated typing test of the Windows build: the page reports what it sees
            out = app.cfg.resolve_dir("log_dir") / "uitest.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(self.read_json()), encoding="utf-8")
            return self.send_json({"ok": True})
        if parts == ["settings"]:
            if method == "PUT":
                view = settings.update(self.read_json())
                settings.apply_to_config(app.cfg)
                return self.send_json(view)
            view = settings.public_view()
            view["data_folder"] = str(_data_folder())
            return self.send_json(view)
        if parts == ["settings", "test"] and method == "POST":
            body = self.read_json()
            ok, msg = settings.test_key(body.get("service", ""), body.get("key"))
            return self.send_json({"ok": ok, "message": msg})
        if parts == ["settings", "open-folder"] and method == "POST":
            _open_folder(_data_folder())
            return self.send_json({"ok": True})
        if parts == ["input-files"]:
            folder = app.cfg.resolve_dir("input_dir")
            folder.mkdir(parents=True, exist_ok=True)
            return self.send_json([{"name": f.name, "path": str(f), "size": f.stat().st_size}
                                   for f in list_videos(folder)])
        if parts == ["projects"]:
            if method == "GET":
                return self.send_json(store.list())
            body = self.read_json()
            pid, proj = store.create(body.get("name") or "My video")
            return self.send_json({"id": pid, "project": proj})
        if parts and parts[0] == "jobs" and len(parts) == 2:
            job = app.jobs.get(parts[1])
            if not job:
                return self.send_error_json(404, "no such job")
            return self.send_json(job.to_dict())
        if parts and parts[0] == "jobs" and len(parts) == 3 and parts[2] == "cancel":
            job = app.jobs.get(parts[1])
            if job:
                job.cancel.set()
            return self.send_json({"ok": True})
        if len(parts) >= 2 and parts[0] == "projects":
            pid = parts[1]
            store.dir(pid)  # validates id
            rest = parts[2:]
            if not rest:
                if method == "GET":
                    return self.send_json(store.load(pid))
                if method == "PUT":
                    with app.lock(pid):
                        saved = store.save(pid, normalize(self.read_json()))
                    return self.send_json({"ok": True, "duration": total_duration(saved)})
                if method == "DELETE":
                    with app.lock(pid):
                        try:
                            store.delete(pid)
                        except PermissionError as exc:
                            raise ValueError("a file in this project is still in use; close it and try again") from exc
                    return self.send_json({"ok": True})
            if rest == ["rename"] and method == "POST":
                with app.lock(pid):
                    saved = store.rename(pid, self.read_json().get("name", ""))
                return self.send_json({"ok": True, "name": saved["name"]})
            if rest == ["media"] and method == "POST":
                name = Path(q.get("name") or "upload.mp4").name
                if Path(name).suffix.lower() not in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
                    return self.send_error_json(400, f"{name} is not a video or audio file")
                dest = _unique(store.dir(pid) / "media" / _clean_name(name))
                self.save_body(dest)
                try:
                    entry = app.add_media(pid, dest, f"media/{dest.name}", recording=q.get("recording") == "1")
                except Exception:
                    dest.unlink(missing_ok=True)
                    raise
                return self.send_json(entry)
            if len(rest) == 2 and rest[0] == "media" and method == "GET":
                m = find(store.load(pid)["media"], rest[1])
                if not m:
                    return self.send_error_json(404, "no such media")
                if m.get("proxy") and q.get("v") == "proxy":
                    return self.send_file(store.resolve(pid, m["proxy"]))
                return self.send_file(media_path(store, pid, m))
            if rest == ["media-path"] and method == "POST":
                path = Path(self.read_json().get("path", ""))
                if not path.is_file():
                    return self.send_error_json(400, "file not found")
                return self.send_json(app.add_media(pid, path))
            if rest == ["image"] and method == "POST":
                name = Path(q.get("name") or "image.png").name
                if Path(name).suffix.lower() not in IMAGE_EXTENSIONS:
                    return self.send_error_json(400, f"{name} is not an image (png, jpg, webp)")
                folder = q.get("folder", "media")
                if folder.startswith("avatars/"):
                    if not re.fullmatch(r"avatars/[A-Za-z0-9_\-]+", folder):
                        return self.send_error_json(400, "bad folder")
                    dest = store.dir(pid) / folder / _clean_name(name)
                else:
                    dest = _unique(store.dir(pid) / "media" / _clean_name(name))
                self.save_body(dest)
                return self.send_json({"path": dest.relative_to(store.dir(pid)).as_posix(),
                                       "v": int(time.time() * 1000)})
            if rest == ["transcribe"] and method == "POST":
                opts = self.read_json()
                job = app.jobs.start("transcribe", pid, lambda j: app.transcribe(pid, opts, j))
                return self.send_json(job.to_dict())
            if rest == ["autoedit"] and method == "POST":
                opts = self.read_json()
                job = app.jobs.start("autoedit", pid, lambda j: app.auto_edit(pid, opts, j))
                return self.send_json(job.to_dict())
            if rest == ["generate-sound"] and method == "POST":
                opts = self.read_json()
                job = app.jobs.start("generate", pid, lambda j: app.generate_sound(pid, opts, j))
                return self.send_json(job.to_dict())
            if rest == ["sfx"] and method == "POST":
                from cliptool.editor import sfx
                body = self.read_json()
                with app.lock(pid):
                    p = store.load(pid)
                    item = sfx.add_sound(p, store, pid, body["name"], float(body.get("t", 0)),
                                         float(body.get("volume", 0.6)))
                    store.save(pid, p)
                return self.send_json({"ok": True, "item": item})
            if rest == ["agent"] and method == "POST":
                from cliptool.editor import agent
                body = self.read_json()
                job = app.jobs.start("agent", pid, lambda j: agent.run_turn(app, pid, str(body.get("message", "")), j,
                                                                            float(body.get("time") or 0)))
                return self.send_json(job.to_dict())
            if rest == ["agent"] and method == "GET":
                from cliptool.editor import agent
                return self.send_json(agent.load_history(app, pid))
            if rest == ["agent", "reset"] and method == "POST":
                from cliptool.editor import agent
                agent.history_file(app, pid).unlink(missing_ok=True)
                return self.send_json({"ok": True})
            if rest == ["copy-style"] and method == "POST":
                src = store.load(self.read_json()["from"])
                with app.lock(pid):
                    p = store.load(pid)
                    p["caption_style"] = copy.deepcopy(src["caption_style"])
                    p["frame"] = copy.deepcopy(src["frame"])
                    p["preset"] = src["preset"]
                    titles = [o for o in src["overlays"] if o.get("type") == "title"]
                    texts = [o for o in src["overlays"] if o.get("type") == "text"]
                    for o in p["overlays"]:  # titles and texts take the other project's look
                        model = titles[0] if o.get("type") == "title" and titles else \
                            texts[0] if o.get("type") == "text" and texts else None
                        if model:
                            for f in ("template", "style", "font", "color", "accent", "size", "anim_in", "anim_out"):
                                if f in model:
                                    o[f] = model[f]
                    store.save(pid, p)
                return self.send_json({"ok": True})
            if rest == ["qa"] and method == "POST":
                job = app.jobs.start("qa", pid, lambda j: app.qa(pid, j))
                return self.send_json(job.to_dict())
            if rest == ["hook-versions"] and method == "POST":
                opts = self.read_json()
                job = app.jobs.start("hooks", pid, lambda j: app.hook_versions(pid, opts, j))
                return self.send_json(job.to_dict())
            if rest == ["render"] and method == "POST":
                opts = self.read_json()
                job = app.jobs.start("render", pid, lambda j: app.render(pid, opts, j))
                return self.send_json(job.to_dict())
            if rest == ["frame"] and method == "GET":
                out = app.frame(pid, float(q.get("t", 0)))
                return self.send_file(out)
            if rest == ["exports"] and method == "GET":
                d = store.dir(pid) / "exports"
                files = sorted(d.glob("*.mp4"), key=lambda f: -f.stat().st_mtime) if d.exists() else []
                return self.send_json([{"name": f.name, "file": f"exports/{f.name}",
                                        "size": f.stat().st_size} for f in files])
            if rest == ["open-folder"] and method == "POST":
                _open_folder(store.dir(pid) / "exports")
                return self.send_json({"ok": True})
        self.send_error_json(404, "unknown api call")


def _has_alpha(path: Path) -> bool:
    """Does the video really have see-through parts (e.g. an exported VTuber animation)?

    The alpha_mode tag alone can't be trusted (Chrome sets it on every webcam recording),
    so a few frames are decoded and the transparency channel is checked.
    """
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream_tags=alpha_mode:stream=pix_fmt,codec_name", "-of", "json", str(path)],
                              capture_output=True, text=True)
        st = (json.loads(proc.stdout or "{}").get("streams") or [{}])[0]
        tags = {k.lower(): v for k, v in (st.get("tags") or {}).items()}
        pix = str(st.get("pix_fmt", ""))
        tagged = str(tags.get("alpha_mode", "")) == "1"
        if not tagged and not any(f in pix for f in ("yuva", "rgba", "bgra", "argb", "abgr", "ya8", "gbrap")):
            return False
        dec = ["-c:v", "libvpx-vp9"] if st.get("codec_name") == "vp9" else \
            ["-c:v", "libvpx"] if st.get("codec_name") == "vp8" else []
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", *dec, "-i", str(path), "-frames:v", "8",
                               "-vf", "format=yuva420p,alphaextract,signalstats,metadata=print:key=lavfi.signalstats.YMIN",
                               "-f", "null", "-"], capture_output=True, text=True)
        mins = [int(float(line.split("=")[1])) for line in proc.stderr.splitlines() if "signalstats.YMIN=" in line]
        return bool(mins) and min(mins) < 250
    except (OSError, ValueError):
        return False


def _clean_name(name: str) -> str:
    stem, ext = os.path.splitext(name)
    return (re.sub(r"[^A-Za-z0-9_\-]+", "_", stem).strip("_") or "file")[:60] + ext.lower()


def _unique(path: Path) -> Path:
    n, cand = 2, path
    while cand.exists():
        cand = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return cand


def _open_folder(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except OSError as exc:
        log.warning("Could not open folder: %s", exc)


def _data_folder() -> Path:
    return settings.settings_file().parent


def make_server(cfg: Config, port: int = 8765) -> ThreadingHTTPServer:
    settings.apply_env()  # API keys saved in Settings
    settings.apply_to_config(cfg)
    app = EditorApp(cfg)
    handler = type("BoundHandler", (Handler,), {"app": app})
    for p in [port] + list(range(port + 1, port + 20)):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), handler)
            server.daemon_threads = True
            return server
        except OSError:
            continue
    raise OSError(f"No free port found near {port}")


def run_editor(cfg: Config, port: int = 8765, open_browser: bool = True) -> int:
    server = make_server(cfg, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"\nClipTool Editor is running at {url}")
    print("It opens in your web browser. Keep this window open while you edit; press Ctrl+C to stop.\n")
    log.info("Editor started at %s (projects in %s)", url, cfg.resolve_dir("projects_dir"))
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEditor stopped.")
    finally:
        server.server_close()
    return 0
