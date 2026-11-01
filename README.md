# ClipTool

Turn raw recordings into finished **TikTok**, **YouTube Shorts**, **Instagram Reels** and
**YouTube** videos.

- **Video editor**: a drag-and-drop editor that opens in your web browser. It has a live preview,
  a timeline with cut and trim, effects with adjustable duration bars, transitions, zoom, text,
  stickers, and captions that show each speaker's avatar with the right expression.
  See section 3.
- **Auto Edit**: one click tightens the video (cuts pauses, "um/uh" and false starts at quiet
  moments), hides jump cuts with punch-in zooms, adds zoom punches, a hook title, call-outs,
  bleeps, a cold-open teaser, sound effects and background music that dips under the voice.
  See section 3b.
- **AI Editor**: tell Claude what you want ("cut the part about the weather and add a hook") and it
  edits your timeline. Needs a Claude API key in **Settings**.
- **Video check**: finds black or frozen frames, silences, loudness and clipping problems, caption
  mistakes and text hidden behind the TikTok buttons before you upload.
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

### Windows: the ClipTool Editor app (recommended)
1. Go to the **[Releases page](https://github.com/legaljoker/ClipTool/releases)** and download
   **`ClipTool-Editor-Setup-<version>.exe`**.
2. Run it. It installs for your user account only, so no administrator password is needed, and it
   adds ClipTool Editor to the Start menu (and optionally the desktop).
3. Start **ClipTool Editor**. It opens in its own window.

Nothing else is needed: Python and ffmpeg are built in. Your projects and finished videos are kept in
**Documents\ClipTool**. Windows may show "Windows protected your PC" the first time, because the app
isn't code-signed. Click **More info → Run anyway**.

Prefer no installer? Download `ClipTool-Editor-<version>-portable.zip`, unzip it anywhere, and run
`ClipTool-Editor.exe`.

### Windows: menu tools and command line (from source)
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

Start **ClipTool Editor** from the Start menu (installed app). From source, use `ClipTool-Editor.bat`,
`./editor.sh` or menu option **E**. It runs only on your computer; nothing is uploaded to the internet.

Each project is saved automatically in `Documents\ClipTool\projects\<name>\` (installed app) or
`projects/<name>/` (source). Finished videos go to the project's `exports` folder.

**Everything you drag is live.** While you drag an effect, text or title over the timeline or the
video, it already shows in the preview. A ghost bar and a time tooltip show exactly where it will
land. Moving or resizing a bar also moves the preview to that spot. Drag a clip's middle to move it to
another place in the video; its captions and effects go with it.

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
| **Text** | **Text & Pics** tab. Choose from 11 styles: Plain, Bold, Clipped, Highlighter, Hackerman, Intuition, Meme, Outline, Pixel, Signature, Neon. Each text can animate in (fade, pop, slide, zoom, blur, typewriter) and out. |
| **Animated titles** | **Text & Pics → Animated titles**: motion titles with a second line (bar, slanted bar, accent box, accent circle, underline, line-in). Pick your own colors. |
| **Pictures** | Drop a picture straight onto the video. Drag to move it and drag the yellow corner to resize. Its bar on the timeline sets when it shows. |
| **Record your voice** | **Record → Voice**: pick your microphone, press the round button to start, press again to stop. Listen back, then **Add at the playhead**. It goes on the **Audio** track, where you can drag it anywhere, drag its ends to trim it, split it (**S**), and set its volume and fade in/out. Up to 5 minutes per recording. |
| **Record your webcam** | **Record → Webcam**: pick a camera and microphone and record. Add it **on top of the video** (picture-in-picture: drag/resize it, make it a circle) or as a normal clip. |
| **VTuber** | Pick your VTuber app's virtual camera (VTube Studio, Veadotube, OBS Virtual Camera...) in **Record → Webcam**. Or upload a video of your 2D/3D model with **Record → VTuber / video on top**. Turn on **Remove background** for green or blue screens; transparent .webm files keep their transparency. |
| **Music & sounds** | **Record → Music / sound**, or drop audio files (mp3, wav, m4a...) onto the window. They go on the **Audio** track. |
| **Expressions** | **People** tab, at the bottom: add your own expressions (name + emoji) or remove ones you don't use. **Cartoon faces** makes a face for every expression; custom ones use their emoji. |
| **Check the real result** | **Exact preview** renders the current frame with ffmpeg, exactly as the final video will look. |
| **Render** | **Render video**. The video shape (TikTok / Shorts / Reels / YouTube / Square) sets the size and bitrate. |

Keyboard: **Space** play/pause · **S** cut · **Delete** delete · **Ctrl+Z / Ctrl+Y** undo/redo ·
**← →** step one frame (hold Shift for 1 second).

**Projects**: the **Projects** button lists your projects. ✏️ renames one, 🗑 deletes it (with all its
files, after you confirm).

### 3b. Auto Edit, AI Editor and Settings

**🪄 Auto Edit tab.** Tick what you want and press **Run Auto Edit**. Afterwards a report says what
changed; **Undo Auto Edit** (or Ctrl+Z) takes all of it back in one step.

| Option | What it does |
|---|---|
| Cut pauses and dead air | Measures the sound and cuts silences longer than the limit in **Settings**, keeping a short natural breath (0.12 s by default). Cuts never clip the start of a word and land on whole frames. Captions, effects and text move with the video. |
| Remove "um" / "uh", repeated words and false starts | "I I think" becomes "I think"; "we went, we went home" becomes "we went home". Repeats people say on purpose ("very very", "no no") stay. |
| Zoom in / out at cuts | Every other piece of a jump cut is slightly zoomed in, so the cut looks intentional. |
| Zoom punches on strong words | Quick zooms on words like "insane", "never", numbers and exclamations (at most one every 5 seconds), with a soft swoosh. |
| Hook title | A title banner over the first 3 seconds: your text, Claude's suggestion, or the strongest line. It shrinks to fit the screen. |
| Call-out text | "FOLLOW FOR MORE", "LINK IN BIO", "COMMENT BELOW 👇"... when the speaker says it, with a pop sound. |
| Bleep swear words | A bleep over the word, which is also masked in the captions ("f***"). |
| Cold open | Repeats the most quotable 2-5 seconds as a teaser at the very start, then flashes into the video. |
| Background music | Pick a music file from the project. It plays under the whole video and dips automatically while someone talks. |

Also on the Auto Edit tab:
- **Sound effects**: whoosh, swoosh, pop, click, ding, impact, riser, bleep, record scratch, boing.
  Click one to put it at the playhead. They are made by ClipTool itself (no downloads, no licences).
  With an ElevenLabs key you can **generate** new sounds or background music from a description.
- **Check the video**: renders a quick test (or uses your latest export) and lists problems with
  clickable times: black or frozen picture, long silences, too quiet / too loud (platforms expect
  about -14 LUFS), distortion, overlapping or too-fast captions, text cut off at the edges or hidden
  behind the app's buttons. Anything it could not check is listed as "not checked".
- **Copy a look**: use the captions, frame style, format and title look of another project.
- **Hook versions**: renders 2-6 versions, each opening with a different strong moment, to test
  which keeps viewers. A `hook_versions.txt` in the exports folder lists them.

**🤖 AI Editor tab.** Chat with Claude about your video: "tighten this and make the captions bigger",
"cut the part where I talk about my cat", "make a hook title and add a whoosh", "check the video
and fix what's wrong", or paste review notes. Claude reads the captions and the timeline, makes the
edits with the same tools you have, and tells you what it changed. Each answer is one Undo step.

**⚙️ Settings** (top bar):
- **API keys** for Claude (AI Editor, Auto Edit planning, smarter clip picks and titles) and
  ElevenLabs (generated sounds and music). Keys are stored only on this PC, encrypted for your Windows
  account, and sent only to that service. **Test** checks a key. Without keys everything else works.
- Claude model (Opus = best, Sonnet = faster, Haiku = fastest) and how thorough it is.
- Speech-to-text accuracy, language and graphics card.
- Auto Edit defaults: pause kept at cuts, which pauses to cut, fillers, repeats, punch-ins, bleeps,
  music level.
- Export file names, e.g. `{project}_{preset}_{date}` (also `{time}` and `{n}`).

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
| Claude AI | AI Editor, Auto Edit planning, smarter clip ranking, better titles, descriptions and hashtags | Add your key in the editor's **Settings** (or set the environment variable `ANTHROPIC_API_KEY`) |
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
