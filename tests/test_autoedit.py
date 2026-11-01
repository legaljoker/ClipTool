"""Auto Edit, Settings, sound effects, video check and the AI Editor."""
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from cliptool import settings
from cliptool.editor import agent, autoedit as ae, qa, render
from cliptool.editor.project import ProjectStore, normalize, total_duration
from cliptool.ffmpeg_utils import decode_audio, probe
from cliptool.transcribe import Segment, Transcript, Word
from tests.conftest import fake_transcript, needs_ffmpeg


def words(spec):
    """[(text, start, end), ...] -> Words"""
    return [Word(a, b, " " + t) for t, a, b in spec]


def tone_envelope(spans, total, sr=16000):
    """Loud sound during the given spans, near-silence elsewhere."""
    a = np.random.default_rng(0).normal(0, 0.0005, int(total * sr)).astype(np.float32)
    for s, e in spans:
        i, j = int(s * sr), int(e * sr)
        a[i:j] += 0.4 * np.sin(2 * np.pi * 220 * np.arange(j - i) / sr).astype(np.float32)
    return ae.Envelope.from_samples(a, sr)


# ---- finding cuts --------------------------------------------------------------------------
def test_restarts_and_fillers_found():
    ws = words([("I", 0, .2), ("I", .25, .4), ("think", .45, .7), ("we", .8, .9), ("went", .95, 1.1),
                ("we", 1.15, 1.25), ("went", 1.3, 1.5), ("home", 1.55, 1.8), ("very", 2, 2.2), ("very", 2.25, 2.4)])
    assert ae.restart_indices(ws) == {0, 3, 4}  # "very very" is kept on purpose


def test_cuts_are_in_silence_and_keep_a_breath():
    ws = words([("hello", 0.5, 1.0), ("there", 1.05, 1.5), ("um", 2.5, 2.8), ("friend", 3.8, 4.3)])
    env = tone_envelope([(0.5, 1.5), (2.5, 2.8), (3.8, 4.3)], 5.0)
    cuts = ae.find_cuts(ws, 0.0, 5.0, env, {"keep_pause": 0.12, "min_gap": 0.3}, fps=30)
    reasons = [c.reason for c in cuts]
    assert "start" in reasons and "end" in reasons and "filler" in reasons
    for c in cuts:
        # never inside a spoken word
        for w in ws:
            if w.text.strip() == "um":
                continue
            assert not (c.start < w.end - 0.01 and c.end > w.start + 0.01), (c, w)
        # whole frames
        assert abs(c.start * 30 - round(c.start * 30)) < 0.02
    filler = next(c for c in cuts if c.reason == "filler")
    assert filler.start <= 2.5 and filler.end >= 2.8  # the "um" itself is gone
    assert filler.start >= 1.5 + 0.03 and filler.end <= 3.8 - 0.04  # a little pause kept on each side


def test_pause_that_is_not_quiet_is_kept():
    ws = words([("one", 0.0, 0.5), ("two", 2.0, 2.5)])
    env = tone_envelope([(0.0, 2.5)], 3.0)  # laughing / music during the "pause"
    assert ae.find_cuts(ws, 0.0, 2.5, env, {}, fps=30) == []


def _project_with_captions(seconds=18):
    tr = fake_transcript("wide.mp4", seconds)
    p = normalize({"name": "t", "media": [{"id": "m1", "kind": "video", "path": "x.mp4", "duration": seconds,
                                           "width": 640, "height": 360, "fps": 30}],
                   "clips": [{"id": "c1", "media": "m1", "in": 0, "out": seconds}]})
    for seg in tr.segments:
        p["captions"].append({"id": f"k{int(seg.start)}", "start": seg.start, "end": seg.end, "text": seg.text,
                              "words": [{"start": w.start, "end": w.end, "text": w.text.strip()} for w in seg.words],
                              "person": None, "expression": "neutral"})
    return p, tr


def test_tighten_splits_clips_and_moves_captions():
    p, tr = _project_with_captions()
    env = tone_envelope([(i * 3.0, i * 3.0 + 2.0) for i in range(6)], 18.0)
    new, st = ae.tighten(p, {"m1": tr}, {"m1": env}, {"keep_pause": 0.12, "min_gap": 0.3})
    assert st["seconds"] > 4 and len(new["clips"]) == 6
    assert all(c.get("jump") for c in new["clips"][1:])
    # every caption still lines up with its own piece of video
    starts = [s for s in __import__("cliptool.editor.project", fromlist=["x"]).clip_starts(new["clips"])]
    for k, c, s in zip(sorted(new["captions"], key=lambda k: k["start"]), new["clips"], starts):
        assert abs(k["start"] - (s + (k["words"][0]["start"] - k["start"]) + 0)) < 0.5
        src = c["in"] + (k["words"][0]["start"] - s)
        assert abs(src - round(src / 3) * 3) < 0.05  # first word of each phrase is at a multiple of 3s in the file
    assert total_duration(new) == pytest.approx(st["after"], abs=0.01)


def test_cut_timeline_and_cold_open_roundtrip():
    p, _ = _project_with_captions()
    new, removed = ae.cut_timeline(p, 3.0, 6.0)
    assert removed == pytest.approx(3.0, abs=0.01) and total_duration(new) == pytest.approx(15.0, abs=0.01)
    assert not any("I love this" in k["text"] for k in new["captions"])
    assert any("Why does" in k["text"] for k in new["captions"])
    q = json.loads(json.dumps(new))
    rng = ae.cold_open(q, 6.0, 8.0)
    assert rng == (6.0, 8.0)
    assert q["clips"][0].get("cold_open") and q["clips"][1]["transition"]["type"] == "fadewhite"
    # captions keep their words in sync after being shifted
    for k in q["captions"]:
        assert k["words"][0]["start"] >= k["start"] - 0.01 and k["words"][-1]["end"] <= k["end"] + 0.01
    assert ae.remove_cold_open(q) > 1.5
    assert total_duration(q) == pytest.approx(total_duration(new), abs=0.01)
    assert [k["text"] for k in q["captions"]] == [k["text"] for k in new["captions"]]


def test_passes_add_items():
    p, _ = _project_with_captions()
    p["captions"][1]["text"] += " follow for more"
    p["captions"][2]["words"][0]["text"] = "fucking"
    assert ae.bleeps(p) == 1
    assert any(e["type"] == "bleep" for e in p["effects"])
    assert p["captions"][2]["words"][0]["text"] == "f******"
    assert ae.callouts(p)[0]["text"] == "FOLLOW FOR MORE"
    o = ae.hook_title(p, "this is a very long hook title that would never fit on a phone")
    assert o["size"] < 80
    assert ae.emphasis(p) >= 1


# ---- rendering many cuts ---------------------------------------------------------------------
@needs_ffmpeg
def test_render_many_cuts_from_one_take(media_dir, tmp_path):
    store = ProjectStore(tmp_path / "projects")
    pid, p = store.create("cuts")
    p["media"].append({"id": "m1", "kind": "video", "name": "wide.mp4", "path": str(media_dir / "wide.mp4"),
                       "duration": 18, "width": 640, "height": 360, "has_audio": True, "fps": 30})
    p["clips"] = [{"id": f"c{i}", "media": "m1", "in": i * 0.6, "out": i * 0.6 + 0.4} for i in range(25)]
    p["effects"] = [{"id": "e1", "type": "zoom_in", "start": 1, "end": 3, "params": {"ramp": 0.01}},
                    {"id": "e2", "type": "bleep", "start": 4, "end": 4.5, "params": {}}]
    p["audio"] = []
    store.save(pid, p)
    p = store.load(pid)
    plan = render.build_plan(p, store, pid, tmp_path / "w")
    graph = plan.script.read_text()
    assert graph.count("select=") == 1  # one decoder for all 25 pieces
    out = render.render_video(p, store, pid, tmp_path / "o.mp4", tmp_path / "w2", "draft")
    assert probe(out).duration == pytest.approx(10.0, abs=0.15)


# ---- sound effects / settings / QA -------------------------------------------------------------------
@needs_ffmpeg
def test_sound_library(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPTOOL_HOME", str(tmp_path))
    from cliptool.editor import sfx
    f = sfx.library_file("pop")
    assert f.exists() and probe(f).duration == pytest.approx(0.14, abs=0.03)
    out = subprocess.run(["ffmpeg", "-i", str(f), "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    assert "max_volume: -3.0 dB" in out


def test_settings_keys_are_hidden(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    f = tmp_path / "settings.json"
    view = settings.update({"keys": {"elevenlabs": "sk_secret_1234567890"}, "prefs": {"punch_ins": False,
                                                                                    "music_volume": "0.2"}}, f)
    assert "sk_secret_1234567890" not in f.read_text()
    assert view["keys"]["elevenlabs"]["set"] and view["keys"]["elevenlabs"]["hint"].endswith("7890")
    assert "1234567890" not in json.dumps(view)
    assert settings.get_key("elevenlabs", f) == "sk_secret_1234567890"
    assert settings.prefs(f)["punch_ins"] is False and settings.prefs(f)["music_volume"] == 0.2
    settings.update({"keys": {"elevenlabs": ""}}, f)
    assert settings.get_key("elevenlabs", f) is None


def test_qa_edit_checks(tmp_path):
    p, _ = _project_with_captions()
    p["captions"][1]["start"] = p["captions"][0]["start"] + 0.5  # overlap
    p["overlays"].append({"id": "o1", "type": "text", "style": "bold", "text": "LOW TEXT", "x": 0.5, "y": 0.93,
                          "size": 70, "start": 0, "end": 2})
    p["overlays"].append({"id": "o2", "type": "text", "style": "bold", "text": "W" * 60, "x": 0.5, "y": 0.4,
                          "size": 90, "start": 0, "end": 2})
    kinds = {i["kind"] for i in qa.check_layer2(p, ProjectStore(tmp_path), "x")}
    assert {"caption", "safe", "overflow"} <= kinds


@needs_ffmpeg
def test_qa_measures_rendered_video(tmp_path):
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=320x240:d=3", "-f", "lavfi",
                    "-i", "anullsrc=r=48000:cl=stereo", "-t", "3", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    str(v)], check=True)
    issues, _ = qa.check_layer1(v, 3.0)
    kinds = {i["kind"] for i in issues}
    assert "black" in kinds and "silence" in kinds and "freeze" in kinds


# ---- AI Editor ------------------------------------------------------------------------------
class FakeClaude:
    """Plays back scripted answers like the Messages API."""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self
        self.beta = SimpleNamespace(messages=self)

    def create(self, **kw):
        kw.pop("betas", None), kw.pop("fallbacks", None)
        self.calls.append(json.loads(json.dumps(kw, default=lambda o: getattr(o, "__dict__", str(o)))))
        blocks = self.script.pop(0)
        content = [SimpleNamespace(type="tool_use", id=f"t{i}", name=b[0], input=b[1]) if isinstance(b, tuple)
                   else SimpleNamespace(type="text", text=b) for i, b in enumerate(blocks)]
        stop = "tool_use" if any(isinstance(b, tuple) for b in blocks) else "end_turn"
        return SimpleNamespace(content=content, stop_reason=stop)


def test_ai_editor_turn_applies_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPTOOL_HOME", str(tmp_path))
    store = ProjectStore(tmp_path / "projects")
    pid, _ = store.create("ai")
    p, _ = _project_with_captions()
    store.save(pid, {**store.load(pid), **{k: p[k] for k in ("media", "clips", "captions")}})
    app = SimpleNamespace(store=store, lock=lambda pid: __import__("threading").Lock())
    job = SimpleNamespace(cancel=__import__("threading").Event(), message="", progress=0)
    fake = FakeClaude([
        [("cut_range", {"start": 3, "end": 6}), ("hook_title", {"text": "Nobody tells you this"})],
        [("add_effect", {"type": "zoom_in", "start": 1, "end": 2}), ("remove_items", {"ids": ["nope"]})],
        ["I cut the part about bad luck, added a hook title and a zoom."],
    ])
    res = agent.run_turn(app, pid, "cut the bad luck part and make it punchy", job, 0.0, lambda: fake)
    assert res["changed"] and "hook title" in res["reply"]
    assert len(res["actions"]) == 4
    saved = store.load(pid)
    assert total_duration(saved) == pytest.approx(15.0, abs=0.01)
    assert any(o.get("auto") == "hook" for o in saved["overlays"])
    assert any(e["type"] == "zoom_in" for e in saved["effects"])
    # the model saw the project and every tool result went back with the right id
    first = fake.calls[0]["messages"][-1]["content"]
    assert "Captions (6)" in first and "Why does this always happen" in first
    second = fake.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in second] == ["t0", "t1"]
    assert agent.load_history(app, pid)[-1]["text"].startswith("I cut")
    # tool definitions are valid JSON schemas with unique names
    names = [t["name"] for t in fake.calls[0]["tools"]]
    assert len(names) == len(set(names))
