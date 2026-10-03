"""Command line entry point. Run with no arguments for the interactive menu."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cliptool import __version__
from cliptool.config import APP_ROOT, load_config
from cliptool.export import PLATFORMS
from cliptool.logger import setup_logging
from cliptool.pipeline import MODES, JobOptions, batch, run_job

# command -> (help, preset options)
PRESETS: dict[str, tuple[str, dict]] = {
    "quick": ("Silence cut + captions + vertical reformat + titles (one finished short per video)",
              dict(mode="each", silence_cut=True, captions=True, metadata=True)),
    "transcribe": ("Transcribe videos to text and subtitle files", dict(mode="analyze", transcribe=True)),
    "silence": ("Remove silence / dead air", dict(mode="each", silence_cut=True, platforms=["source"])),
    "captions": ("Burn TikTok-style captions into videos", dict(mode="each", captions=True, platforms=["source"])),
    "reformat": ("Reformat 16:9 videos to vertical 9:16", dict(mode="each")),
    "clips": ("Find the best 30-60s clips in a long video (add --render to export them)",
              dict(mode="analyze", find_clips=True, metadata=True)),
    "bestof": ("Combine the best moments of several shorts into one short",
               dict(mode="bestof", captions=True, metadata=True)),
    "longform": ("Join several shorts into one longer YouTube video with chapters",
                 dict(mode="longform", metadata=True, platforms=["youtube"])),
    "speakers": ("Identify speakers + expressions (add --avatars to insert pictures)",
                 dict(mode="analyze", speakers=True, expressions=True)),
    "metadata": ("Draft titles, descriptions and hashtags", dict(mode="analyze", metadata=True)),
    "run": ("Custom: pick any options with flags", dict()),
}


def _add_common(p: argparse.ArgumentParser, inputs: bool = True) -> None:
    if inputs:
        p.add_argument("inputs", nargs="+", help="video files or folders")
    p.add_argument("--name", help="name for the output folder")
    p.add_argument("--platforms", help=f"comma list: {', '.join(PLATFORMS)}")
    p.add_argument("--mode", choices=list(MODES), help="what kind of output to make")
    p.add_argument("--reformat-mode", choices=["smart", "crop", "blur"])
    p.add_argument("--silence-cut", action="store_true", help="remove dead air")
    p.add_argument("--captions", action="store_true", help="burn in captions")
    p.add_argument("--find-clips", action="store_true", help="find best clips")
    p.add_argument("--speakers", action="store_true", help="identify speakers")
    p.add_argument("--expressions", action="store_true", help="detect expressions")
    p.add_argument("--avatars", action="store_true", help="overlay speaker pictures")
    p.add_argument("--metadata", action="store_true", help="draft titles/descriptions/hashtags")
    p.add_argument("--timeline", help="hand-edited timeline .json for --avatars")
    p.add_argument("--num-speakers", type=int, help="how many people talk (if known)")
    p.add_argument("--min", type=float, dest="clip_min", help="shortest clip (seconds)")
    p.add_argument("--max", type=float, dest="clip_max", help="longest clip (seconds)")
    p.add_argument("--count", type=int, help="how many clips to suggest")
    p.add_argument("--render", action="store_true", help="(clips) also export the clips as videos")
    p.add_argument("--keep-work", action="store_true", help="keep intermediate files")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cliptool", description="Make TikTok / YouTube videos from raw recordings.")
    ap.add_argument("--version", action="version", version=f"ClipTool {__version__}")
    ap.add_argument("--config", help="path to a config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true", help="show detailed output")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("menu", help="interactive menu (default)")
    for name, (help_text, _) in PRESETS.items():
        _add_common(sub.add_parser(name, help=help_text, description=help_text))
    b = sub.add_parser("batch", help="process every video in a drop folder")
    b.add_argument("folder", nargs="?", help="drop folder (default: the 'input' folder)")
    b.add_argument("--watch", action="store_true", help="keep watching for new videos")
    _add_common(b, inputs=False)
    sub.add_parser("doctor", help="check that everything is installed")
    sub.add_parser("changelog", help="show the list of changes to ClipTool")
    sub.add_parser("platforms", help="list export platforms and their settings")
    return ap


def options_from_args(args: argparse.Namespace, preset: dict, cfg) -> JobOptions:
    opts = JobOptions(**{k: v for k, v in preset.items()})
    for flag in ("silence_cut", "captions", "find_clips", "speakers", "expressions", "avatars", "metadata"):
        if getattr(args, flag, False):
            setattr(opts, flag, True)
    if args.mode:
        opts.mode = args.mode
    if args.command == "clips" and args.render:
        opts.mode = "clips"
    if args.command in ("speakers",) and args.avatars:
        opts.mode = "each"
    if args.platforms:
        opts.platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    elif "platforms" not in preset:
        opts.platforms = list(cfg.get_path("export.platforms", ["tiktok"]))
    for p in opts.platforms:
        if p not in PLATFORMS:
            raise SystemExit(f"Unknown platform '{p}'. Choose from: {', '.join(PLATFORMS)}")
    opts.reformat_mode = args.reformat_mode
    opts.timeline_file = Path(args.timeline) if args.timeline else None
    opts.keep_work_files = args.keep_work
    first = Path(args.inputs[0]).stem if getattr(args, "inputs", None) else "batch"
    opts.name = args.name or f"{args.command}_{first}"
    if args.num_speakers:
        cfg.set_path("speakers.num_speakers", args.num_speakers)
    if args.clip_min:
        cfg.set_path("clips.min_length", args.clip_min)
    if args.clip_max:
        cfg.set_path("clips.max_length", args.clip_max)
    if args.count:
        cfg.set_path("clips.count", args.count)
    return opts


def show_changelog() -> None:
    path = APP_ROOT / "CHANGELOG.md"
    print(path.read_text(encoding="utf-8") if path.exists() else "CHANGELOG.md not found.")


def show_platforms() -> None:
    print(f"{'key':<17}{'name':<22}{'size':<12}{'video':<8}{'audio':<7}max length")
    for p in PLATFORMS.values():
        size = f"{p.width}x{p.height}" if p.width else "source"
        print(f"{p.key:<17}{p.label:<22}{size:<12}{p.video_bitrate or 'crf18':<8}{p.audio_bitrate:<7}"
              f"{str(int(p.max_duration)) + 's' if p.max_duration else '-'}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f"Could not read the config file: {exc}")
        return 2
    setup_logging(cfg.resolve_dir("log_dir"), args.verbose)

    if args.command in (None, "menu"):
        from cliptool.menu import run_menu
        return run_menu(cfg)
    if args.command == "doctor":
        from cliptool.doctor import run_doctor
        return 0 if run_doctor(cfg) else 1
    if args.command == "changelog":
        show_changelog()
        return 0
    if args.command == "platforms":
        show_platforms()
        return 0

    from cliptool.ffmpeg_utils import FFmpegError, ffmpeg_bin
    try:
        ffmpeg_bin()
    except FFmpegError as exc:
        print(exc)
        return 2

    if args.command == "batch":
        opts = options_from_args(args, dict(mode="each"), cfg)
        folder = Path(args.folder) if args.folder else cfg.resolve_dir("input_dir")
        results = batch(folder, opts, cfg, watch=args.watch)
        return 0 if all(not r.errors for r in results) else 1

    opts = options_from_args(args, PRESETS[args.command][1], cfg)
    missing = [i for i in args.inputs if not Path(i).exists()]
    if missing:
        print("Not found: " + ", ".join(missing))
        return 2
    result = run_job([Path(i) for i in args.inputs], opts, cfg)
    print(f"\nDone. Results are in: {result.job_dir}")
    if result.report_file:
        print(f"Read this first:      {result.report_file}")
    return 0 if not result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
