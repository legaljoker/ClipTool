# ClipTool change log

Every update or change to ClipTool is recorded here, newest first.
Read it from inside the app with menu option **14) View change log** or `cliptool changelog`.

Format: each release lists what was **Added**, **Changed**, **Fixed** or **Removed**.
When you change the application, add an entry at the top and bump the version in
`cliptool/__init__.py`.

---

## [1.0.1] - 2026-10-04 - Fixes for newer ffmpeg and PyAV

### Fixed
- **Silence cutting and every final render failed with ffmpeg 9** ("Unrecognized option
  'filter_complex_script'"). ffmpeg 9 removed that option. ClipTool now checks which option
  the installed ffmpeg supports (`-/filter_complex` on ffmpeg 7 and newer, the old option on
  ffmpeg 6 and older).
- **Transcription failed with "open() got an unexpected keyword argument 'metadata_errors'"**.
  faster-whisper's built-in audio reader (PyAV) is incompatible with some PyAV versions.
  ClipTool now reads the audio with ffmpeg itself and gives faster-whisper the decoded audio,
  so the PyAV version no longer matters.
- REPORT.txt said "Input videos: 0" when a video failed. It now shows how many videos were given
  and how many could not be processed.

### Added
- Tests for both fixes.

## [1.0.0] - 2026-10-03 - First release

### Added
- **Interactive menu** (`run.sh` / `ClipTool.bat` / `python -m cliptool`) with numbered options, so
  no commands need to be remembered. Every option is also available as a command (`cliptool --help`).
- **One-step installers**: `install.sh` (macOS/Linux) and `install.bat` (Windows). They create a
  private Python environment, offer to install ffmpeg, install everything, copy
  `config.example.yaml` to `config.yaml` and run a setup check.
- **Setup check** (`cliptool doctor`, menu 13) that lists what is installed and how to fix anything missing.
- **Transcription** with Whisper (faster-whisper, or openai-whisper as a fallback). Saves `.txt`,
  `.srt`, `.vtt` and `.json`. Results are cached so re-runs don't transcribe again.
- **Auto silence-cutter**: removes dead air with ffmpeg's silence detection (or auto-editor if
  enabled). Captions and clip times are re-timed to match automatically; the report gives clip
  times for both the original and the cut version.
- **TikTok-style captions**: short bold word groups with the spoken word highlighted and "popped",
  burned in with ffmpeg/libass. Font, colors, size, position and words-per-screen are configurable;
  optional per-speaker colors.
- **16:9 -> 9:16 reformatter** with three modes: `smart` (follows faces using OpenCV), `crop`
  (center crop) and `blur` (whole frame over a blurred background). Works for any target shape.
- **Clip finder**: scores every 30-60s window of a transcript on hook strength, emotion, questions
  and exclamations, pace, vocal energy, topic focus and clean sentence boundaries, and returns the
  best non-overlapping clips with reasons.
- **Best-of compilation**: picks the strongest moments across multiple shorts and combines them into
  one short of a target length.
- **Long video from shorts**: joins multiple shorts into one 16:9 YouTube video (vertical shorts on a
  blurred background) with automatically generated YouTube chapters.
- **Speaker identification**: built-in voice clustering (no extra installs) or pyannote.audio for
  high accuracy. Speakers can be mapped to names in `config.yaml`.
- **Expression detection** per spoken line: neutral, happy, excited, sad, mad, angry, surprised,
  scared - from the words, punctuation and voice energy (or a transformer model if installed).
- **Speaker pictures (avatars)**: shows a picture of whoever is talking with the matching
  expression; optional "mouth open" `_talk` images alternate while they speak. Layouts: only the
  active speaker, or everyone with the quiet people dimmed. An editable timeline `.json` lets you
  fix any speaker/expression and re-render.
- **Batch exporter**: process a whole drop folder (optionally keep watching it) into platform-ready
  files for TikTok, YouTube Shorts, Instagram Reels, YouTube 1080p/4K with the right resolution,
  frame rate, bitrate and loudness (-14 LUFS) for each platform.
- **Title, description and hashtag drafts** from the transcript, per platform (built-in, or Claude
  AI when `ANTHROPIC_API_KEY` is set). Saved next to every exported video as a `.txt`.
- **Optional Claude AI** re-ranking of clip suggestions.
- **REPORT.txt** in every job folder: plain-English summary of the best clips with exact time
  ranges, why each was picked, a ready-to-run cut command, speakers and talk time, expressions,
  upload drafts, chapters and any problems. Plus `best_clips.csv` for spreadsheets.
- **Logging**: `logs/cliptool.log` (all runs) and `job.log` in each job folder.
- Automated tests (`pytest`) covering the logic and full ffmpeg renders.
