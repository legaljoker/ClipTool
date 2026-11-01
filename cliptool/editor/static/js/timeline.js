// Timeline: tracks for captions, text & pictures, effects, video clips and audio.
// Everything updates live while you drag: bars move with the mouse, the preview jumps
// to where the item now starts, and a tooltip shows the exact time.
import { clipLength, clipStarts, fmtTime, remapAfterReorder, rippleDelete, rippleShift, totalDuration,
  transitionDuration } from './model.js';

const TRACKS = [
  { key: 'captions', label: 'Captions' },
  { key: 'overlays', label: 'Text & pics' },
  { key: 'effects', label: 'Effects' },
  { key: 'video', label: 'Video' },
  { key: 'audio', label: 'Audio' },
];

// Stack overlapping bars into lanes so nothing hides behind something else.
function lanes(items) {
  const ends = [], lane = {};
  for (const it of [...items].sort((a, b) => a.start - b.start)) {
    let l = ends.findIndex(e => e <= it.start + 1e-6);
    if (l < 0) { l = ends.length; ends.push(0); }
    ends[l] = it.end;
    lane[it.id] = l;
  }
  return { lane, n: Math.max(1, ends.length) };
}
const LANE_H = 26;
const trackHeight = (n) => n === 1 ? 44 : n * LANE_H + 8;
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fromFile = (it) => it.media !== undefined && it.in !== undefined;

export class Timeline {
  constructor(app, root) {
    this.app = app;
    this.root = root;
    this.scroll = root.querySelector('.tl-scroll');
    this.inner = root.querySelector('.tl-inner');
    this.labels = root.querySelector('.tl-labels');
    this.pxps = 60;  // pixels per second (timeline zoom)
    this.labels.innerHTML = '<div class="tl-label ruler-label"></div>' +
      TRACKS.map(t => `<div class="tl-label">${t.label}</div>`).join('');
    this.tip = document.createElement('div');
    this.tip.className = 'tl-tip hidden';
    document.body.appendChild(this.tip);
    this.bind();
  }

  get p() { return this.app.project; }
  get T() { return this.app.meta.transitions; }

  setZoom(pxps, anchorTime) {
    const old = this.pxps;
    this.pxps = Math.max(8, Math.min(600, pxps));
    if (anchorTime != null) {
      const offset = anchorTime * old - this.scroll.scrollLeft;
      this.render();
      this.scroll.scrollLeft = anchorTime * this.pxps - offset;
    } else this.render();
    this.app.onZoom(this.pxps);
  }

  fit() {
    const d = Math.max(1, totalDuration(this.p, this.T));
    this.setZoom((this.scroll.clientWidth - 60) / d);
  }

  x2t(clientX) {
    const r = this.inner.getBoundingClientRect();
    return Math.max(0, (clientX - r.left) / this.pxps);
  }

  trackAt(clientY) {
    for (const el of this.inner.querySelectorAll('.track')) {
      const r = el.getBoundingClientRect();
      if (clientY >= r.top && clientY < r.bottom) return el.dataset.track;
    }
    return null;
  }

  showTip(x, y, text) {
    this.tip.textContent = text;
    this.tip.style.left = x + 14 + 'px';
    this.tip.style.top = y - 30 + 'px';
    this.tip.classList.remove('hidden');
  }

  hideTip() { this.tip.classList.add('hidden'); }

  // ---- render -----------------------------------------------------------
  render() {
    const p = this.p;
    if (!p) return;
    const px = this.pxps;
    const dur = totalDuration(p, this.T);
    const end = Math.max(dur, ...p.captions.map(c => c.end), ...p.effects.map(c => c.end),
      ...p.overlays.map(c => c.end), ...(p.audio || []).map(c => c.end), 5);
    const width = end * px + 300;
    const sel = this.app.selection || {};
    const isSel = (kind, id) => sel.kind === kind && sel.id === id ? ' selected' : '';
    const L = { caption: lanes(p.captions), overlay: lanes(p.overlays), effect: lanes(p.effects), audio: lanes(p.audio || []) };
    const bar = (kind, it, label, color, bg) => {
      const { lane, n } = L[kind];
      const geo = n === 1 ? '' : `top:${4 + lane[it.id] * LANE_H}px;height:${LANE_H - 3}px;`;
      return `<div class="bar ${kind}${isSel(kind, it.id)}${it._ghost ? ' ghost-bar' : ''}" data-kind="${kind}" data-id="${it.id}"
         style="${geo}left:${it.start * px}px;width:${Math.max(4, (it.end - it.start) * px)}px;${color ? `--c:${color};` : ''}${bg || ''}"
         title="${esc(label.replace(/<[^>]+>/g, ''))} (${fmtTime(it.start)} - ${fmtTime(it.end)})">
         <span class="h-l"></span><span class="bar-label">${label}</span><span class="h-r"></span></div>`;
    };

    const step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120].find(s => s * px >= 70) || 300;
    let ticks = '';
    for (let t = 0; t <= end + step; t += step) {
      ticks += `<div class="tick" style="left:${t * px}px"><span>${fmtTime(t, false)}</span></div>`;
    }

    const people = Object.fromEntries(p.people.map(x => [x.id, x]));
    const media = Object.fromEntries(p.media.map(m => [m.id, m]));
    const caps = p.captions.map(c => {
      const person = people[c.person];
      return bar('caption', c, `${esc(this.app.exprEmoji(c.expression))} <b>${esc(person ? person.name : 'No person')}:</b> ${esc(c.text)}`,
        person ? person.color : '#888');
    }).join('');
    const ovs = p.overlays.map(o => {
      if (o.type === 'video') {
        const m = media[o.media] || {};
        return bar('overlay', o, `🎥 ${esc(m.name || 'video')}`, '#ff9f43',
          m.thumb ? `background-image:url('/files/${this.app.pid}/${m.thumb}');background-size:auto 100%;` : '');
      }
      if (o.type === 'image') return bar('overlay', o, `🖼 ${esc(o.src.split('/').pop())}`);
      if (o.type === 'title') return bar('overlay', o, `🎬 ${esc(o.text)}`);
      return bar('overlay', o, `T ${esc(o.text)}`);
    }).join('');
    const fxs = p.effects.map(e => bar('effect', e, `✨ ${esc(this.app.meta.effects[e.type]?.label || e.type)}`)).join('');
    const auds = (p.audio || []).map(a => {
      const m = media[a.media] || {};
      // waveform image covers the whole file; shift it so the used part shows
      const full = (m.duration || 1) * px;
      const bg = m.thumb ? `background-image:url('/files/${this.app.pid}/${m.thumb}');background-size:${full}px 100%;background-position:${-a.in * px}px 0;` : '';
      return bar('audio', a, `🎙 ${esc(a.name || m.name || 'audio')}`, '#2bd9c5', bg);
    }).join('');

    const starts = clipStarts(p.clips, this.T);
    const clips = p.clips.map((c, i) => {
      const m = media[c.media] || {};
      const d = transitionDuration(p.clips, i, this.T);
      const thumb = m.thumb ? `background-image:url('/files/${this.app.pid}/${m.thumb}')` : '';
      const tr = i > 0 ? `<div class="tr-badge ${d ? 'on' : ''}" data-kind="transition" data-id="${c.id}"
          style="width:${Math.max(18, d * px)}px" title="${d ? esc(this.T[c.transition.type]) + ` (${d}s)` : 'Drop a transition here'}">${d ? '⇄' : '+'}</div>` : '';
      return `<div class="clip${isSel('clip', c.id)}" data-kind="clip" data-id="${c.id}"
          style="left:${starts[i] * px}px;width:${Math.max(6, clipLength(c) * px)}px;${thumb}">
          <span class="h-l"></span>${tr}<span class="clip-label">${esc(m.name || '?')} · ${clipLength(c).toFixed(1)}s</span><span class="h-r"></span></div>`;
    }).join('');

    const heights = [L.caption.n, L.overlay.n, L.effect.n, 1, L.audio.n].map(trackHeight);
    this.inner.style.width = width + 'px';
    this.inner.innerHTML = `
      <div class="ruler" data-kind="ruler">${ticks}</div>
      <div class="track" data-track="captions" style="height:${heights[0]}px">${caps}</div>
      <div class="track" data-track="overlays" style="height:${heights[1]}px">${ovs}</div>
      <div class="track" data-track="effects" style="height:${heights[2]}px">${fxs}</div>
      <div class="track" data-track="video" style="height:${heights[3]}px">${clips}</div>
      <div class="track" data-track="audio" style="height:${heights[4]}px">${auds}</div>
      <div class="drop-ghost hidden"></div><div class="insert-marker hidden"></div>
      <div class="playhead" style="left:${this.app.time * px}px"><span></span></div>`;
    this.playhead = this.inner.querySelector('.playhead');
    this.ghost = this.inner.querySelector('.drop-ghost');
    this.marker = this.inner.querySelector('.insert-marker');
    [...this.labels.children].slice(1).forEach((el, i) => { el.style.height = heights[i] + 'px'; });
  }

  updatePlayhead(t) {
    if (!this.playhead) return;
    this.playhead.style.left = t * this.pxps + 'px';
    if (this.app.preview.playing) {  // follow the playhead
      const x = t * this.pxps, s = this.scroll;
      if (x > s.scrollLeft + s.clientWidth - 40) s.scrollLeft = x - 80;
    }
  }

  // ---- drops from the side panel: live ghost while hovering ------------------
  dropTarget(e) {
    const drag = this.app.dragging;  // set by the panel's dragstart
    const t = this.snap(this.x2t(e.clientX), this.snapPoints(null));
    const track = this.trackAt(e.clientY);
    const clipEl = e.target.closest('.clip');
    return { drag, t, track, clipId: clipEl?.dataset.id };
  }

  showDropGhost(e) {
    const { drag, t, track, clipId } = this.dropTarget(e);
    const px = this.pxps;
    this.ghost.classList.add('hidden');
    this.marker.classList.add('hidden');
    this.inner.querySelectorAll('.clip.drop-target').forEach(el => el.classList.remove('drop-target'));
    if (!drag) { this.showTip(e.clientX, e.clientY, `Drop at ${fmtTime(t)}`); return; }
    const place = (trackKey, start, len, label) => {
      const tr = this.inner.querySelector(`.track[data-track="${trackKey}"]`);
      this.ghost.style.cssText = `left:${start * px}px;top:${tr.offsetTop + 5}px;width:${Math.max(8, len * px)}px`;
      this.ghost.textContent = label;
      this.ghost.classList.remove('hidden');
    };
    if (drag.kind === 'transition') {
      const el = clipId && this.inner.querySelector(`.clip[data-id="${clipId}"]`);
      if (el) el.classList.add('drop-target');
      this.showTip(e.clientX, e.clientY, clipId ? 'Transition into this clip' : 'Drop on the second clip of a cut');
      return;
    }
    if (drag.kind === 'media') {
      const m = this.p.media.find(x => x.id === drag.id);
      if (!m) return;
      if (m.kind === 'audio' || track === 'audio') place('audio', t, m.duration, `🎙 ${m.name}`);
      else if (track === 'overlays') place('overlays', t, m.duration, `🎥 ${m.name} (on top)`);
      else {
        const idx = this.insertIndex(t);
        const starts = clipStarts(this.p.clips, this.T);
        const x = idx < this.p.clips.length ? starts[idx] : (starts.length ? starts[starts.length - 1] + clipLength(this.p.clips[this.p.clips.length - 1]) : 0);
        const tr = this.inner.querySelector('.track[data-track="video"]');
        this.marker.style.cssText = `left:${x * px - 2}px;top:${tr.offsetTop}px;height:${tr.offsetHeight}px`;
        this.marker.classList.remove('hidden');
        this.showTip(e.clientX, e.clientY, `Insert ${m.name} at ${fmtTime(x)}`);
        return;
      }
      this.showTip(e.clientX, e.clientY, fmtTime(t));
      return;
    }
    // effects / text / titles: a ghost bar here, and the real thing live in the preview
    const label = drag.kind === 'effect' ? `✨ ${this.app.meta.effects[drag.type]?.label || drag.type}`
      : drag.kind === 'title' ? '🎬 Animated title' : 'T Text';
    place(drag.kind === 'effect' ? 'effects' : 'overlays', t, drag.kind === 'effect' ? 2 : drag.kind === 'title' ? 4 : 3, label);
    this.app.previewDrop(drag, t);
    this.showTip(e.clientX, e.clientY, `${fmtTime(t)}`);
  }

  insertIndex(t) {
    const starts = clipStarts(this.p.clips, this.T);
    const i = starts.findIndex((s, k) => t < s + clipLength(this.p.clips[k]) / 2);
    return i < 0 ? this.p.clips.length : i;
  }

  clearDrop() {
    this.inner.classList.remove('drop-hover');
    this.ghost?.classList.add('hidden');
    this.marker?.classList.add('hidden');
    this.inner.querySelectorAll('.clip.drop-target').forEach(el => el.classList.remove('drop-target'));
    this.hideTip();
    this.app.previewDrop(null);
  }

  // ---- interaction --------------------------------------------------------
  bind() {
    this.scroll.addEventListener('wheel', (e) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        this.setZoom(this.pxps * (e.deltaY < 0 ? 1.15 : 1 / 1.15), this.x2t(e.clientX));
      }
    }, { passive: false });

    this.inner.addEventListener('pointerdown', (e) => this.onDown(e));

    this.inner.addEventListener('dragover', (e) => {
      if (![...e.dataTransfer.types].some(t => t === 'application/x-cliptool' || t === 'Files')) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      this.inner.classList.add('drop-hover');
      const now = performance.now();
      if (now - (this._lastGhost || 0) > 30) { this._lastGhost = now; this.showDropGhost(e); }
    });
    this.inner.addEventListener('dragleave', (e) => {
      if (!this.inner.contains(e.relatedTarget)) this.clearDrop();
    });
    this.inner.addEventListener('drop', (e) => {
      e.preventDefault();
      const { t, track, clipId } = this.dropTarget(e);
      this.clearDrop();
      if (e.dataTransfer.files.length) return this.app.dropFiles(e.dataTransfer.files, { time: t, track });
      const raw = e.dataTransfer.getData('application/x-cliptool');
      if (!raw) return;
      this.app.dropItem(JSON.parse(raw), { time: t, clipId, track, index: this.insertIndex(t) });
    });
  }

  onDown(e) {
    if (e.button !== 0) return;
    const el = e.target.closest('[data-kind]');
    const kind = el?.dataset.kind;
    const app = this.app;
    if (!el || kind === 'ruler' || e.target.classList.contains('track') || e.target === this.inner) {
      app.seek(this.x2t(e.clientX));
      const move = (ev) => { app.seek(this.x2t(ev.clientX)); this.showTip(ev.clientX, ev.clientY, fmtTime(app.time)); };
      const up = () => { this.hideTip(); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      if (!el || kind === 'ruler') app.select(null);
      return;
    }
    if (kind === 'transition') {
      app.select({ kind: 'clip', id: el.dataset.id }, 'transition');
      return;
    }
    const id = el.dataset.id;
    app.select({ kind, id });
    const edge = e.target.classList.contains('h-l') ? 'l' : e.target.classList.contains('h-r') ? 'r' : null;
    if (kind === 'clip') return edge ? this.trimClip(e, id, edge) : this.moveClip(e, id);
    const item = app.findItem(kind, id);
    if (!item) return;
    const elNow = () => this.inner.querySelector(`[data-kind="${kind}"][data-id="${id}"]`);
    const x0 = e.clientX, s0 = item.start, e0 = item.end, in0 = +item.in, out0 = +item.out;
    const m = fromFile(item) ? this.p.media.find(x => x.id === item.media) : null;
    const snaps = this.snapPoints(item);
    app.beginChange();
    let moved = false;
    const move = (ev) => {
      const dt = (ev.clientX - x0) / this.pxps;
      if (Math.abs(ev.clientX - x0) > 2) moved = true;
      if (!moved) return;
      if (edge === 'l') {
        let ns = Math.min(e0 - 0.1, Math.max(0, this.snap(s0 + dt, snaps)));
        if (m) { ns = Math.max(ns, s0 - in0); item.in = in0 + (ns - s0); }  // trims the start of the file
        item.start = ns;
      } else if (edge === 'r') {
        let ne = Math.max(s0 + 0.1, this.snap(e0 + dt, snaps));
        if (m) { ne = Math.min(ne, s0 + (m.duration - in0)); item.out = in0 + (ne - s0); }
        item.end = ne;
      } else {
        let ns = Math.max(0, s0 + dt);
        const sn = this.snap(ns, snaps), en = this.snap(ns + (e0 - s0), snaps);
        if (sn !== ns) ns = sn; else if (en !== ns + (e0 - s0)) ns = en - (e0 - s0);
        const shift = ns - item.start;
        item.start = ns;
        item.end = ns + (e0 - s0);
        if (item.words && shift) item.words.forEach(w => { w.start += shift; w.end += shift; });
      }
      if (item.words && edge) item.words = null;  // re-time words evenly after a resize
      const b = elNow();
      if (b) {
        b.style.left = item.start * this.pxps + 'px';
        b.style.width = Math.max(4, (item.end - item.start) * this.pxps) + 'px';
      }
      // live: show the frame where the item now starts (or ends, when dragging the end)
      app.seek(edge === 'r' ? Math.max(item.start, item.end - 0.05) : item.start + 0.02);
      this.showTip(ev.clientX, ev.clientY, `${fmtTime(item.start)} → ${fmtTime(item.end)}  (${(item.end - item.start).toFixed(2)}s)`);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      this.hideTip();
      app.endChange(moved);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  snapPoints(except) {
    const pts = [this.app.time, 0];
    for (const it of [...this.p.captions, ...this.p.effects, ...this.p.overlays, ...(this.p.audio || [])]) {
      if (it === except || it._ghost) continue;
      pts.push(it.start, it.end);
    }
    const starts = clipStarts(this.p.clips, this.T);
    this.p.clips.forEach((c, i) => pts.push(starts[i], starts[i] + clipLength(c)));
    return pts;
  }

  snap(t, pts) {
    if (!this.app.snapOn) return t;
    const tol = 8 / this.pxps;
    let best = t, bd = tol;
    for (const p of pts) { const d = Math.abs(p - t); if (d < bd) { bd = d; best = p; } }
    return best;
  }

  // Drag a clip's body to move it to another place in the video.
  moveClip(e, id) {
    const app = this.app, p = this.p;
    const el = this.inner.querySelector(`.clip[data-id="${id}"]`);
    const x0 = e.clientX;
    let moved = false;
    const move = (ev) => {
      if (!moved && Math.abs(ev.clientX - x0) < 6) return;
      moved = true;
      el.classList.add('dragging');
      el.style.transform = `translateX(${ev.clientX - x0}px)`;
      const t = this.x2t(ev.clientX);
      const idx = this.insertIndex(t);
      const starts = clipStarts(p.clips, this.T);
      const last = p.clips.length - 1;
      const x = idx <= last ? starts[idx] : starts[last] + clipLength(p.clips[last]);
      const tr = this.inner.querySelector('.track[data-track="video"]');
      this.marker.style.cssText = `left:${x * this.pxps - 2}px;top:${tr.offsetTop}px;height:${tr.offsetHeight}px`;
      this.marker.classList.remove('hidden');
      this.showTip(ev.clientX, ev.clientY, `Move here (${fmtTime(x)})`);
    };
    const up = (ev) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      this.hideTip();
      if (!moved) return;
      const from = p.clips.findIndex(c => c.id === id);
      let to = this.insertIndex(this.x2t(ev.clientX));
      if (to > from) to -= 1;
      if (to === from) { this.render(); return; }
      app.beginChange();
      const old = p.clips.map(c => ({ ...c }));
      const [c] = p.clips.splice(from, 1);
      p.clips.splice(to, 0, c);
      if (p.clips[0].transition) p.clips[0].transition = null;
      remapAfterReorder(p, old, this.T);
      app.endChange(true);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  trimClip(e, id, edge) {
    const app = this.app, p = this.p;
    const c = p.clips.find(x => x.id === id);
    const m = p.media.find(x => x.id === c.media);
    const i = p.clips.indexOf(c);
    const starts = clipStarts(p.clips, this.T);
    const start0 = starts[i], in0 = +c.in, out0 = +c.out;
    const x0 = e.clientX;
    app.beginChange();
    let moved = false;
    const move = (ev) => {
      const dt = (ev.clientX - x0) / this.pxps;
      if (Math.abs(ev.clientX - x0) > 2) moved = true;
      if (!moved) return;
      if (edge === 'l') c.in = Math.max(0, Math.min(out0 - 0.2, in0 + dt));
      else c.out = Math.min(m.duration, Math.max(in0 + 0.2, out0 + dt));
      this.render();
      app.preview.seek(edge === 'l' ? start0 : start0 + clipLength(c) - 0.04);
      this.showTip(ev.clientX, ev.clientY, `${edge === 'l' ? 'Starts' : 'Ends'} at ${fmtTime(edge === 'l' ? c.in : c.out)} in the file · ${clipLength(c).toFixed(2)}s`);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      this.hideTip();
      if (moved) {
        // keep captions/effects lined up with the video that moved
        if (edge === 'l') {
          const d = +c.in - in0;
          if (d > 0) rippleDelete(p, start0, start0 + d); else rippleShift(p, start0, -d);
        } else {
          const d = out0 - +c.out;
          const oldEnd = start0 + (out0 - in0);
          if (d > 0) rippleDelete(p, oldEnd - d, oldEnd); else rippleShift(p, oldEnd, -d);
        }
      }
      app.endChange(moved);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }
}
