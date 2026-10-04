"""Writes REPORT.txt: a plain-English summary of what a job found and produced."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from cliptool import __version__, silence
from cliptool.export import get_platform
from cliptool.ffmpeg_utils import fmt_time
from cliptool.pipeline import MODES
from cliptool.speakers import speaker_stats

if TYPE_CHECKING:
    from cliptool.config import Config
    from cliptool.pipeline import JobResult, Prepared

LINE = "=" * 72
THIN = "-" * 72

OPTION_LABELS = {
    "silence_cut": "Remove silence / dead air",
    "transcribe": "Transcribe",
    "captions": "Burn in TikTok-style captions",
    "find_clips": "Find best clips",
    "speakers": "Identify speakers",
    "expressions": "Detect expressions",
    "avatars": "Insert speaker pictures (avatars)",
    "metadata": "Title / description / hashtag drafts",
}


def _rel(path: Optional[Path], base: Path) -> str:
    if not path:
        return "-"
    try:
        return str(Path(path).resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def _range(a: float, b: float) -> str:
    return f"{fmt_time(a, True)} -> {fmt_time(b, True)}  ({b - a:.1f}s)"


def _orig(prep: "Prepared", t: float) -> float:
    return silence.cut_to_original(t, prep.keep) if prep.keep else t


def write_report(job: "JobResult", cfg: "Config") -> Path:
    o = job.options
    base = job.job_dir
    L: list[str] = []
    add = L.append

    add(LINE)
    add(f" CLIPTOOL REPORT  -  {o.name}")
    add(f" Created {job.started:%Y-%m-%d %H:%M}   |   ClipTool v{__version__}")
    add(LINE)
    add("")
    add("WHAT WAS DONE")
    add(f"  Job type : {MODES.get(o.mode, o.mode)}")
    for key, label in OPTION_LABELS.items():
        if getattr(o, key):
            add(f"  [x] {label}")
    if o.mode != "analyze":
        plats = [get_platform(p) for p in o.platforms]
        add("  Platforms: " + ", ".join(f"{p.label} ({p.width}x{p.height})" if p.width else p.label
                                        for p in plats))
        if any(p.vertical for p in plats):
            add(f"  Vertical reformat mode: {o.reformat_mode or cfg.get_path('reformat.mode')}")
    n_in = len(job.input_files) or len(job.prepared)
    failed = n_in - len(job.prepared)
    add(f"  Input videos: {n_in}" + (f"  ({failed} could not be processed)" if failed > 0 else ""))
    if job.finished:
        add(f"  Time taken: {(job.finished - job.started).seconds // 60}m {(job.finished - job.started).seconds % 60}s")
    add("")

    # ---- TL;DR -----------------------------------------------------------
    all_clips = [(p, c) for p in job.prepared for c in p.clips]
    add(THIN)
    add("QUICK SUMMARY")
    add(THIN)
    if all_clips:
        p, best = max(all_clips, key=lambda pc: pc[1].score)
        add(f"  BEST CLIP: {p.original.name}  at  {_range(best.original_start or best.start, best.original_end or best.end)}")
        add(f"             starts with: \"{best.hook[:90]}\"")
    outputs = [(prod, plat, f) for prod in job.products for plat, f in prod.outputs.items()]
    if outputs:
        add(f"  Finished videos: {len(outputs)} (in the 'videos' folder, one sub-folder per platform)")
    if job.errors:
        add(f"  PROBLEMS: {len(job.errors)} - see the PROBLEMS section at the bottom.")
    if not all_clips and not outputs and not job.errors:
        add("  Nothing to summarise.")
    add("")

    # ---- per input ---------------------------------------------------------
    for n, p in enumerate(job.prepared, 1):
        add(THIN)
        add(f"VIDEO {n}: {p.original.name}")
        add(THIN)
        orig_len = p.silence_stats["original"] if p.silence_stats else p.info.duration
        add(f"  Length: {fmt_time(orig_len)}   ({p.info.width}x{p.info.height}, {p.info.fps:.0f} fps)")
        if p.silence_stats:
            s = p.silence_stats
            add(f"  Silence removed: {s['removed']:.1f}s of dead air cut in {s['cuts']} places "
                f"-> new length {fmt_time(s['kept'])}")
        if p.transcript:
            tr = p.transcript
            add(f"  Language: {tr.language or '?'}   |   {len(tr.segments)} spoken segments, "
                f"{len(tr.text.split())} words")
            for kind, files in p.transcript_files.items():
                label = "Transcript" if kind == "main" else "Transcript (original timing)"
                add(f"  {label}: {_rel(files['txt'], base)}  (+ .srt subtitles, .vtt, .json)")
            stats = speaker_stats(tr)
            if stats:
                total = sum(stats.values()) or 1
                names = {str(k): v for k, v in (cfg.get_path("speakers.names") or {}).items()}
                parts = [f"{spk}{' (' + names[spk] + ')' if spk in names else ''} "
                         f"{v / total * 100:.0f}% ({fmt_time(v)})" for spk, v in stats.items()]
                add(f"  Speakers ({len(stats)}): " + ", ".join(parts))
            emos = Counter(s.emotion for s in tr.segments if s.emotion)
            if emos:
                add("  Expressions: " + ", ".join(f"{e} x{c}" for e, c in emos.most_common()))
            if p.timeline_file:
                add(f"  Speaker/expression timeline (editable): {_rel(p.timeline_file, base)}")

        if p.clips:
            add("")
            note = " (times are in the ORIGINAL recording)" if p.keep else ""
            add(f"  BEST CLIPS, ranked{note}:")
            for c in p.clips:
                os_, oe = c.original_start or c.start, c.original_end or c.end
                add("")
                add(f"   #{c.rank}  {_range(os_, oe)}   score {c.score:.1f}")
                if p.keep:
                    add(f"       in the silence-removed version: {_range(c.start, c.end)}")
                if c.title:
                    add(f"       Suggested title: {c.title}")
                add(f"       Opens with: \"{c.hook[:100]}\"")
                add(f"       Why it's good: {'; '.join(c.reasons) or '-'}")
                preview = c.text[:220] + ("..." if len(c.text) > 220 else "")
                add(f"       What's said: {preview}")
                exported = [prod for prod in job.products if prod.clip is c]
                for prod in exported:
                    for plat, f in prod.outputs.items():
                        add(f"       Exported ({plat}): {_rel(f, base)}")
                add(f"       Cut it yourself: ffmpeg -ss {os_:.2f} -i \"{p.original.name}\" -t {oe - os_:.2f} "
                    f"-c:v libx264 -c:a aac clip{c.rank}.mp4")
        add("")

    # ---- combined products ------------------------------------------------
    preps_by_video = {p.video: p for p in job.prepared}
    for prod in job.products:
        if prod.kind == "bestof":
            add(THIN)
            add(f"BEST-OF COMPILATION: {prod.name}")
            add(THIN)
            for i, m in enumerate(prod.moments, 1):
                p = preps_by_video.get(m.source)
                a, b = (_orig(p, m.clip.start), _orig(p, m.clip.end)) if p else (m.clip.start, m.clip.end)
                name = p.original.name if p else Path(m.source).name
                add(f"  {i}. at {fmt_time(m.out_offset)} in the compilation <- {name}  {_range(a, b)}")
                add(f"     \"{m.clip.text[:110]}\"")
            add("")
        if prod.kind == "longform" and prod.chapters:
            add(THIN)
            add(f"LONG VIDEO: {prod.name} - YouTube chapters (paste into the description)")
            add(THIN)
            for line in prod.chapters.splitlines():
                add(f"  {line}")
            add("")

    # ---- outputs + upload drafts -----------------------------------------
    if job.products:
        add(THIN)
        add("FINISHED VIDEOS & UPLOAD DRAFTS")
        add(THIN)
        for prod in job.products:
            add(f"  {prod.name}")
            for plat, f in prod.outputs.items():
                add(f"    [{get_platform(plat).label}] {_rel(f, base)}")
            if prod.timeline_file:
                add(f"    Speaker/expression timeline: {_rel(prod.timeline_file, base)}")
            for plat, md in prod.metadata.items():
                add(f"    -- {get_platform(plat).label} drafts ({md.source}) --")
                for i, t in enumerate(md.titles, 1):
                    add(f"      Title {i}: {t}")
                add("      Description:")
                for line in md.description.splitlines():
                    add(f"        {line}")
            add("")

    # ---- timeline sample ---------------------------------------------------
    for p in job.prepared:
        if p.transcript and (o.speakers or o.expressions):
            add(THIN)
            add(f"WHO SAID WHAT - {p.original.name} (first 25 lines; full list in the timeline file)")
            add(THIN)
            for s in p.transcript.segments[:25]:
                add(f"  {fmt_time(s.start)}-{fmt_time(s.end)}  {s.speaker or '':<10} {s.emotion or '':<9} "
                    f"{s.text[:70]}")
            add("")

    if job.errors:
        add(THIN)
        add("PROBLEMS")
        add(THIN)
        for e in job.errors:
            add(f"  ! {e}")
        add("  Full details are in job.log in this folder.")
        add("")

    add(THIN)
    add("TIPS")
    add(THIN)
    add("  * Times are hours:minutes:seconds.milliseconds.")
    if o.speakers or o.avatars:
        add("  * Wrong speaker or expression? Edit the timeline .json file, then run")
        add("    'Speakers & avatars' again and choose that timeline file.")
        add("  * Put pictures in avatars/<Name>/ (neutral.png, happy.png, sad.png, mad.png, angry.png...)")
        add("    and map speakers to names in config.yaml under speakers -> names.")
    add("  * Transcripts (.srt) can be uploaded to YouTube as closed captions.")
    add("")

    report = base / "REPORT.txt"
    report.write_text("\n".join(L), encoding="utf-8")

    if all_clips:
        with open(base / "best_clips.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["video", "rank", "start", "end", "start_seconds", "end_seconds", "length", "score",
                        "title", "opens_with", "why"])
            for p, c in all_clips:
                a, b = c.original_start or c.start, c.original_end or c.end
                w.writerow([p.original.name, c.rank, fmt_time(a, True), fmt_time(b, True), f"{a:.2f}",
                            f"{b:.2f}", f"{b - a:.1f}", f"{c.score:.2f}", c.title, c.hook, "; ".join(c.reasons)])
    return report
