"""Editor tests: project maths, Python/JS parity, rendering and the local web API."""
import json
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from cliptool.editor import project as proj
from cliptool.editor import render
from cliptool.ffmpeg_utils import probe

from conftest import fake_transcript, needs_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
MODEL_JS = ROOT / "cliptool" / "editor" / "static" / "js" / "model.js"


def sample_project() -> dict:
    p = proj.new_project("t")
    p["media"] = [{"id": "m1", "name": "a.mp4", "path": "media/a.mp4", "duration": 12, "width": 640, "height": 360,
                   "has_audio": True}]
    p["clips"] = [
        {"id": "c1", "media": "m1", "in": 0, "out": 4},
        {"id": "c2", "media": "m1", "in": 5, "out": 6, "transition": {"type": "fade", "duration": 2}},  # clamped
        {"id": "c3", "media": "m1", "in": 6, "out": 9, "transition": {"type": "slideleft", "duration": 0.5}},
        {"id": "c4", "media": "m1", "in": 9, "out": 12, "transition": {"type": "nope", "duration": 1}},  # ignored
    ]
    p["captions"] = [{"id": "k1", "start": 0.2, "end": 2.0, "text": "Hello there, how are you doing today?"},
                     {"id": "k2", "start": 3.0, "end": 4.5, "text": "Great!", "words": [
                         {"start": 3.1, "end": 3.6, "text": "Great!"}]}]
    return proj.normalize(p)


def test_timeline_maths():
    p = sample_project()
    assert proj.transition_duration(p["clips"], 1) == 0.45  # 45% of the 1s clip
    assert proj.transition_duration(p["clips"], 3) == 0.0
    assert proj.transition_duration(p["clips"], 2) == 0.45  # also limited by the 1s clip before it
    assert proj.clip_starts(p["clips"]) == [0.0, 3.55, 4.1, 7.1]
    assert proj.total_duration(p) == 10.1


def test_normalize_drops_broken_clips_and_fills_defaults():
    p = proj.normalize({"media": [], "clips": [{"media": "missing", "in": 0, "out": 1}], "preset": "bogus"})
    assert p["clips"] == [] and p["preset"] == "tiktok"
    assert p["frame"]["style"] == "blur" and p["caption_style"]["max_words"] == 3


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_browser_and_python_timeline_agree(tmp_path):
    """The preview (model.js) and the renderer must put clips and caption words at the same times."""
    p = sample_project()
    script = tmp_path / "parity.mjs"
    script.write_text(f"""
import * as m from '{MODEL_JS.as_uri()}';
const p = {json.dumps(p)};
const T = {json.dumps(proj.TRANSITIONS)};
const caps = [];
for (let t = 0; t < 5; t += 0.05) {{ const c = m.captionAt(p, t); caps.push(c ? c.words.map(w => w.text).join(' ') + '|' + c.active : ''); }}
console.log(JSON.stringify({{ starts: m.clipStarts(p.clips, T), total: m.totalDuration(p, T), caps }}));
""", encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)
    assert out["starts"] == proj.clip_starts(p["clips"])
    assert abs(out["total"] - proj.total_duration(p)) < 1e-9
    groups = render.caption_groups(p)
    py_caps = []
    for i in range(100):
        t = i * 0.05
        g = next((g for g in groups if g.start <= t < g.end), None)
        if not g:
            py_caps.append("")
            continue
        active = max(j for j, w in enumerate(g.words) if t >= (g.start if j == 0 else w.start))
        py_caps.append(" ".join(w.text.strip() for w in g.words) + f"|{active}")
    mismatches = [(i * 0.05, a, b) for i, (a, b) in enumerate(zip(out["caps"], py_caps)) if a != b]
    # allow float jitter right on a boundary
    assert len(mismatches) <= 2, mismatches


def test_effects_chain_builds_valid_expressions():
    fx = render.effects_chain([
        {"type": "zoom_in", "start": 1, "end": 2, "params": {"amount": 1.5, "x": 0.3, "y": 0.4}},
        {"type": "shake", "start": 2, "end": 3, "params": {"intensity": 1}},
        {"type": "flash", "start": 0.5, "end": 0.8, "params": {}},
        {"type": "black_white", "start": 4, "end": 5, "params": {}},
        {"type": "unknown", "start": 0, "end": 1},
    ], 1080, 1920)
    assert fx[0].startswith("zoompan=") and "in_time" in fx[0]
    assert any(f.startswith("hue=s=0") for f in fx) and any("eq=brightness" in f for f in fx)


def test_ass_has_positions_person_colors_and_text_overlays():
    p = sample_project()
    p["people"] = [{"id": "p1", "name": "Dave", "color": "#00FF00", "avatars": {}}]
    p["captions"][0]["person"] = "p1"
    p["overlays"] = [{"id": "o1", "type": "text", "text": "HELLO", "x": 0.5, "y": 0.1, "start": 0, "end": 2}]
    ass = render.build_ass(proj.normalize(p))
    assert "\\pos(540,1382)" in ass  # caption x=0.5, y=0.72 of 1080x1920
    assert "&H00FF00&" in ass  # Dave's green (BGR)
    assert "HELLO" in ass and "\\pos(540,192)" in ass


@needs_ffmpeg
def test_render_project_end_to_end(media_dir, tmp_path):
    store = proj.ProjectStore(tmp_path / "projects")
    pid, p = store.create("Render test")
    d = store.dir(pid)
    shutil.copy(media_dir / "second.mp4", d / "media" / "a.mp4")
    info = probe(d / "media" / "a.mp4")
    p["media"] = [{"id": "m1", "name": "a.mp4", "path": "media/a.mp4", "duration": info.duration,
                   "width": info.width, "height": info.height, "has_audio": True}]
    p["clips"] = [{"id": "c1", "media": "m1", "in": 0, "out": 2.5},
                  {"id": "c2", "media": "m1", "in": 6, "out": 8.5, "transition": {"type": "wipeleft", "duration": 0.5}}]
    (d / "avatars" / "p1").mkdir(parents=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=100x100",
                    "-frames:v", "1", str(d / "avatars" / "p1" / "happy.png")], check=True)
    p["people"] = [{"id": "p1", "name": "Dave", "color": "#FFFFFF", "avatars": {"happy": "avatars/p1/happy.png"}}]
    p["captions"] = [{"id": "k1", "start": 0.2, "end": 2.0, "text": "Best day ever", "person": "p1",
                      "expression": "lol"}]
    p["effects"] = [{"id": "e1", "type": "zoom_in", "start": 0.5, "end": 1.5, "params": {}},
                    {"id": "e2", "type": "glitch", "start": 3, "end": 3.5, "params": {}}]
    p["overlays"] = [{"id": "o1", "type": "image", "src": "avatars/p1/happy.png", "x": 0.8, "y": 0.1, "w": 0.2,
                      "start": 0, "end": 4}]
    p["frame"]["style"] = "blur_dark"
    p = store.save(pid, p)
    out = render.render_video(p, store, pid, d / "exports" / "out.mp4", d / "cache" / "w", "draft")
    i = probe(out)
    assert (i.width, i.height) == (1080, 1920) and i.has_audio
    assert abs(i.duration - 4.5) < 0.15  # 2.5 + 2.5 - 0.5 transition
    frame = render.render_frame(p, store, pid, 1.0, d / "cache" / "f.jpg", d / "cache" / "w2")
    assert frame.stat().st_size > 1000
    # avatar ('lol' falls back to 'happy') is drawn above the caption: red pixels there
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(frame), "-vf",
                          "crop=60:60:510:1150,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    assert raw[0] > 150 and raw[1] < 100, raw[:3]


@needs_ffmpeg
def test_web_api(media_dir, cfg, tmp_path, monkeypatch):
    from cliptool.editor import server as srv

    cfg["paths"]["projects_dir"] = str(tmp_path / "projects")
    httpd = srv.make_server(cfg, 8811)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(method, url, data=None, raw=None, headers=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        req = urllib.request.Request(base + url, data=body, method=method, headers=headers or {})
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), dict(r.headers)

    try:
        assert b"ClipTool Editor" in call("GET", "/")[1]
        meta = json.loads(call("GET", "/api/meta")[1])
        assert "face_slap" in meta["expressions"] and "rofl" in meta["expression_fallbacks"]
        pid = json.loads(call("POST", "/api/projects", {"name": "Web test"})[1])["id"]
        media = json.loads(call("POST", f"/api/projects/{pid}/media?name=clip.mp4",
                                raw=(media_dir / "second.mp4").read_bytes())[1])
        assert media["duration"] > 11 and media["thumb"]
        # range requests (needed for seeking in the browser)
        status, body, headers = call("GET", f"/api/projects/{pid}/media/{media['id']}", headers={"Range": "bytes=10-19"})
        assert status == 206 and len(body) == 10 and headers["Content-Range"].startswith("bytes 10-19/")
        p = json.loads(call("GET", f"/api/projects/{pid}")[1])
        p["clips"] = [{"id": "c1", "media": media["id"], "in": 0, "out": 3}]
        assert json.loads(call("PUT", f"/api/projects/{pid}", p)[1])["duration"] == 3
        status, jpg, headers = call("GET", f"/api/projects/{pid}/frame?t=1.0")
        assert status == 200 and jpg[:2] == b"\xff\xd8"
        # sticker upload + path traversal is refused
        img = json.loads(call("POST", f"/api/projects/{pid}/image?name=sticker.png",
                              raw=(media_dir / "tall.mp4").read_bytes()[:10])[1])
        assert img["path"] == "media/sticker.png"
        with pytest.raises(urllib.error.HTTPError):
            call("GET", f"/files/{pid}/../../etc/passwd")
        # automatic captions (Whisper stubbed)
        monkeypatch.setattr("cliptool.transcribe.transcribe",
                            lambda path, c, cache_dir=None: fake_transcript(str(path), 12))
        job = json.loads(call("POST", f"/api/projects/{pid}/transcribe", {"speakers": False, "expressions": True})[1])
        import time
        for _ in range(100):
            j = json.loads(call("GET", f"/api/jobs/{job['id']}")[1])
            if j["status"] != "running":
                break
            time.sleep(0.1)
        assert j["status"] == "done", j
        p = json.loads(call("GET", f"/api/projects/{pid}")[1])
        assert p["captions"] and p["captions"][0]["text"].startswith("Here's the secret")
        assert all(c["end"] <= 3.01 for c in p["captions"])  # only the part inside the 0-3s clip
        assert {c["expression"] for c in p["captions"]} - {"neutral"}
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------------------
# 1.2: text styles & titles, expressions, audio / video overlays, recordings
# ---------------------------------------------------------------------------
from cliptool.editor import textfx  # noqa: E402

TEXTFX_JS = ROOT / "cliptool" / "editor" / "static" / "js" / "textfx.js"


def fake_measure(text, font, size, bold):
    return len(text) * size * (0.7 if "Black" in font else 0.55)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("template", list(textfx.TITLE_TEMPLATES))
def test_title_layout_matches_browser(tmp_path, template):
    o = {"template": template, "text": "Golf time", "subtitle": "Best shots", "size": 80, "x": 0.4, "y": 0.3}
    py = textfx.title_layout(o, 1080, 1920, fake_measure)
    script = tmp_path / "t.mjs"
    script.write_text(f"""
import {{ titleLayout }} from '{TEXTFX_JS.as_uri()}';
const m = (t, f, s, b) => t.length * s * (f.includes('Black') ? 0.7 : 0.55);
console.log(JSON.stringify(titleLayout({json.dumps(o)}, {json.dumps(textfx.TITLE_TEMPLATES)}, 1080, 1920, m)));
""", encoding="utf-8")
    js = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)
    for key in ("x0", "y0", "w", "h", "rot"):
        assert abs(py[key] - js[key]) < 0.01, key
    assert len(py["parts"]) == len(js["parts"])
    for a, b in zip(py["parts"], js["parts"]):
        assert a["kind"] == b["kind"] and a["anim"] == b["anim"] and abs(a["delay"] - b["delay"]) < 1e-9
        for k in ("x", "y", "w", "h", "r", "size"):
            if k in a:
                assert abs(a[k] - b[k]) < 0.01, (template, k)


def test_text_animation_events():
    o = {"type": "text", "style": "hackerman", "text": "Hey you", "x": 0.5, "y": 0.5, "size": 60, "start": 1, "end": 4,
         "anim_in": "typewriter", "anim_out": "slide_left", "anim_dur": 0.5}
    ev = textfx.text_events(o, 1080, 1920)
    assert len(ev) == 7 + 1 + 1  # one step per character (space included), hold, exit
    assert "\\alpha&HFF&" in ev[0] and "\\move(" in ev[-1] and "Share Tech Mono" in ev[0]
    hollow = textfx.style_tags("outline", {}, 50, 1080, 1920)[1]
    assert "\\1a&HFF&" in hollow
    assert textfx.style_tags("highlighter", {}, 50, 1080, 1920)[0] == "Box"


def test_bundled_fonts_present():
    names = {f.name for f in textfx.font_files()}
    for family, files in textfx.FONT_FILES.items():
        for f in files.values():
            assert f in names, (family, f)


def test_expressions_and_audio_items_normalize():
    p = proj.normalize({"expressions": [{"id": "happy", "label": "Happy", "emoji": "😊"}],
                        "media": [{"id": "m1", "kind": "audio", "path": "x.webm", "duration": 5}],
                        "audio": [{"media": "m1", "in": 1, "out": 3, "start": 10, "end": 99}],
                        "captions": [{"start": 0, "end": 1, "text": "hi", "expression": "removed_one"}]})
    assert p["expressions"][0]["id"] == "neutral"  # neutral can't be removed
    assert p["captions"][0]["expression"] == "neutral"
    assert p["audio"][0]["end"] == 12.0  # start + (out - in)
    assert p["clips"] == []  # audio media can't be a video clip


@needs_ffmpeg
def test_alpha_detection(tmp_path):
    from cliptool.editor.server import _has_alpha
    clear = tmp_path / "clear.webm"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=red@0.0:s=64x64:d=0.5,format=yuva420p,drawbox=x=10:y=10:w=20:h=20:color=blue:t=fill",
                    "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", str(clear)], check=True)
    solid = tmp_path / "solid.webm"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.5",
                    "-c:v", "libvpx-vp9", "-metadata:s:v", "alpha_mode=1", str(solid)], check=True)
    assert _has_alpha(clear) is True
    assert _has_alpha(solid) is False  # tagged like a Chrome webcam recording, but nothing is see-through


@needs_ffmpeg
def test_render_audio_track_and_video_overlay(media_dir, tmp_path):
    store = proj.ProjectStore(tmp_path / "projects")
    pid, p = store.create("Overlay test")
    d = store.dir(pid)
    shutil.copy(media_dir / "second.mp4", d / "media" / "a.mp4")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x00FF00:s=160x120:d=2,"
                    "drawbox=x=50:y=30:w=60:h=60:color=red:t=fill", "-c:v", "libvpx-vp9", str(d / "media" / "g.webm")],
                   check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=2", "-c:a", "libopus",
                    str(d / "media" / "v.webm")], check=True)
    p["media"] = [{"id": "m1", "kind": "video", "name": "a.mp4", "path": "media/a.mp4", "duration": 12, "width": 640,
                   "height": 360, "has_audio": True},
                  {"id": "m2", "kind": "video", "name": "g.webm", "path": "media/g.webm", "duration": 2, "width": 160,
                   "height": 120, "has_audio": False},
                  {"id": "m3", "kind": "audio", "name": "v.webm", "path": "media/v.webm", "duration": 2, "has_audio": True}]
    p["clips"] = [{"id": "c1", "media": "m1", "in": 0, "out": 3}]
    p["overlays"] = [{"id": "o1", "type": "video", "media": "m2", "in": 0, "out": 2, "start": 0.5, "x": 0.5, "y": 0.5,
                      "w": 0.5, "shape": "circle", "chroma": "green"},
                     {"id": "o2", "type": "title", "template": "underline", "text": "Hi", "x": 0.5, "y": 0.2,
                      "size": 80, "start": 0, "end": 3}]
    p["audio"] = [{"id": "a1", "media": "m3", "in": 0, "out": 2, "start": 1, "volume": 1, "fade_out": 0.5}]
    p["frame"].update(style="color", color="#0000FF", scale=0.2)  # tiny main video: blue around the overlay
    p = store.save(pid, p)
    out = render.render_video(p, store, pid, d / "exports" / "o.mp4", d / "cache" / "w", "draft")
    assert abs(probe(out).duration - 3) < 0.15
    frame = render.render_frame(p, store, pid, 1.0, d / "cache" / "f.png", d / "cache" / "w2")

    def px(x, y):
        return subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(frame), "-vf", f"crop=4:4:{x}:{y},scale=1:1",
                               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout

    centre = px(538, 958)
    assert centre[0] > 180 and centre[1] < 90  # red figure from the overlay
    corner = px(300, 760)  # inside the overlay's square but outside its circle: background shows
    assert corner[2] > 180 and corner[1] < 90, corner


@needs_ffmpeg
def test_recording_upload_is_made_seekable(media_dir, cfg, tmp_path):
    from cliptool.editor import server as srv
    cfg["paths"]["projects_dir"] = str(tmp_path / "projects")
    app = srv.EditorApp(cfg)
    pid, _ = app.store.create("Rec")
    raw = app.store.dir(pid) / "media" / "voice.webm"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=400:d=1.5", "-c:a", "libopus",
                    str(raw)], check=True)
    entry = app.add_media(pid, raw, "media/voice.webm", recording=True)
    assert entry["kind"] == "audio" and entry["path"].endswith("_rec.webm") and entry["thumb"].endswith(".png")
    assert abs(entry["duration"] - 1.5) < 0.1 and entry["recording"] is True
    assert not raw.exists()


def test_key_handlers_do_not_swallow_typing():
    """`el.onkeydown = (e) => e.key === 'Enter' && go()` returns false for every other key, which
    cancels it: nothing could be typed into the new-project name (bug reported in 1.2)."""
    import re
    static = Path(__file__).resolve().parent.parent / "cliptool" / "editor" / "static" / "js"
    bad = []
    for f in static.glob("*.js"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.onkey(down|press|up)\s*=\s*\(?\w*\)?\s*=>\s*[^{\s]", line):
                bad.append(f"{f.name}:{n}: {line.strip()}")
    assert not bad, "key handlers must use a { block } body:\n" + "\n".join(bad)
