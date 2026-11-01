"""Self-test for the packaged app: `ClipTool-Editor.exe --selftest`.

Checks that ffmpeg and every filter the editor uses are present, renders a small
project that uses every kind of feature, talks to the editor's web server, and
(with --with-whisper) downloads the tiny speech model and transcribes a clip.
Results go to logs/selftest.log; the exit code is 0 only if everything passed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.request
from pathlib import Path

REQUIRED_FILTERS = ["ass", "xfade", "acrossfade", "zoompan", "chromakey", "alphamerge", "alphaextract", "geq",
                    "amix", "adelay", "afade", "loudnorm", "boxblur", "rgbashift", "vignette", "showwavespic",
                    "colorchannelmixer", "tpad", "apad"]


def run(cfg, with_whisper: bool = False) -> int:
    log_dir = cfg.resolve_dir("log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    out = open(log_dir / "selftest.log", "w", encoding="utf-8")
    results: list[tuple[str, bool, str]] = []

    def say(msg: str) -> None:
        out.write(msg + "\n")
        out.flush()
        print(msg)

    def check(name: str, fn) -> None:
        t = time.time()
        try:
            detail = fn() or ""
            results.append((name, True, detail))
            say(f"PASS  {name} ({time.time() - t:.1f}s) {detail}")
        except Exception as exc:
            results.append((name, False, str(exc)))
            say(f"FAIL  {name}: {exc}\n{traceback.format_exc()}")

    from cliptool import __version__
    say(f"ClipTool Editor {__version__} self-test")

    def t_ffmpeg():
        from cliptool.ffmpeg_utils import ffmpeg_bin, ffprobe_bin
        v = subprocess.run([ffmpeg_bin(), "-version"], capture_output=True, text=True).stdout.splitlines()[0]
        ffprobe_bin()
        return f"{ffmpeg_bin()} | {v}"

    def t_filters():
        from cliptool.ffmpeg_utils import ffmpeg_bin
        listing = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        names = {line.split()[1] for line in listing.splitlines() if len(line.split()) > 2}
        missing = [f for f in REQUIRED_FILTERS if f not in names]
        enc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        dec = subprocess.run([ffmpeg_bin(), "-hide_banner", "-decoders"], capture_output=True, text=True).stdout
        for codec, listing2 in (("libx264", enc), ("aac", enc), ("libvpx-vp9", dec)):
            if f" {codec} " not in listing2:
                missing.append(codec)
        if missing:
            raise RuntimeError("missing: " + ", ".join(missing))
        return f"{len(REQUIRED_FILTERS)} filters ok"

    def t_script_option():
        from cliptool.ffmpeg_utils import filter_script_args
        return " ".join(filter_script_args(Path("x"))[:1])

    work = Path(tempfile.mkdtemp(prefix="cliptool_selftest_"))

    def t_render():
        from cliptool.editor import render
        from cliptool.editor.project import ProjectStore
        from cliptool.ffmpeg_utils import ffmpeg, probe
        store = ProjectStore(work / "projects")
        pid, p = store.create("Self test")
        d = store.dir(pid)
        ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=640x360:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3",
                "-c:v", "libx264", "-c:a", "aac", "-shortest", d / "media" / "a.mp4"], desc="selftest clip")
        ffmpeg(["-f", "lavfi", "-i", "color=c=0x00FF00:s=320x240:d=2,drawbox=x=100:y=60:w=120:h=120:color=red:t=fill",
                "-c:v", "libvpx-vp9", "-b:v", "300k", d / "media" / "green.webm"], desc="selftest overlay")
        ffmpeg(["-f", "lavfi", "-i", "sine=f=300:d=2", "-c:a", "libopus", d / "media" / "voice.webm"], desc="selftest voice")
        ffmpeg(["-f", "lavfi", "-i", "color=c=orange:s=100x100", "-frames:v", "1", d / "avatars" / "happy.png"],
               desc="selftest avatar")
        p["media"] = [
            {"id": "m1", "kind": "video", "name": "a.mp4", "path": "media/a.mp4", "duration": 3, "width": 640, "height": 360, "has_audio": True},
            {"id": "m2", "kind": "video", "name": "green.webm", "path": "media/green.webm", "duration": 2, "width": 320, "height": 240, "has_audio": False},
            {"id": "m3", "kind": "audio", "name": "voice.webm", "path": "media/voice.webm", "duration": 2, "has_audio": True}]
        p["clips"] = [{"id": "c1", "media": "m1", "in": 0, "out": 2},
                      {"id": "c2", "media": "m1", "in": 1, "out": 3, "transition": {"type": "circleopen", "duration": 0.5}}]
        p["people"] = [{"id": "p1", "name": "Tester", "color": "#FFFFFF", "avatars": {"happy": "avatars/happy.png"}}]
        p["captions"] = [{"id": "k1", "start": 0.1, "end": 1.6, "text": "Self test captions", "person": "p1", "expression": "lol"}]
        p["effects"] = [{"id": "e1", "type": "zoom_in", "start": 0.2, "end": 1.0, "params": {}},
                        {"id": "e2", "type": "glitch", "start": 2.0, "end": 2.4, "params": {}}]
        p["overlays"] = [
            {"id": "o1", "type": "text", "style": "pixel", "text": "PIXEL", "x": 0.5, "y": 0.1, "size": 60, "start": 0, "end": 3, "anim_in": "typewriter"},
            {"id": "o2", "type": "title", "template": "circle", "text": "Motion", "subtitle": "Titles", "x": 0.5, "y": 0.2, "size": 70, "start": 0.5, "end": 3},
            {"id": "o3", "type": "video", "media": "m2", "in": 0, "out": 2, "start": 0.5, "x": 0.7, "y": 0.8, "w": 0.3, "shape": "circle", "chroma": "green"}]
        p["audio"] = [{"id": "a1", "media": "m3", "in": 0, "out": 2, "start": 1, "volume": 1, "fade_in": 0.3}]
        p = store.save(pid, p)
        video = render.render_video(p, store, pid, d / "exports" / "selftest.mp4", d / "cache" / "w", "draft")
        info = probe(video)
        if not (info.width == 1080 and info.height == 1920 and info.has_audio and abs(info.duration - 3.5) < 0.2):
            raise RuntimeError(f"unexpected output {info}")
        frame = render.render_frame(p, store, pid, 1.2, d / "cache" / "f.jpg", d / "cache" / "w2")
        return f"{info.duration:.2f}s video, frame {frame.stat().st_size} bytes"

    def t_server():
        from cliptool.editor.server import make_server
        server = make_server(cfg, 8899)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            html = urllib.request.urlopen(base + "/").read()
            assert b"ClipTool Editor" in html
            meta = json.loads(urllib.request.urlopen(base + "/api/meta").read())
            assert "text_styles" in meta and "face_slap" in meta["expressions"]
            for f in ("/static/js/app.js", "/static/js/record.js", "/static/css/app.css", "/static/fonts/Anton_400Regular.ttf"):
                assert len(urllib.request.urlopen(base + f).read()) > 100, f
            return base
        finally:
            server.shutdown()

    def t_webview():
        import webview  # noqa: F401
        return "pywebview " + getattr(webview, "__version__", "")

    def t_whisper():
        import faster_whisper  # noqa: F401
        import ctranslate2
        detail = f"ctranslate2 {ctranslate2.__version__}"
        if with_whisper:
            from cliptool.ffmpeg_utils import ffmpeg
            from cliptool.transcribe import transcribe
            wav = work / "tone.wav"
            ffmpeg(["-f", "lavfi", "-i", "sine=f=440:d=2", wav], desc="selftest tone")
            tr = transcribe(wav, {**cfg["transcription"], "model": "tiny", "engine": "faster-whisper"})
            detail += f", tiny model ran ({len(tr.segments)} segments)"
        return detail

    for name, fn in (("ffmpeg found", t_ffmpeg), ("ffmpeg filters & codecs", t_filters),
                     ("filter script option", t_script_option), ("render a full project", t_render),
                     ("editor web server", t_server), ("app window library", t_webview),
                     ("speech-to-text engine", t_whisper)):
        check(name, fn)
    shutil.rmtree(work, ignore_errors=True)
    failed = [r for r in results if not r[1]]
    say(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    out.close()
    return 1 if failed else 0
