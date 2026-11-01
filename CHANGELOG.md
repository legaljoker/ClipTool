# ClipTool change log

Every update or change to ClipTool is recorded here, newest first.
Read it from inside the app with menu option **14) View change log** or `cliptool changelog`.

Format: each release lists what was **Added**, **Changed**, **Fixed** or **Removed**.
When you change the application, add an entry at the top and bump the version in
`cliptool/__init__.py`.

---

## [1.2.0] - 2026-11-01 - Stand-alone app, text styles, recording, live dragging

### Added
- **ClipTool Editor is a real Windows program.** `ClipTool-Editor-Setup.exe` installs it (no admin
  needed), with a Start-menu and desktop shortcut. It opens in its own window and has Python and ffmpeg
  built in, so no batch files are needed to install or run it. A portable zip is also available. Your
  projects, settings and logs live in **Documents\ClipTool**. Speech models download there on first use.
- The Windows build is made automatically on GitHub. Each build runs a **self-test of the finished
  program** (renders a project using every feature, checks every ffmpeg filter, runs speech-to-text)
  before it is published on the Releases page. You can run it yourself with `ClipTool-Editor.exe --selftest`.
- **Text styles:** Plain, Bold, Clipped, Highlighter, Hackerman, Intuition, Meme, Outline, Pixel,
  Signature and Neon. They use bundled fonts (Montserrat, Anton, Bangers, Great Vibes, Press Start 2P,
  Share Tech Mono; SIL Open Font License), so they look the same on every PC and in the final video.
  Font picker and colour pickers per text.
- **Text animations** in and out: fade, pop, slide (4 directions), zoom, blur, typewriter, with an
  adjustable speed.
- **Animated titles** (motion titles with a second line): bar, slanted bar, accent box, accent circle,
  underline, line-in.
- **Record tab:**
  - **Voice:** microphone picker, 5-minute timer, live level meter, start/stop button, listen back,
    then add at the playhead.
  - **Webcam:** camera + microphone picker, live preview. Add the recording on top of the video
    (picture-in-picture) or as a clip.
  - **Uploads** for VTuber / overlay videos and for music or sounds.
- **Audio track** on the timeline, showing a waveform. Move, trim by dragging its ends, split (S),
  volume, and fade in/out. Mixed into the final video.
- **Video on top (picture-in-picture):**
  - size and position by dragging on the video
  - rectangle or circle shape
  - green / blue / custom-colour background removal, previewed live
  - transparent .webm VTuber videos keep their transparency
  - trim and split like audio
- **Add or remove expressions** (People tab). Custom expressions get an emoji face from
  **Cartoon faces**. Removing one turns its captions back to Neutral.
- **Live dragging everywhere:**
  - Effects, text and titles show in the preview while you drag them over the timeline or video,
    with a ghost bar and a time tooltip.
  - Moving or resizing a bar moves the preview to that spot.
  - Clips can be dragged to a new place, and their captions and effects move with them.
- **What's new** button in the editor shows this change log.
- `cliptool app` command opens the editor window from source.

### Fixed
- Overlapping text and pictures in the timeline no longer hide behind each other (lanes).
- Webcam recordings are no longer mistaken for see-through videos. Chrome marks them that way,
  so ClipTool now checks the actual pixels.
- Browser recordings are rewritten on import, so they have a proper length and can be seeked.

### Changed
- "Smart" (face-following) cropping was removed from the editor's style list because the editor never
  supported it. It is still available in the menu tools.

## [1.1.0] - 2026-10-31 - Video editor and frame styles

### Added
- **Video editor in your web browser** (`ClipTool-Editor.bat`, `./editor.sh`, menu option **E**, or
  `cliptool editor`). It runs only on your computer.
  - Live preview of every option: style, transitions, effects, captions, avatars, text and pictures.
  - **Exact preview** renders the current frame with ffmpeg so you see the real result.
  - Drag and drop: add videos, effects, transitions, text and pictures onto the timeline or the
    video. Drag captions, text and pictures on the video to place them, and resize pictures/text
    with their corner handle.
  - Timeline with tracks for captions, text & pictures, effects and video. Overlapping items stack
    into lanes. Timeline zoom (slider, +/−, Ctrl + mouse wheel), snapping, and a playhead you can drag.
  - **Cut tool**: cut at the playhead (S), delete pieces, trim clip edges, and move clips earlier or
    later. Captions and effects after a cut move with the video.
  - **Effects with duration bars** (drag the ends to set how long they last): zoom in, zoom out,
    camera shake, flash, black & white, sepia, blur, vignette, invert, glitch. You choose the zoom
    spot on the video.
  - **Transitions** between clips: crossfade, fade through black/white, dissolve, wipes, slides,
    circle open/close, radial, pixelize, zoom. Adjustable length.
  - **Captions with people**: create captions from speech (optionally detecting who is talking and
    their expression), edit text and timing, and choose Person 1, Person 2... and an expression
    for each caption. The person's avatar for that expression is shown with the caption in the
    final video, above, left or right of the text, and alternates with a "talking" image while
    they speak.
  - **People & avatars**: a picture per expression for each person. Drop your own images, or
    generate a full set of cartoon faces or a set from one photo.
  - Projects save automatically, with unlimited undo/redo.
  - Rendering shows progress and can be cancelled. Uses the TikTok / Shorts / Reels / YouTube /
    Square size and bitrate.
- **New expressions:** ROFL, LOL, Disappointed and Face slap, with automatic detection from the words
  ("lmao", "haha", "oh well", "facepalm"...) and sensible picture fallbacks
  (ROFL → LOL → Happy, Face slap → Disappointed → Mad...).
- **Frame styles** that keep the whole video in the middle: blur, blur_dark, blur_bw, pixelate,
  mirror, color, gradient, black. Settings for the size and position of the middle video.
- Tests for the editor, including a check that the browser preview and the final render put clips
  and caption words at the same times.

### Changed
- **Blur is now the default** for TikTok, YouTube Shorts and Reels everywhere (menu, commands,
  batch). It is controlled by the new `reformat.style` setting, so older config files that said
  `mode: smart` no longer override it.
- The menu lists all frame styles when it asks how to make a video vertical.

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
