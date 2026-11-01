"""AI Editor: Claude edits the project through tools, like an editor sitting next to you.

Each message from the user runs one turn: Claude looks at the project, calls editing
tools (each one changes the project), and answers with what it did. The whole turn is
saved at once, so the editor can undo it in one step.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, Optional

from cliptool.editor import autoedit as ae
from cliptool.editor.project import (CANVAS_PRESETS, EFFECTS, TRANSITIONS, clip_length, clip_starts, new_id,
                                     normalize, total_duration)
from cliptool.editor.textfx import TEXT_STYLES, TITLE_TEMPLATES
from cliptool.logger import get_logger
from cliptool.reformat import FRAME_STYLES

log = get_logger("agent")

MAX_STEPS = 24
HISTORY_TURNS = 12

SYSTEM = """You are the AI editor inside ClipTool Editor, a video editor for TikTok, YouTube Shorts, Reels and
YouTube. The user talks to you while looking at their timeline. You edit their project directly with the tools.

How to work
- Read the project summary first. Times are seconds on the edited timeline. Captions show what is said.
- Act, don't ask, when the request is clear. Make sensible choices like a professional short-form editor:
  tight pacing, a hook in the first 2 seconds, readable captions in the safe area (not below 80% of the
  height on vertical video), zooms/sound effects used with restraint.
- Prefer the high-level tools (tighten, punch_ins, emphasis_zooms, hook_title, callouts, bleep_swears,
  cold_open) over many small edits. Use cut_range to remove parts the user describes, finding the times
  from the captions.
- After cutting, times of everything later move. Call get_project again before further time-based edits.
- Don't invent content the user didn't say. Don't remove the user's own titles or text unless asked.
- Finish with a short, friendly summary in plain words (2-5 lines) of what you changed. No markdown headings.
  If something couldn't be done, say why and what the user can do."""


# ---------------------------------------------------------------------------
# Project summary for the model
# ---------------------------------------------------------------------------

def summary(p: dict, limit_caps: int = 400) -> str:
    total = total_duration(p)
    starts = clip_starts(p["clips"])
    media = {m["id"]: m for m in p["media"]}
    out = [f"Project “{p['name']}”, format {p['preset']} ({CANVAS_PRESETS[p['preset']]['label']}), "
           f"length {total:.2f}s. Frame style: {p['frame'].get('style')}."]
    out.append("Clips (timeline start-end ← source file in-out):")
    for i, (c, s) in enumerate(zip(p["clips"], starts)):
        tr = (c.get("transition") or {}).get("type")
        out.append(f"  #{i} {s:.2f}-{s + clip_length(c):.2f} ← {media.get(c['media'], {}).get('name', '?')} "
                   f"{float(c['in']):.2f}-{float(c['out']):.2f}" + (f" transition-in {tr}" if tr else "")
                   + (f" volume {c.get('volume')}" if c.get("volume", 1) != 1 else ""))
    people = {x["id"]: x["name"] for x in p["people"]}
    caps = sorted(p["captions"], key=lambda k: k["start"])
    out.append(f"Captions ({len(caps)}):" if caps else "Captions: none yet (use add_captions).")
    for k in caps[:limit_caps]:
        who = people.get(k.get("person"))
        out.append(f"  [{k['start']:.2f}-{k['end']:.2f}] id={k['id']}{' ' + who + ':' if who else ''} {k['text']}")
    if len(caps) > limit_caps:
        out.append(f"  … {len(caps) - limit_caps} more")
    cs = p["caption_style"]
    out.append(f"Caption style: y={cs.get('y')} size={cs.get('size')} max_words={cs.get('max_words')} "
               f"uppercase={cs.get('uppercase')} color={cs.get('color')} highlight={cs.get('highlight')}")
    if p["effects"]:
        out.append("Effects:")
        for e in sorted(p["effects"], key=lambda e: e["start"]):
            out.append(f"  id={e['id']} {e['type']} {e['start']:.2f}-{e['end']:.2f}"
                       + (f" (auto {e['auto']})" if e.get("auto") else ""))
    if p["overlays"]:
        out.append("Text, titles, pictures, video on top:")
        for o in sorted(p["overlays"], key=lambda o: o["start"]):
            label = o.get("text") or o.get("src") or o.get("media") or ""
            out.append(f"  id={o['id']} {o['type']} {o['start']:.2f}-{o['end']:.2f} “{label}” at x={o.get('x')} "
                       f"y={o.get('y')}")
    if p["audio"]:
        out.append("Audio track:")
        for a in p["audio"]:
            out.append(f"  id={a['id']} {a.get('name')} {a['start']:.2f}-{a['end']:.2f} volume {a.get('volume')}"
                       + (" (ducks under voice)" if a.get("duck") else ""))
    sounds = [m for m in p["media"] if m.get("kind") == "audio" and not m.get("sfx_source")]
    if sounds:
        out.append("Music/sound files in the project: " + ", ".join(f"{m['id']}={m['name']}" for m in sounds))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _num(desc: str, **kw) -> dict:
    return {"type": "number", "description": desc, **kw}


def _str(desc: str, **kw) -> dict:
    return {"type": "string", "description": desc, **kw}


def _obj(props: dict, required: Optional[list] = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS: list[dict] = [
    {"name": "get_project", "description": "The current project: clips, captions with times, effects, text, audio.",
     "input_schema": _obj({})},
    {"name": "tighten", "description": "Cut pauses/dead air, and optionally um/uh and repeated words or false "
                                       "starts. Cuts are placed in quiet moments. Changes all later times.",
     "input_schema": _obj({"remove_fillers": {"type": "boolean"}, "remove_restarts": {"type": "boolean"},
                           "keep_pause": _num("seconds of pause kept at each cut, 0.05-0.4 (default 0.12)")})},
    {"name": "cut_range", "description": "Remove part of the video (timeline seconds). Later items move earlier.",
     "input_schema": _obj({"start": _num("start second"), "end": _num("end second")}, ["start", "end"])},
    {"name": "add_captions", "description": "Create captions from speech (replaces existing captions).",
     "input_schema": _obj({"speakers": {"type": "boolean", "description": "detect several people talking"}})},
    {"name": "edit_caption", "description": "Change a caption's text (fix a misheard word) or delete it.",
     "input_schema": _obj({"id": _str("caption id"), "text": _str("new text"), "delete": {"type": "boolean"}},
                          ["id"])},
    {"name": "set_caption_style", "description": "Look and position of all captions.",
     "input_schema": _obj({"y": _num("vertical centre 0-1 (0.7 typical for vertical video)"),
                           "size": _num("font size for a 1080px wide frame (60-110)"),
                           "max_words": {"type": "integer", "description": "words on screen at once (1-6)"},
                           "uppercase": {"type": "boolean"}, "color": _str("#RRGGBB"),
                           "highlight": _str("#RRGGBB colour of the word being spoken"),
                           "show_avatar": {"type": "boolean"}})},
    {"name": "add_effect", "description": "Add an effect between two times. zoom_in params: amount (1.05-2), x, y "
                                          "(0-1 focus point), ramp (seconds). bleep/mute affect the sound.",
     "input_schema": _obj({"type": _str("effect", enum=list(EFFECTS)), "start": _num("second"), "end": _num("second"),
                           "params": {"type": "object", "description": "optional effect settings"}},
                          ["type", "start", "end"])},
    {"name": "add_text", "description": "Text on screen.",
     "input_schema": _obj({"text": _str("the text (short)"), "start": _num("second"), "end": _num("second"),
                           "style": _str("text style", enum=list(TEXT_STYLES)),
                           "x": _num("centre 0-1"), "y": _num("centre 0-1"), "size": _num("size (40-120)"),
                           "anim_in": _str("pop, fade, slide_up, slide_down, slide_left, slide_right, zoom, blur, "
                                           "typewriter or none")}, ["text", "start", "end"])},
    {"name": "add_title", "description": "Animated title with optional second line.",
     "input_schema": _obj({"text": _str("title"), "subtitle": _str("second line"), "start": _num("second"),
                           "end": _num("second"), "template": _str("look", enum=list(TITLE_TEMPLATES)),
                           "y": _num("centre 0-1")}, ["text", "start", "end"])},
    {"name": "update_item", "description": "Move, retime or change an existing effect, text, title or audio item "
                                           "by id (only the given fields change).",
     "input_schema": _obj({"id": _str("item id"), "start": _num("second"), "end": _num("second"),
                           "text": _str("new text"), "x": _num("0-1"), "y": _num("0-1"), "size": _num("size"),
                           "volume": _num("0-2 for audio")}, ["id"])},
    {"name": "remove_items", "description": "Delete effects, text/titles/pictures or audio items by id.",
     "input_schema": _obj({"ids": {"type": "array", "items": {"type": "string"}}}, ["ids"])},
    {"name": "punch_ins", "description": "Alternate slight zooms across jump cuts so cuts look intentional.",
     "input_schema": _obj({"amount": _num("zoom, 1.05-1.3 (default 1.12)")})},
    {"name": "emphasis_zooms", "description": "Quick zoom punches on strong words, or at given times.",
     "input_schema": _obj({"times": {"type": "array", "items": {"type": "number"}}})},
    {"name": "hook_title", "description": "Title banner over the first seconds (replaces an earlier auto title).",
     "input_schema": _obj({"text": _str("2-6 punchy words"), "subtitle": _str("optional second line")}, ["text"])},
    {"name": "callouts", "description": "Automatic call-out text for calls to action (follow, subscribe, link...).",
     "input_schema": _obj({})},
    {"name": "bleep_swears", "description": "Bleep swear words and mask them in the captions.",
     "input_schema": _obj({})},
    {"name": "cold_open", "description": "Repeat a strong 2-5 s moment at the very start as a teaser (flash into "
                                         "the video). Without times the best moment is picked.",
     "input_schema": _obj({"start": _num("second"), "end": _num("second")})},
    {"name": "add_sound_effect", "description": "Put a sound effect on the audio track.",
     "input_schema": _obj({"name": _str("sound", enum=[]), "time": _num("second"),
                           "volume": _num("0.1-1 (default 0.6)")}, ["name", "time"])},
    {"name": "add_music", "description": "Background music under the whole video from a sound file in the project; "
                                         "it dips automatically while people talk.",
     "input_schema": _obj({"media_id": _str("id of the music file"), "volume": _num("0.05-0.5 (default 0.15)")},
                          ["media_id"])},
    {"name": "set_frame", "description": "How wide video fills a vertical frame, and the video format.",
     "input_schema": _obj({"style": _str("frame style", enum=list(FRAME_STYLES)),
                           "scale": _num("size of the middle video, 0.6-2"), "offset_y": _num("-0.4 to 0.4"),
                           "preset": _str("video format", enum=list(CANVAS_PRESETS))})},
    {"name": "set_transition", "description": "Transition into clip #index (0 removes nothing; use type 'none' to "
                                              "remove).",
     "input_schema": _obj({"index": {"type": "integer"}, "type": _str("transition", enum=list(TRANSITIONS) + ["none"]),
                           "duration": _num("seconds (0.2-1.5)")}, ["index", "type"])},
    {"name": "set_clip_volume", "description": "Volume of a clip's own sound.",
     "input_schema": _obj({"index": {"type": "integer"}, "volume": _num("0-2")}, ["index", "volume"])},
    {"name": "check_video", "description": "Render a quick test and check it for black/frozen picture, silences, "
                                           "loudness, clipping and caption problems. Slow (renders the video).",
     "input_schema": _obj({})},
]


class Session:
    """One AI turn: holds the project being edited and runs the tools."""

    def __init__(self, app, pid: str, job, time_hint: float = 0.0):
        from cliptool.editor import sfx
        self.app, self.pid, self.job = app, pid, job
        self.p = app.store.load(pid)
        self.changed = False
        self.actions: list[str] = []
        self.time_hint = time_hint
        self.sfx_names = list(sfx.LIBRARY)

    # helpers
    def _save(self):
        with self.app.lock(self.pid):
            self.app.store.save(self.pid, self.p)

    def _done(self, text: str) -> str:
        self.changed = True
        self.actions.append(text)
        return text + "\nNew length: %.2fs." % total_duration(self.p)

    def _find(self, item_id: str) -> tuple[Optional[str], Optional[dict]]:
        for key in ("captions", "effects", "overlays", "audio"):
            for it in self.p[key]:
                if it.get("id") == item_id:
                    return key, it
        return None, None

    def run_tool(self, name: str, a: dict) -> str:
        p = self.p
        if name == "get_project":
            return summary(p)
        if name == "tighten":
            trs, envs = self.app.media_analysis(p, self.pid, self.job)
            opts = {"fillers": a.get("remove_fillers", True), "restarts": a.get("remove_restarts", True),
                    "keep_pause": a.get("keep_pause", 0.12)}
            self.p, st = ae.tighten(p, trs, envs, opts)
            return self._done(f"Tightened: removed {st['seconds']:.1f}s ({st['pause'] + st['start'] + st['end']} pauses, "
                              f"{st['filler']} um/uh, {st['restart']} repeats).")
        if name == "cut_range":
            s, e = float(a["start"]), float(a["end"])
            if e <= s:
                return "Error: end must be after start."
            self.p, removed = ae.cut_timeline(p, s, e)
            return self._done(f"Cut {s:.2f}-{e:.2f} ({removed:.2f}s removed).")
        if name == "add_captions":
            self._save()
            res = self.app.transcribe(self.pid, {"speakers": bool(a.get("speakers")), "expressions": True}, self.job)
            self.p = self.app.store.load(self.pid)
            return self._done(f"Added {res['captions']} captions.") + "\n" + summary(self.p)
        if name == "edit_caption":
            key, k = self._find(a["id"])
            if key != "captions":
                return "Error: no caption with that id."
            if a.get("delete"):
                p["captions"].remove(k)
                return self._done(f"Deleted caption “{k['text']}”.")
            old = k["text"]
            k["text"] = str(a.get("text", old))
            if k.get("words"):
                new_words = k["text"].split()
                if len(new_words) == len(k["words"]):
                    for w, t in zip(k["words"], new_words):
                        w["text"] = t
                else:  # spread the new words over the caption's time
                    n, d = len(new_words), (k["end"] - k["start"]) / max(1, len(new_words))
                    k["words"] = [{"start": round(k["start"] + i * d, 3), "end": round(k["start"] + (i + 1) * d, 3),
                                   "text": t} for i, t in enumerate(new_words)] if n else None
            return self._done(f"Caption “{old}” → “{k['text']}”.")
        if name == "set_caption_style":
            cs = p["caption_style"]
            for f in ("y", "size", "max_words", "uppercase", "color", "highlight", "show_avatar"):
                if f in a:
                    cs[f] = a[f]
            return self._done("Caption style updated: " + ", ".join(f"{k}={v}" for k, v in a.items()) + ".")
        if name == "add_effect":
            if a["type"] not in EFFECTS:
                return "Error: unknown effect."
            e = {"id": new_id("e"), "type": a["type"], "start": round(float(a["start"]), 3),
                 "end": round(float(a["end"]), 3), "params": {**EFFECTS[a["type"]]["params"], **(a.get("params") or {})}}
            p["effects"].append(e)
            return self._done(f"Added {EFFECTS[a['type']]['label'].lower()} {e['start']:.2f}-{e['end']:.2f} (id {e['id']}).")
        if name == "add_text":
            o = {"id": new_id("o"), "type": "text", "style": a.get("style", "bold"), "text": a["text"],
                 "x": a.get("x", 0.5), "y": a.get("y", 0.3), "size": a.get("size", 70),
                 "anim_in": a.get("anim_in", "pop"), "anim_out": "fade", "anim_dur": 0.35,
                 "start": round(float(a["start"]), 3), "end": round(float(a["end"]), 3)}
            p["overlays"].append(o)
            return self._done(f"Added text “{o['text']}” at {o['start']:.2f}-{o['end']:.2f} (id {o['id']}).")
        if name == "add_title":
            pr = CANVAS_PRESETS[p["preset"]]
            text, sub, size = ae.fit_title(a["text"], a.get("subtitle", ""), pr["width"], pr["height"])
            o = {"id": new_id("o"), "type": "title", "template": a.get("template", "bar"), "text": text,
                 "subtitle": sub.upper(), "x": 0.5, "y": a.get("y", 0.25), "size": size,
                 "start": round(float(a["start"]), 3), "end": round(float(a["end"]), 3)}
            p["overlays"].append(o)
            return self._done(f"Added title “{text}” (id {o['id']}).")
        if name == "update_item":
            key, it = self._find(a["id"])
            if not it:
                return "Error: no item with that id."
            for f in ("start", "end", "text", "x", "y", "size", "volume"):
                if f in a:
                    it[f] = a[f]
            if key == "audio" and "start" in a:
                it["end"] = round(float(it["start"]) + float(it["out"]) - float(it["in"]), 3)
            return self._done(f"Updated {key[:-1]} {a['id']}.")
        if name == "remove_items":
            n = 0
            for item_id in a.get("ids", []):
                key, it = self._find(item_id)
                if it:
                    p[key].remove(it)
                    n += 1
            return self._done(f"Removed {n} item{'s' if n != 1 else ''}.")
        if name == "punch_ins":
            n = ae.punch_ins(p, float(a.get("amount", 1.12)))
            return self._done(f"Added {n} punch-in zooms at jump cuts.") if n else "No jump cuts found (tighten first)."
        if name == "emphasis_zooms":
            n = ae.emphasis(p, picks=a.get("times") or None)
            return self._done(f"Added {n} emphasis zooms.") if n else "No good spots found."
        if name == "hook_title":
            o = ae.hook_title(p, a.get("text"), a.get("subtitle", ""))
            return self._done(f"Hook title “{o['text']}” over the first {o['end']:.1f}s.") if o else "No title text."
        if name == "callouts":
            added = ae.callouts(p)
            return self._done("Call-outs: " + ", ".join(o["text"] for o in added) + ".") if added else \
                "No calls to action found in the captions."
        if name == "bleep_swears":
            n = ae.bleeps(p)
            return self._done(f"Bleeped {n} swear words.") if n else "No swear words found."
        if name == "cold_open":
            r = ae.cold_open(p, a.get("start"), a.get("end"))
            return self._done(f"Cold open: {r[0]:.2f}-{r[1]:.2f} now also plays first.") if r else \
                "No strong moment found (give start and end)."
        if name == "add_sound_effect":
            from cliptool.editor import sfx
            if a["name"] not in sfx.LIBRARY:
                return "Error: unknown sound. Available: " + ", ".join(sfx.LIBRARY)
            it = sfx.add_sound(p, self.app.store, self.pid, a["name"], float(a["time"]), float(a.get("volume", 0.6)))
            return self._done(f"Added {sfx.LIBRARY[a['name']]['label']} at {it['start']:.2f}s.")
        if name == "add_music":
            m = next((x for x in p["media"] if x["id"] == a["media_id"] and x.get("kind") == "audio"), None)
            if not m:
                return "Error: no sound file with that id in the project."
            length = min(float(m["duration"]), total_duration(p))
            p["audio"] = [x for x in p["audio"] if x.get("auto") != "music"]
            p["audio"].append({"id": new_id("a"), "media": m["id"], "name": m["name"], "in": 0.0, "out": length,
                               "start": 0.0, "end": length, "volume": float(a.get("volume", 0.15)), "fade_in": 0.5,
                               "fade_out": 1.5, "duck": True, "auto": "music"})
            return self._done(f"Music “{m['name']}” under the video, ducking under the voice.")
        if name == "set_frame":
            if a.get("preset") in CANVAS_PRESETS:
                p["preset"] = a["preset"]
            for f in ("style", "scale", "offset_y"):
                if f in a:
                    p["frame"][f] = a[f]
            return self._done("Frame updated: " + ", ".join(f"{k}={v}" for k, v in a.items()) + ".")
        if name == "set_transition":
            i = int(a["index"])
            if not 0 < i < len(p["clips"]):
                return "Error: transitions go into clips #1 and later."
            p["clips"][i]["transition"] = None if a["type"] == "none" else \
                {"type": a["type"], "duration": float(a.get("duration", 0.5))}
            return self._done(f"Transition into clip #{i}: {a['type']}.")
        if name == "set_clip_volume":
            i = int(a["index"])
            if not 0 <= i < len(p["clips"]):
                return "Error: no such clip."
            p["clips"][i]["volume"] = max(0.0, min(2.0, float(a["volume"])))
            return self._done(f"Clip #{i} volume {p['clips'][i]['volume']}.")
        if name == "check_video":
            self._save()
            res = self.app.qa(self.pid, self.job)
            if not res["issues"]:
                return "No problems found." + (" Not checked: " + "; ".join(res["skipped"]) if res["skipped"] else "")
            return "\n".join(f"{i['level']}: {i['message']}" + (f" at {i['time']}s" if i["time"] is not None else "")
                             for i in res["issues"])
        return f"Error: unknown tool {name}."


def _tools_for(session: Session) -> list[dict]:
    tools = copy.deepcopy(TOOLS)
    for t in tools:
        if t["name"] == "add_sound_effect":
            t["input_schema"]["properties"]["name"]["enum"] = session.sfx_names
    return tools


def _create(client, **kwargs):
    try:
        try:
            return client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                               **kwargs)
        except TypeError:  # older SDK without the fallbacks parameter
            return client.messages.create(**kwargs)
    except Exception as exc:
        # a model chosen in Settings that doesn't take the effort option: try once without it
        if "output_config" in kwargs and "effort" in str(exc).lower() and getattr(exc, "status_code", 0) == 400:
            kwargs.pop("output_config")
            return _create(client, **kwargs)
        raise


def history_file(app, pid: str) -> Path:
    return app.store.dir(pid) / "ai_chat.json"


def load_history(app, pid: str) -> list[dict]:
    try:
        return json.loads(history_file(app, pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def run_turn(app, pid: str, message: str, job, time_hint: float = 0.0,
             client_factory: Optional[Callable[[], Any]] = None) -> dict:
    """One user message → edits + reply. Returns {"reply", "actions", "changed"}."""
    from cliptool import settings
    pr = settings.prefs()
    if client_factory is None:
        key = settings.get_key("anthropic")
        if not key:
            raise ValueError("Add a Claude API key in Settings to use the AI Editor.")
        import anthropic

        def client_factory():
            return anthropic.Anthropic(api_key=key)
    client = client_factory()
    s = Session(app, pid, job, time_hint)
    history = load_history(app, pid)[-2 * HISTORY_TURNS:]
    messages: list[dict] = [{"role": h["role"], "content": h["text"]} for h in history]
    messages.append({"role": "user", "content": f"{message}\n\n(The playhead is at {time_hint:.2f}s.)\n\n"
                                                f"Current project:\n{summary(s.p)}"})
    tools = _tools_for(s)
    reply = ""
    for step in range(MAX_STEPS):
        if job.cancel.is_set():
            raise RuntimeError("Cancelled")
        job.message = "Claude is thinking..." if step == 0 else f"Claude is editing ({len(s.actions)} changes so far)..."
        job.progress = min(0.95, 0.05 + step / MAX_STEPS)
        resp = _create(client, model=pr["claude_model"], max_tokens=16000, system=SYSTEM, tools=tools,
                       messages=messages, output_config={"effort": pr["claude_effort"]})
        if resp.stop_reason == "refusal":
            reply = "Claude declined this request."
            break
        messages.append({"role": "assistant", "content": resp.content})
        texts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        calls = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if resp.stop_reason != "tool_use" or not calls:
            reply = "\n".join(texts).strip()
            break
        results = []
        for c in calls:
            try:
                out = s.run_tool(c.name, dict(c.input or {}))
                is_err = out.startswith("Error:")
            except Exception as exc:  # report back so Claude can adapt
                log.debug("tool %s failed", c.name, exc_info=True)
                out, is_err = f"Error: {exc}", True
            results.append({"type": "tool_result", "tool_use_id": c.id, "content": out, "is_error": is_err})
        messages.append({"role": "user", "content": results})
    else:
        reply = "I made the changes above but ran out of steps; ask me to continue if something is missing."
    if s.changed:
        s.p = normalize(s.p)
        s._save()
    reply = reply or ("Done." if s.changed else "I didn't change anything.")
    hist = load_history(app, pid) + [{"role": "user", "text": message}, {"role": "assistant", "text": reply}]
    history_file(app, pid).write_text(json.dumps(hist[-2 * HISTORY_TURNS:], ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    return {"reply": reply, "actions": s.actions, "changed": s.changed}
