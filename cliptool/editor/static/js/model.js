// Project model helpers. The timeline maths mirrors cliptool/editor/project.py
// and caption grouping mirrors cliptool/editor/render.py so preview == render.

export const TRANSITION_DEFAULT = 0.5;

export function uid(prefix) {
  return prefix + Math.random().toString(16).slice(2, 10);
}

export function clipLength(c) {
  return Math.max(0, (+c.out) - (+c.in));
}

export function transitionDuration(clips, i, transitions) {
  if (i <= 0 || i >= clips.length) return 0;
  const t = clips[i].transition || {};
  if (!t.type || (transitions && !(t.type in transitions))) return 0;
  const want = +(t.duration ?? TRANSITION_DEFAULT) || 0;
  const limit = 0.45 * Math.min(clipLength(clips[i - 1]), clipLength(clips[i]));
  return Math.round(Math.max(0, Math.min(want, limit)) * 1000) / 1000;
}

export function clipStarts(clips, transitions) {
  const out = [];
  let t = 0;
  clips.forEach((c, i) => {
    t -= transitionDuration(clips, i, transitions);
    out.push(Math.round(t * 1000) / 1000);
    t += clipLength(c);
  });
  return out;
}

export function totalDuration(p, transitions) {
  if (!p.clips.length) return 0;
  const s = clipStarts(p.clips, transitions);
  return s[s.length - 1] + clipLength(p.clips[p.clips.length - 1]);
}

// Which clips are visible at time t (two during a transition).
export function activeClips(p, t, transitions) {
  const starts = clipStarts(p.clips, transitions);
  const out = [];
  p.clips.forEach((c, i) => {
    const s = starts[i], e = s + clipLength(c);
    if (t >= s && t < e) {
      const d = transitionDuration(p.clips, i, transitions);
      const progress = d > 0 ? Math.min(1, (t - s) / d) : 1;
      out.push({ clip: c, index: i, start: s, end: e, local: (+c.in) + (t - s), progress,
                 transition: d > 0 && t - s < d ? c.transition.type : null });
    }
  });
  if (!out.length && p.clips.length) {
    const i = p.clips.length - 1, c = p.clips[i];
    out.push({ clip: c, index: i, start: starts[i], end: starts[i] + clipLength(c), local: +c.out - 0.04,
               progress: 1, transition: null });
  }
  return out;
}

// ---- captions -------------------------------------------------------------

export function captionWords(cap) {
  const text = (cap.text || '').trim();
  const words = cap.words || [];
  const joined = words.map(w => w.text.trim()).join(' ');
  if (words.length && joined === text.split(/\s+/).join(' ')) {
    return words.map(w => ({ start: +w.start, end: +w.end, text: w.text.trim() }));
  }
  const tokens = text.split(/\s+/).filter(Boolean);
  const step = Math.max(0.01, cap.end - cap.start) / Math.max(1, tokens.length);
  return tokens.map((t, i) => ({ start: cap.start + i * step, end: cap.start + (i + 1) * step, text: t }));
}

function groupWords(words, maxWords, maxGap = 0.6, maxChars = 22) {
  const groups = [];
  let cur = [];
  for (const w of words) {
    const chars = cur.reduce((n, x) => n + x.text.length + 1, 0) + w.text.length;
    if (cur.length && (cur.length >= maxWords || w.start - cur[cur.length - 1].end > maxGap || chars > maxChars)) {
      groups.push(cur);
      cur = [];
    }
    cur.push(w);
    if (/[.?!,;:]$/.test(w.text)) {
      groups.push(cur);
      cur = [];
    }
  }
  if (cur.length) groups.push(cur);
  return groups;
}

// The word group on screen at time t and which word is highlighted.
export function captionAt(p, t) {
  const maxWords = +p.caption_style.max_words || 3;
  for (const cap of p.captions) {
    if (t < cap.start || t >= cap.end) continue;
    const groups = groupWords(captionWords(cap), maxWords);
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i];
      const gs = i === 0 ? cap.start : g[0].start;
      const ge = i + 1 < groups.length ? groups[i + 1][0].start : cap.end;
      if (t >= gs && t < ge) {
        let active = 0;
        g.forEach((w, j) => { if (t >= (j === 0 ? gs : w.start)) active = j; });
        return { caption: cap, words: g, active };
      }
    }
  }
  return null;
}

// ---- editing helpers --------------------------------------------------------

// Everything that sits on the timeline besides clips.
export function timedItems(p) {
  return [...p.captions, ...p.effects, ...p.overlays, ...(p.audio || [])];
}

// Items cut from a file (audio, video overlays) keep end - start == out - in.
const fromFile = (it) => it.media !== undefined && it.in !== undefined;

// Shift items starting at/after `from` by `delta` seconds (ripple).
export function rippleShift(p, from, delta) {
  if (!delta) return;
  for (const it of timedItems(p)) {
    if (it.start >= from - 1e-6) {
      it.start = Math.max(0, it.start + delta);
      it.end = Math.max(it.start + 0.05, it.end + delta);
      if (it.words) it.words.forEach(w => { w.start += delta; w.end += delta; });
    } else if (it.end > from && delta < 0) {
      const newEnd = Math.max(it.start + 0.05, it.end + delta);
      if (fromFile(it)) it.out = +it.out - (it.end - newEnd);
      it.end = newEnd;
    }
  }
}

// Remove [a, b) from every timed item, pulling later ones back.
export function rippleDelete(p, a, b) {
  const len = b - a;
  p.captions = p.captions.filter(c => !(c.start >= a && c.end <= b));
  p.effects = p.effects.filter(c => !(c.start >= a && c.end <= b));
  p.overlays = p.overlays.filter(c => !(c.start >= a && c.end <= b));
  p.audio = (p.audio || []).filter(c => !(c.start >= a && c.end <= b));
  rippleShift(p, b, -len);
}

// After clips are reordered, move every timed item with the clip it sat on.
export function remapAfterReorder(p, oldClips, transitions) {
  const oldStarts = clipStarts(oldClips, transitions);
  const newStarts = clipStarts(p.clips, transitions);
  const newById = Object.fromEntries(p.clips.map((c, i) => [c.id, newStarts[i]]));
  for (const it of timedItems(p)) {
    const i = oldClips.findIndex((c, k) => it.start >= oldStarts[k] && it.start < oldStarts[k] + clipLength(c));
    if (i < 0) continue;
    const shift = newById[oldClips[i].id] - oldStarts[i];
    if (!shift) continue;
    it.start += shift; it.end += shift;
    if (it.words) it.words.forEach(w => { w.start += shift; w.end += shift; });
  }
}

export function fmtTime(t, ms = true) {
  t = Math.max(0, t || 0);
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${(ms ? s.toFixed(2) : Math.floor(s).toString()).padStart(ms ? 5 : 2, '0')}`;
}

// ---- undo / redo ---------------------------------------------------------------

export class History {
  constructor(limit = 100) { this.undoStack = []; this.redoStack = []; this.limit = limit; }
  push(snapshot) {
    if (this.undoStack[this.undoStack.length - 1] === snapshot) return;
    this.undoStack.push(snapshot);
    if (this.undoStack.length > this.limit) this.undoStack.shift();
    this.redoStack = [];
  }
  undo(current) {
    if (!this.undoStack.length) return null;
    this.redoStack.push(current);
    return this.undoStack.pop();
  }
  redo(current) {
    if (!this.redoStack.length) return null;
    this.undoStack.push(current);
    return this.redoStack.pop();
  }
}
