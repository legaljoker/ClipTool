"""Runs a job: the selected options applied to one or more input videos."""
from __future__ import annotations

import re
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from cliptool import ai, clipfinder, compile as comp, emotions, metadata, silence, speakers
from cliptool.avatars import TimelineEntry, build_timeline, load_timeline, save_timeline
from cliptool.clipfinder import Clip
from cliptool.config import Config
from cliptool.export import RenderOptions, get_platform, render
from cliptool.ffmpeg_utils import MediaInfo, cut, extract_audio, list_videos, probe, read_wav
from cliptool.logger import add_job_log, get_logger, remove_handler
from cliptool.metadata import Metadata
from cliptool.transcribe import Transcript, transcribe

log = get_logger("pipeline")

MODES = {
    "each": "Process each video as its own finished video",
    "clips": "Find the best clips in each video and export them",
    "bestof": "Combine the best moments of all videos into ONE short",
    "longform": "Join all videos into ONE longer video (with chapters)",
    "analyze": "Analyze only (transcript, best clips, titles) - no video output",
}


@dataclass
class JobOptions:
    name: str = "job"
    mode: str = "each"
    silence_cut: bool = False
    transcribe: bool = False
    captions: bool = False
    find_clips: bool = False
    speakers: bool = False
    expressions: bool = False
    avatars: bool = False
    metadata: bool = False
    platforms: list[str] = field(default_factory=lambda: ["tiktok"])
    reformat_mode: Optional[str] = None  # None = from config
    timeline_file: Optional[Path] = None  # hand-edited speaker/expression timeline
    keep_work_files: bool = False

    def enabled(self) -> list[str]:
        names = ["silence_cut", "transcribe", "captions", "find_clips", "speakers", "expressions",
                 "avatars", "metadata"]
        return [n for n in names if getattr(self, n)]

    @property
    def needs_transcript(self) -> bool:
        return (self.transcribe or self.captions or self.find_clips or self.speakers or self.expressions
                or self.avatars or self.metadata or self.mode in ("clips", "bestof"))


@dataclass
class Prepared:
    original: Path
    video: Path
    info: MediaInfo
    keep: Optional[list] = None
    silence_stats: Optional[dict] = None
    transcript: Optional[Transcript] = None
    audio: Optional[tuple] = None
    clips: list[Clip] = field(default_factory=list)
    transcript_files: dict = field(default_factory=dict)
    timeline_file: Optional[Path] = None


@dataclass
class Product:
    name: str
    video: Optional[Path]
    transcript: Optional[Transcript]
    kind: str
    description: str = ""
    chapters: str = ""
    moments: list = field(default_factory=list)
    clip: Optional[Clip] = None
    outputs: dict[str, Path] = field(default_factory=dict)
    metadata: dict[str, Metadata] = field(default_factory=dict)
    timeline_file: Optional[Path] = None


@dataclass
class JobResult:
    options: JobOptions
    job_dir: Path
    started: datetime
    finished: Optional[datetime] = None
    prepared: list[Prepared] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    report_file: Optional[Path] = None
    input_files: list[Path] = field(default_factory=list)


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", s).strip("_")[:60] or "video"


def make_job_dir(cfg: Config, name: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    job = cfg.resolve_dir("output_dir") / f"{safe_name(name)}_{stamp}"
    job.mkdir(parents=True, exist_ok=True)
    return job


class Pipeline:
    def __init__(self, cfg: Config, opts: JobOptions):
        self.cfg = cfg
        self.opts = opts
        if opts.avatars:
            opts.speakers = opts.expressions = True
        self.reformat_mode = opts.reformat_mode or cfg.get_path("reformat.mode", "smart")
        self.names = {str(k): str(v) for k, v in (cfg.get_path("speakers.names") or {}).items()}

    # ------------------------------------------------------------------ stage 1
    def prepare(self, src: Path, job: JobResult, work: Path) -> Prepared:
        o, cfg = self.opts, self.cfg
        log.info("")
        log.info("=== %s ===", src.name)
        info = probe(src)
        if not info.has_video:
            raise ValueError(f"{src.name} has no video stream")
        p = Prepared(original=src, video=src, info=info)
        stem = safe_name(src.stem)

        exact_map = True
        if o.silence_cut:
            out = work / f"{stem}_nosilence.mp4"
            p.video, p.keep, p.silence_stats = silence.cut_silence(src, out, cfg["silence"], work)
            exact_map = p.silence_stats.get("exact_map", True)
            p.info = probe(p.video)

        if o.needs_transcript:
            cache = cfg.resolve_dir("output_dir") / ".transcript_cache"
            if p.keep and not exact_map:
                tr = transcribe(p.video, cfg["transcription"], cache)
            else:
                tr = transcribe(src, cfg["transcription"], cache)
                if p.keep:
                    p.transcript_files["original"] = tr.save_all(job.job_dir / "transcripts",
                                                                 f"{stem}_original_timing")
                    tr = silence.remap_transcript(tr, p.keep)
            p.transcript = tr

        if o.speakers or o.expressions or o.find_clips or o.mode in ("clips", "bestof"):
            if p.info.has_audio:
                wav = extract_audio(p.video, work / f"{stem}.wav")
                p.audio = read_wav(wav)
                if o.speakers and p.transcript:
                    speakers.identify_speakers(p.transcript, wav, *p.audio, cfg["speakers"])
            if o.expressions and p.transcript:
                log.info("Detecting expressions...")
                emotions.tag_expressions(p.transcript, cfg["emotions"], p.audio)

        if p.transcript:
            p.transcript_files["main"] = p.transcript.save_all(job.job_dir / "transcripts", stem)
            if o.speakers or o.expressions:
                (job.job_dir / "timelines").mkdir(exist_ok=True)
                p.timeline_file = save_timeline(build_timeline(p.transcript, self.names),
                                                job.job_dir / "timelines" / f"{stem}_timeline.json")

        if (o.find_clips or o.mode == "clips") and p.transcript:
            c = cfg["clips"]
            reranker = ai.make_clip_reranker(cfg["ai"]) if ai.should_use(c.get("use_ai", "auto")) else None
            log.info("Finding the best %s-%ss clips...", c["min_length"], c["max_length"])
            p.clips = clipfinder.find_clips(p.transcript, float(c["min_length"]), float(c["max_length"]),
                                            int(c["count"]), p.audio, reranker)
            for clip in p.clips:
                clip.source = str(src)
                if not clip.title:
                    part = p.transcript.slice(clip.start, clip.end)
                    clip.title = metadata.builtin_metadata(part, "tiktok", 1).titles[0] if part.text else ""
                if p.keep:
                    clip.original_start = silence.cut_to_original(clip.start, p.keep)
                    clip.original_end = silence.cut_to_original(clip.end, p.keep)
                else:
                    clip.original_start, clip.original_end = clip.start, clip.end
            log.info("  -> %d clip suggestions", len(p.clips))
        return p

    # ------------------------------------------------------------------ stage 2
    def build_products(self, job: JobResult, work: Path) -> list[Product]:
        o, cfg = self.opts, self.cfg
        preps = [p for p in job.prepared]
        products: list[Product] = []
        if o.mode == "analyze" or not preps:
            return products

        if o.mode == "each":
            for p in preps:
                products.append(Product(safe_name(p.original.stem), p.video, p.transcript, "video",
                                        timeline_file=p.timeline_file))

        elif o.mode == "clips":
            for p in preps:
                for clip in p.clips:
                    name = f"{safe_name(p.original.stem)}_clip{clip.rank:02d}"
                    out = work / f"{name}.mp4"
                    cut(p.video, clip.start, clip.end, out)
                    tr = p.transcript.slice(clip.start, clip.end) if p.transcript else None
                    products.append(Product(name, out, tr, "clip", clip=clip))

        elif o.mode == "bestof":
            b = cfg["bestof"]
            per_source: dict[Path, list[Clip]] = {}
            for p in preps:
                if not p.transcript:
                    continue
                moments = clipfinder.find_clips(p.transcript, float(b["moment_min"]), float(b["moment_max"]),
                                                int(b["max_per_source"]) * 2, p.audio)
                for m in moments:
                    m.source = str(p.original)
                per_source[p.video] = moments
            chosen = comp.select_bestof(per_source, float(b["target_length"]), int(b["max_per_source"]))
            if not chosen:
                raise ValueError("No usable speech moments were found for a best-of compilation.")
            w, h = (int(x) for x in str(b.get("canvas", "1080x1920")).split("x"))
            parts, trs = [], []
            by_video = {p.video: p for p in preps}
            for i, m in enumerate(chosen):
                part = work / f"bestof_part{i:02d}.mp4"
                comp.normalize(m.source, part, w, h, self.reformat_mode, 30, m.clip.start, m.clip.end)
                parts.append(part)
                trs.append((by_video[m.source].transcript.slice(m.clip.start, m.clip.end), m.out_offset))
            out = work / "bestof_combined.mp4"
            comp.concat(parts, out, work)
            total = sum(m.clip.duration for m in chosen)
            log.info("Best-of: %d moments from %d videos, %.1fs total", len(chosen), len(per_source), total)
            products.append(Product(safe_name(o.name) + "_bestof", out, Transcript.concat(trs), "bestof",
                                    moments=chosen))

        elif o.mode == "longform":
            lf = cfg["longform"]
            w, h = (int(x) for x in str(lf.get("canvas", "1920x1080")).split("x"))
            parts, trs, chapters = [], [], []
            offset = 0.0
            for i, p in enumerate(preps):
                part = work / f"long_part{i:02d}.mp4"
                comp.normalize(p.video, part, w, h, lf.get("reformat_mode", "blur"), 30)
                parts.append(part)
                if p.transcript:
                    trs.append((p.transcript, offset))
                    title = metadata.builtin_metadata(p.transcript, "youtube", 1).titles[0] \
                        if p.transcript.text else p.original.stem
                else:
                    title = p.original.stem.replace("_", " ")
                if any(title == t for _, t in chapters):  # keep chapter names distinct
                    title = p.original.stem.replace("_", " ")
                chapters.append((offset, title))
                offset += probe(part).duration
            out = work / "longform_combined.mp4"
            comp.concat(parts, out, work)
            ch = comp.chapters_text(chapters) if lf.get("chapters", True) and len(chapters) > 1 else ""
            log.info("Long video: %d parts, %.1f minutes", len(parts), offset / 60)
            products.append(Product(safe_name(o.name) + "_long", out, Transcript.concat(trs) if trs else None,
                                    "longform", chapters=ch))
        return products

    # ------------------------------------------------------------------ stage 3
    def finish(self, product: Product, job: JobResult, work: Path) -> None:
        o, cfg = self.opts, self.cfg
        timeline: Optional[list[TimelineEntry]] = None
        if o.avatars:
            if o.timeline_file and len(job.prepared) == 1 and product.kind == "video":
                timeline = load_timeline(o.timeline_file)
                log.info("Using hand-edited timeline %s", o.timeline_file)
            elif product.transcript:
                timeline = build_timeline(product.transcript, self.names)
            if timeline and product.kind != "video":
                (job.job_dir / "timelines").mkdir(exist_ok=True)
                product.timeline_file = save_timeline(timeline, job.job_dir / "timelines"
                                                      / f"{product.name}_timeline.json")

        for plat_key in o.platforms:
            plat = get_platform(plat_key)
            out = job.job_dir / "videos" / plat_key / f"{product.name}.mp4"
            if product.video is not None:
                ropts = RenderOptions(
                    platform=plat_key,
                    reformat_mode=self.reformat_mode,
                    blur_strength=int(cfg.get_path("reformat.blur_strength", 20)),
                    transcript=product.transcript if o.captions else None,
                    captions_cfg=cfg["captions"],
                    timeline=timeline,
                    avatars_dir=cfg.resolve_dir("avatars_dir"),
                    avatars_cfg=cfg["avatars"],
                    loudnorm=bool(cfg.get_path("export.loudnorm", True)),
                    preset=cfg.get_path("export.preset", "medium"),
                )
                product.outputs[plat_key] = render(product.video, out, ropts, work)
            if o.metadata and product.transcript and product.transcript.text:
                md = metadata.generate(product.transcript, plat_key, cfg["metadata"], cfg["ai"],
                                       product.chapters if plat.key.startswith("youtube") else "")
                product.metadata[plat_key] = md
                if product.video is not None:
                    _write_upload_text(out.with_suffix(".txt"), md, plat.label)

    # ------------------------------------------------------------------ run
    def run(self, inputs: list[Path]) -> JobResult:
        from cliptool.report import write_report

        job_dir = make_job_dir(self.cfg, self.opts.name)
        handler = add_job_log(job_dir)
        job = JobResult(self.opts, job_dir, datetime.now())
        work = job_dir / "_work"
        work.mkdir(exist_ok=True)
        log.info("Job folder: %s", job_dir)
        log.info("Mode: %s | options: %s | platforms: %s", self.opts.mode,
                 ", ".join(self.opts.enabled()) or "none", ", ".join(self.opts.platforms))
        try:
            files: list[Path] = []
            for i in inputs:
                files += list_videos(Path(i))
            job.input_files = files
            if not files:
                raise ValueError("No video files found in the input you gave.")
            for src in files:
                try:
                    job.prepared.append(self.prepare(src, job, work))
                except Exception as exc:
                    log.error("Problem with %s: %s", src.name, exc)
                    log.debug(traceback.format_exc())
                    job.errors.append(f"{src.name}: {exc}")
            try:
                job.products = self.build_products(job, work)
            except Exception as exc:
                log.error("Could not build the output video: %s", exc)
                log.debug(traceback.format_exc())
                job.errors.append(f"combine step: {exc}")
            if self.opts.mode == "analyze" and self.opts.metadata:
                for p in job.prepared:
                    if p.transcript and p.transcript.text:
                        prod = Product(safe_name(p.original.stem), None, p.transcript, "analysis")
                        self.finish(prod, job, work)
                        job.products.append(prod)
            else:
                for prod in job.products:
                    try:
                        self.finish(prod, job, work)
                    except Exception as exc:
                        log.error("Could not finish %s: %s", prod.name, exc)
                        log.debug(traceback.format_exc())
                        job.errors.append(f"{prod.name}: {exc}")
        finally:
            job.finished = datetime.now()
            try:
                job.report_file = write_report(job, self.cfg)
                log.info("")
                log.info("Report written: %s", job.report_file)
            except Exception as exc:
                log.error("Could not write report: %s", exc)
                log.debug(traceback.format_exc())
            if not self.opts.keep_work_files:
                shutil.rmtree(work, ignore_errors=True)
            remove_handler(handler)
        return job


def _write_upload_text(path: Path, md: Metadata, label: str) -> None:
    lines = [f"{label} upload drafts ({md.source})", "", "TITLE OPTIONS:"]
    lines += [f"  {i}. {t}" for i, t in enumerate(md.titles, 1)]
    lines += ["", "DESCRIPTION:", md.description, "", "HASHTAGS:", " ".join(md.hashtags), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_job(inputs: list[Path], opts: JobOptions, cfg: Config) -> JobResult:
    return Pipeline(cfg, opts).run(inputs)


# ---------------------------------------------------------------------------
# Batch / drop folder
# ---------------------------------------------------------------------------

def batch(folder: Path, opts: JobOptions, cfg: Config, watch: bool = False) -> list[JobResult]:
    """Process every video in a drop folder. With watch=True keep checking for new files."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    done_dir = folder / "processed"
    move = bool(cfg.get_path("batch.move_processed", True))
    interval = int(cfg.get_path("batch.watch_interval", 10))
    results: list[JobResult] = []
    sizes: dict[Path, int] = {}

    def ready_files() -> list[Path]:
        ready = []
        for f in list_videos(folder):
            size = f.stat().st_size
            if watch and sizes.get(f) != size:  # still being copied in
                sizes[f] = size
                continue
            ready.append(f)
        return ready

    if watch:
        log.info("Watching %s for new videos (Ctrl+C to stop)...", folder)
    while True:
        files = ready_files()
        if files:
            if opts.mode in ("bestof", "longform"):
                results.append(run_job(files, opts, cfg))
            else:
                for f in files:
                    single = JobOptions(**{**opts.__dict__, "name": f.stem})
                    results.append(run_job([f], single, cfg))
            if move:
                done_dir.mkdir(exist_ok=True)
                for f in files:
                    shutil.move(str(f), str(done_dir / f.name))
        elif not watch:
            log.info("No videos found in %s", folder)
        if not watch:
            break
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            break
    return results
