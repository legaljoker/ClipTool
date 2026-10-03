"""Interactive, numbered menu so nobody has to remember commands."""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Optional

from cliptool import __version__
from cliptool.config import Config
from cliptool.export import PLATFORMS
from cliptool.ffmpeg_utils import FFmpegError, VIDEO_EXTENSIONS, ffmpeg_bin, list_videos
from cliptool.pipeline import JobOptions, batch, run_job

MENU = [
    ("1", "Quick short: remove silence + captions + vertical + titles", "quick"),
    ("2", "Transcribe videos (text + subtitle files)", "transcribe"),
    ("3", "Remove silence / dead air", "silence"),
    ("4", "Add TikTok-style captions", "captions"),
    ("5", "Reformat 16:9 -> 9:16 vertical", "reformat"),
    ("6", "Find the best clips in a long video (VOD)", "clips"),
    ("7", "Best-of: combine the best moments of several shorts into ONE short", "bestof"),
    ("8", "Long video: join several shorts into ONE YouTube video", "longform"),
    ("9", "Speakers & expressions (+ insert speaker pictures)", "speakers"),
    ("10", "Titles, descriptions & hashtags", "metadata"),
    ("11", "Batch export: process the whole drop folder", "batch"),
    ("12", "Custom: pick any combination of options", "custom"),
    ("13", "Check setup", "doctor"),
    ("14", "View change log", "changelog"),
    ("0", "Exit", "exit"),
]

PLATFORM_CHOICES = ["tiktok", "youtube_shorts", "instagram_reels", "youtube", "source"]


def ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    val = input(f"{prompt}{suffix}: ").strip()  # EOFError (closed input) ends the menu
    return val or (default or "")


def yes(prompt: str, default: bool = True) -> bool:
    val = ask(f"{prompt} (y/n)", "y" if default else "n").lower()
    return val.startswith("y")


def ask_paths(cfg: Config, multiple: bool = True) -> list[Path]:
    input_dir = cfg.resolve_dir("input_dir")
    print("\nDrag & drop video file(s) or a folder here, then press Enter.")
    print(f"(Leave empty to use everything in the input folder: {input_dir})")
    raw = ask("Video(s)")
    if not raw:
        input_dir.mkdir(parents=True, exist_ok=True)
        files = list_videos(input_dir)
        if not files:
            print(f"No videos in {input_dir}. Copy some videos there first.")
        return files
    try:
        parts = shlex.split(raw, posix=not raw.count("\\"))  # keep Windows backslashes
    except ValueError:
        parts = [raw]
    paths = []
    for part in parts:
        p = Path(part.strip().strip('"').strip("'")).expanduser()
        if p.exists():
            paths.append(p)
        else:
            print(f"  Not found: {p}")
    if not multiple:
        paths = paths[:1]
    return paths


def ask_platforms(cfg: Config, default: Optional[list[str]] = None) -> list[str]:
    default = default or list(cfg.get_path("export.platforms", ["tiktok"]))
    print("\nWhich platforms? (comma separated numbers)")
    for i, key in enumerate(PLATFORM_CHOICES, 1):
        p = PLATFORMS[key]
        size = f"{p.width}x{p.height}" if p.width else "keep size"
        print(f"  {i}) {p.label:<20} {size}")
    default_nums = ",".join(str(PLATFORM_CHOICES.index(d) + 1) for d in default if d in PLATFORM_CHOICES)
    raw = ask("Platforms", default_nums)
    out = []
    for tok in raw.replace(" ", "").split(","):
        if tok.isdigit() and 1 <= int(tok) <= len(PLATFORM_CHOICES):
            out.append(PLATFORM_CHOICES[int(tok) - 1])
        elif tok in PLATFORMS:
            out.append(tok)
    return out or default


def ask_reformat(cfg: Config) -> str:
    print("\nHow should wide videos be made vertical?")
    print("  1) smart - follow the face of whoever is on screen")
    print("  2) crop  - simple center crop")
    print("  3) blur  - show the whole picture over a blurred background")
    current = cfg.get_path("reformat.mode", "smart")
    raw = ask("Choice", {"smart": "1", "crop": "2", "blur": "3"}.get(current, "1"))
    return {"1": "smart", "2": "crop", "3": "blur"}.get(raw, raw if raw in ("smart", "crop", "blur") else current)


def custom_options(cfg: Config) -> Optional[JobOptions]:
    print("\nWhat kind of result do you want?")
    modes = [("each", "Each video becomes its own finished video"),
             ("clips", "Cut the best clips out of each video"),
             ("bestof", "ONE short made from the best moments of all videos"),
             ("longform", "ONE long video made by joining all videos"),
             ("analyze", "No video - just the report (transcripts, best clips, titles)")]
    for i, (_, label) in enumerate(modes, 1):
        print(f"  {i}) {label}")
    raw = ask("Choice", "1")
    mode = modes[int(raw) - 1][0] if raw.isdigit() and 1 <= int(raw) <= len(modes) else "each"
    opts = JobOptions(mode=mode)
    print("\nTurn options on or off:")
    opts.silence_cut = yes("  Remove silence / dead air?", True)
    opts.find_clips = mode == "clips" or yes("  Find & list the best clips in the report?", mode == "analyze")
    opts.speakers = yes("  Identify different speakers?", False)
    opts.expressions = yes("  Detect expressions (happy, sad, mad, angry...)?", opts.speakers)
    if mode != "analyze":
        opts.captions = yes("  Burn in TikTok-style captions?", True)
        opts.avatars = yes("  Insert speaker pictures with expressions?", False)
    opts.metadata = yes("  Write title / description / hashtag drafts?", True)
    opts.transcribe = True
    if mode != "analyze":
        opts.platforms = ask_platforms(cfg, ["youtube"] if mode == "longform" else None)
        if any(PLATFORMS[p].vertical for p in opts.platforms):
            opts.reformat_mode = ask_reformat(cfg)
    return opts


def ask_clip_lengths(cfg: Config) -> None:
    c = cfg["clips"]
    lo = ask("Shortest clip length in seconds", str(c["min_length"]))
    hi = ask("Longest clip length in seconds", str(c["max_length"]))
    n = ask("How many clips to suggest", str(c["count"]))
    try:
        cfg.set_path("clips.min_length", float(lo))
        cfg.set_path("clips.max_length", float(hi))
        cfg.set_path("clips.count", int(n))
    except ValueError:
        print("  (keeping the defaults)")


def build_options(choice: str, cfg: Config) -> Optional[JobOptions]:
    if choice == "quick":
        return JobOptions(mode="each", silence_cut=True, captions=True, metadata=True,
                          platforms=ask_platforms(cfg), reformat_mode=ask_reformat(cfg))
    if choice == "transcribe":
        return JobOptions(mode="analyze", transcribe=True, speakers=yes("Also label different speakers?", False))
    if choice == "silence":
        return JobOptions(mode="each", silence_cut=True, platforms=["source"])
    if choice == "captions":
        plats = ask_platforms(cfg, ["source"])
        return JobOptions(mode="each", captions=True, platforms=plats,
                          speakers=yes("Color captions by speaker?", False),
                          silence_cut=yes("Also remove silence?", False))
    if choice == "reformat":
        return JobOptions(mode="each", platforms=ask_platforms(cfg), reformat_mode=ask_reformat(cfg),
                          captions=yes("Also add captions?", False))
    if choice == "clips":
        ask_clip_lengths(cfg)
        export = yes("Export the clips as finished videos too?", True)
        opts = JobOptions(mode="clips" if export else "analyze", find_clips=True, metadata=True,
                          silence_cut=yes("Remove silence first?", False))
        if export:
            opts.captions = yes("Add captions to the clips?", True)
            opts.platforms = ask_platforms(cfg)
            opts.reformat_mode = ask_reformat(cfg)
        return opts
    if choice == "bestof":
        t = ask("Target length of the short in seconds", str(cfg.get_path("bestof.target_length")))
        try:
            cfg.set_path("bestof.target_length", float(t))
        except ValueError:
            pass
        return JobOptions(mode="bestof", captions=yes("Add captions?", True), metadata=True,
                          silence_cut=yes("Remove silence first?", True),
                          platforms=ask_platforms(cfg), reformat_mode=ask_reformat(cfg))
    if choice == "longform":
        return JobOptions(mode="longform", metadata=True, captions=yes("Add captions?", False),
                          silence_cut=yes("Remove silence from each short?", False),
                          platforms=ask_platforms(cfg, ["youtube"]))
    if choice == "speakers":
        n = ask("How many people are talking? (empty = let ClipTool guess)", "")
        if n.isdigit():
            cfg.set_path("speakers.num_speakers", int(n))
        avatars = yes("Insert speaker pictures into the video?", False)
        opts = JobOptions(mode="each" if avatars else "analyze", speakers=True, expressions=True,
                          avatars=avatars)
        if avatars:
            print(f"\nPictures go in {cfg.resolve_dir('avatars_dir')}/<Name>/neutral.png, happy.png, sad.png, ...")
            tl = ask("Hand-edited timeline .json from an earlier run (empty = detect automatically)", "")
            if tl and Path(tl.strip('"')).exists():
                opts.timeline_file = Path(tl.strip('"'))
            opts.captions = yes("Also add captions?", True)
            opts.platforms = ask_platforms(cfg)
        return opts
    if choice == "metadata":
        return JobOptions(mode="analyze", metadata=True, find_clips=yes("Also find the best clips?", False),
                          platforms=ask_platforms(cfg))
    if choice == "custom":
        return custom_options(cfg)
    return None


def run_menu(cfg: Config) -> int:
    while True:
        print("\n" + "=" * 64)
        print(f"  ClipTool {__version__}  -  TikTok & YouTube video maker")
        print("=" * 64)
        for key, label, _ in MENU:
            print(f"  {key:>2}) {label}")
        try:
            choice_key = ask("\nChoose an option", "1")
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        action = next((a for k, _, a in MENU if k == choice_key), None)
        if action is None:
            print("Please type one of the numbers shown.")
            continue
        if action == "exit":
            return 0
        if action == "doctor":
            from cliptool.doctor import run_doctor
            run_doctor(cfg)
            continue
        if action == "changelog":
            from cliptool.cli import show_changelog
            show_changelog()
            continue
        try:
            ffmpeg_bin()
        except FFmpegError as exc:
            print(exc)
            continue
        try:
            if action == "batch":
                print("\nBatch mode uses the 'Custom' options for every video in the drop folder.")
                folder = Path(ask("Drop folder", str(cfg.resolve_dir("input_dir"))).strip('"'))
                opts = custom_options(cfg)
                if opts.mode in ("bestof", "longform") and not yes("This combines ALL videos in the folder. OK?"):
                    continue
                watch = yes("Keep watching the folder for new videos?", False)
                results = batch(folder, opts, cfg, watch=watch)
                for r in results:
                    print(f"  -> {r.report_file}")
                continue
            opts = build_options(action, cfg)
            if opts is None:
                continue
            paths = ask_paths(cfg)
            if not paths:
                continue
            if opts.mode in ("bestof", "longform"):
                n = sum(len(list_videos(p)) if p.is_dir() else 1 for p in paths)
                if n < 2:
                    print("Tip: best-of and long videos work best with 2 or more videos.")
            first = paths[0]
            default_name = f"{action}_{first.stem if first.suffix.lower() in VIDEO_EXTENSIONS else first.name}"
            opts.name = ask("Name for this job", default_name)
            result = run_job(paths, opts, cfg)
            print("\n" + "-" * 64)
            print(f"Done! Everything is in: {result.job_dir}")
            if result.report_file:
                print(f"Open this first:        {result.report_file}")
            if result.errors:
                print(f"{len(result.errors)} problem(s) - see the report.")
        except KeyboardInterrupt:
            print("\nCancelled.")
        except Exception as exc:  # keep the menu alive
            print(f"\nSomething went wrong: {exc}\nDetails are in the log file (logs/cliptool.log).")
