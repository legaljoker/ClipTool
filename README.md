# ClipTool

Turn raw recordings into finished **TikTok**, **YouTube Shorts**, **Instagram Reels** and
**YouTube** videos.

- **Video editor**: a drag-and-drop editor that opens in your web browser. It has a live preview,
  a timeline with cut and trim, effects with adjustable duration bars, transitions, zoom, text,
  stickers, and captions that show each speaker's avatar with the right expression.
  See section 3.
- **Auto silence-cutter**: removes dead air.
- **Auto captions**: Whisper transcription burned in as bold TikTok-style captions, with the spoken
  word highlighted.
- **16:9 to 9:16 reformatter**: keeps the whole video in the middle with a background style
  (blur by default, plus dark blur, black & white blur, pixelate, mirror, color, gradient or black),
  or fills the screen by cropping (follow faces / center crop).
- **Clip finder**: scans a long VOD and suggests the best 30-60 second moments.
- **Best-of**: picks the best moments from several shorts and combines them into one short.
- **Long video**: joins several shorts into one YouTube video with chapters.
- **Speakers and expressions**: works out who is talking and how they sound (happy, sad, mad, angry
  and others). It can put that person's picture with the matching expression on the video.
- **Batch exporter**: you drop videos in one folder and get files sized and encoded for each platform.
- **Title, description and hashtag drafts**, written from the transcript.
- **REPORT.txt** for every job. It lists the best clip, the time range to use, why it was picked,
  the upload drafts and any problems.
- **CHANGELOG.md** records every change made to the application.

---

## 1. Install (one time)

### Windows
1. Install **Python 3.10 or newer** from [python.org](https://www.python.org/downloads/).
   During setup, tick **"Add python.exe to PATH"**.
2. Download or clone this folder, then double-click **`install.bat`**.
   - If ffmpeg is missing, the installer offers to install it with `winget`. Afterwards, close the
     window and run `install.bat` again.
3. Double-click **`ClipTool.bat`** to start.

### macOS / Linux
```bash
bash install.sh      # installs everything (offers to install ffmpeg via brew / apt)
./run.sh             # starts ClipTool
```

The installer:
- creates a private Python environment in `.venv`
- installs ClipTool
- creates `config.yaml`
- runs a **setup check**

You can run the setup check again at any time from menu option 13, or with `./run.sh doctor`.

> The first transcription downloads the Whisper speech model (about 500 MB for `small`).
> This happens once.

---

## 2. Use it

Start ClipTool and choose a number:

```
   1) Quick short: remove silence + captions + vertical + titles
   2) Transcribe videos (text + subtitle files)
   3) Remove silence / dead air
   4) Add TikTok-style captions
   5) Reformat 16:9 -> 9:16 vertical
   6) Find the best clips in a long video (VOD)
   7) Best-of: combine the best moments of several shorts into ONE short
   8) Long video: join several shorts into ONE YouTube video
   9) Speakers & expressions (+ insert speaker pictures)
  10) Titles, descriptions & hashtags
  11) Batch export: process the whole drop folder
  12) Custom: pick any combination of options
  13) Check setup
  14) View change log
```

When ClipTool asks for videos, drag the files or a folder into the window and press Enter.
To use everything in the `input` folder, just press Enter.

Each job gets its own folder in `output/`:

```
output/quick_myvideo_2026-10-03_14-05-11/
  REPORT.txt            <- read this first
  best_clips.csv        <- the clip list, for spreadsheets
  videos/tiktok/myvideo.mp4
  videos/tiktok/myvideo.txt        <- title / description / hashtag drafts for this upload
  videos/youtube_shorts/myvideo.mp4
  transcripts/myvideo.txt .srt .vtt .json
  timelines/myvideo_timeline.json  <- who spoke when, with which expression (editable)
  job.log
```

### Example REPORT.txt excerpt
```
  BEST CLIPS, ranked (times are in the ORIGINAL recording):

   #1  00:12:03.400 -> 00:12:48.900  (45.5s)   score 8.3
       Suggested title: Why does this always happen to me?
       Opens with: "Why does this always happen to me?"
       Why it's good: strong opening hook; emotional language (mad, angry); high vocal energy
       What's said: Why does this always happen to me? That is so annoying...
       Cut it yourself: ffmpeg -ss 723.40 -i "stream.mp4" -t 45.50 -c:v libx264 -c:a aac clip1.mp4
```

### Command line (optional)
All menu options are also available as commands. Run `./run.sh --help` to list them
(on Windows, use `ClipTool.bat` instead of `./run.sh`).

```bash
./run.sh quick video.mp4 --platforms tiktok,youtube_shorts
./run.sh clips stream.mp4 --min 30 --max 60 --count 5 --render
./run.sh bestof short1.mp4 short2.mp4 short3.mp4
./run.sh longform shorts_folder/ --platforms youtube
./run.sh speakers podcast.mp4 --avatars --num-speakers 2
./run.sh batch input/ --captions --silence-cut --platforms tiktok,youtube_shorts --watch
./run.sh run video.mp4 --mode clips --silence-cut --captions --speakers --metadata
./run.sh platforms     # lists export sizes and bitrates
./run.sh changelog
```

---

## 3. The video editor

Start it with **`ClipTool-Editor.bat`** (Windows), **`./editor.sh`** (macOS/Linux), or menu option
**E**. It opens in your web browser. It runs only on your computer; nothing is uploaded to the
internet. Keep the black window open while you edit.

Each project is saved automatically in `projects/<name>/`. Finished videos go to `projects/<name>/exports/`.

| To do this | Do this |
|---|---|
| **Add videos** | Drag video files onto the window, or use **Media**. **Add from the input folder** uses the file where it is, without copying it. |
| **Pick a style** | **Style** tab: blur, blur dark, black & white blur, pixelate, mirror, color, gradient, black, or fill the screen. Sliders set the video size, its position and the blur amount. |
| **Cut out a part** | Put the playhead at the start and press **S**. Move to the end and press **S** again. Click the middle piece and press **Delete**. |
| **Trim** | Drag the left or right edge of a clip on the timeline. |
| **Add an effect** | Drag it from **Effects** onto the timeline or the video. **Drag the ends of its bar to set how long it lasts.** For zooms, drag the yellow circle on the video to choose the spot. |
| **Add a transition** | Drag it from **Transitions** onto the start of a clip (the **+**), or select a clip and pick one in the panel on the right. |
| **Zoom** | Use the timeline zoom slider, **+**/**−**, or Ctrl + mouse wheel. The **Zoom in** and **Zoom out** effects zoom the video itself. |
| **Captions** | **Captions** tab, **Create captions from speech**. ClipTool can also work out who is talking and guess expressions. You can edit any caption, and choose **who is talking** and their **expression** per caption. |
| **Place the captions** | Drag the caption on the video. |
| **People & avatars** | **People** tab: one person per speaker. Give each a picture for every expression: Neutral, Happy, Sad, Mad, Angry, ROFL, LOL, Disappointed, Face slap (plus Excited, Surprised, Scared). Drop your own images, or click **Cartoon faces** / **From a photo** to make a full set. Each set includes "talking" versions that alternate while the person speaks. |
| **Text & pictures** | **Text & Pics** tab, or drop a picture straight onto the video. Drag to move it and drag the yellow corner to resize. Its bar on the timeline sets when it shows. |
| **Check the real result** | **Exact preview** renders the current frame with ffmpeg, exactly as the final video will look. |
| **Render** | **Render video**. The video shape (TikTok / Shorts / Reels / YouTube / Square) sets the size and bitrate. |

Keyboard: **Space** play/pause · **S** cut · **Delete** delete · **Ctrl+Z / Ctrl+Y** undo/redo ·
**← →** step one frame (hold Shift for 1 second).

## 4. Speaker pictures with expressions (menu tools)

1. Run **9) Speakers & expressions** once without pictures. `REPORT.txt` shows who is
   `SPEAKER_1`, `SPEAKER_2` and so on.
2. Make a folder per person in `avatars/` and add one picture per expression:
   `neutral.png`, `happy.png`, `excited.png`, `sad.png`, `mad.png`, `angry.png`, `surprised.png`,
   `scared.png`. Only `neutral.png` is required; missing expressions fall back to a similar one.
   - Optional: add `happy_talk.png`, `angry_talk.png` and so on, with the mouth open. ClipTool
     switches between the two images while that person talks, so they look like they are speaking.
3. Name the speakers in `config.yaml`:
   ```yaml
   speakers:
     names:
       SPEAKER_1: Dave
       SPEAKER_2: Sarah
   ```
4. Run option 9 again and answer **yes** to "Insert speaker pictures".
   `avatars.layout: all` shows everyone at once and dims whoever is quiet.
5. If a line went to the wrong person or got the wrong expression, edit the
   `timelines/<video>_timeline.json` file. Then run option 9 again and give it that timeline file.

See `avatars/README.txt` for details.

---

## 5. Optional upgrades

| Upgrade | What it improves | How |
|---|---|---|
| Claude AI | Smarter clip ranking, better titles, descriptions and hashtags | `pip install anthropic`, then set the environment variable `ANTHROPIC_API_KEY` |
| pyannote | Much more accurate speaker identification | `pip install pyannote.audio`. Then create a free token at huggingface.co, accept the terms of `pyannote/speaker-diarization-3.1`, and put the token in `config.yaml` under `speakers.huggingface_token` |
| transformers | AI expression detection from the words | `pip install transformers torch` |
| auto-editor | Alternative silence cutter | `pip install auto-editor`, then set `silence.use_auto_editor: true` |
| NVIDIA GPU | Much faster transcription | Set `transcription.device: cuda` |

Run these `pip` commands inside the ClipTool environment. Either answer **y** in the installer,
or activate the environment first: `.venv\Scripts\activate` on Windows,
`source .venv/bin/activate` on macOS/Linux.

---

## 6. Settings

Edit **`config.yaml`**. Every setting has a comment. The ones people change most:

- `transcription.model`: `base` is faster, `medium` is more accurate.
- `silence.threshold_db` / `min_silence`: how aggressive silence cutting is.
- `captions`: font, colors, words per screen, position.
- `reformat.style`: `blur` (the default for TikTok and Shorts), `blur_dark`, `blur_bw`, `pixelate`,
  `mirror`, `color`, `gradient`, `black`, `smart` or `crop`. `reformat.scale` sets the size of the
  video in the middle.
- `clips.min_length` / `max_length` / `count`.
- `export.platforms`: the default platforms.

### Platform presets
| Platform | Size | Video bitrate | Notes |
|---|---|---|---|
| tiktok | 1080x1920 | 8 Mbps | |
| youtube_shorts | 1080x1920 | 10 Mbps | |
| instagram_reels | 1080x1920 | 8 Mbps | |
| youtube | 1920x1080 | 12 Mbps | |
| youtube_4k | 3840x2160 | 45 Mbps | |
| source | original size | CRF 18 | |

All presets use H.264 and AAC audio at 48 kHz, with fast-start so playback can begin while the
file downloads. Volume is normalized to -14 LUFS, the loudness these platforms target.

---

## 7. Troubleshooting

- **"ffmpeg was not found"**: install ffmpeg (see section 1), then open a new terminal window.
- **Transcription is slow**: set `transcription.model: base` in `config.yaml`, or use a GPU.
- **Captions use the wrong font**: set `captions.font` to a font that is installed on your computer.
- **Speakers are mixed up**: set `num_speakers`, install pyannote, or edit the timeline file.
- **The editor preview is black for a video**: some formats (for example HEVC from phones, or MKV)
  can't play in a browser. ClipTool makes a preview copy automatically; wait for "preparing preview"
  to disappear in **Media**. The final render always uses the original file.
- Everything ClipTool does is logged in `logs/cliptool.log`, and in `job.log` inside each job folder.

## For developers
```bash
pip install -e ".[dev]"
pytest            # unit tests + real ffmpeg renders on synthetic videos (Whisper is stubbed)
```
The code is in `cliptool/`. `pipeline.py` runs jobs, `export.py` handles platform rendering, and
`report.py` writes REPORT.txt. Record every change in `CHANGELOG.md`.
