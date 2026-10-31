// Timeline: tracks for captions, text & stickers, effects and video clips.
// Drag bars to move them, drag their edges to change how long they last,
// drag clip edges to trim, drop effects / transitions / media onto it.
import { clipLength, clipStarts, fmtTime, rippleDelete, rippleShift, totalDuration, transitionDuration } from './model.js';

export const EMOJI = { neutral: '😐', happy: '😊', sad: '😢', mad: '😠', angry: '😡', rofl: '🤣', lol: '😂',
  disappointed: '😞', face_slap: '🤦', excited: '🤩', surprised: '😮', scared: '😱' };

const TRACKS = [
  { key: 'captions', label: 'Captions' },
  { key: 'overlays', label: 'Text & pics' },
  { key: 'effects', label: 'Effects' },
  { key: 'video', label: 'Video' },
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

  // ---- render -----------------------------------------------------------
  render() {
    const p = this.p;
    if (!p) return;
    const px = this.pxps;
    const dur = totalDuration(p, this.T);
    const end = Math.max(dur, ...p.captions.map(c => c.end), ...p.effects.map(c => c.end), ...p.overlays.map(c => c.end), 5);
    const width = end * px + 300;
    const sel = this.app.selection || {};
    const isSel = (kind, id) => sel.kind === kind && sel.id === id ? ' selected' : '';
    const L = { caption: lanes(p.captions), overlay: lanes(p.overlays), effect: lanes(p.effects) };
    const bar = (kind, it, label, color) => {
      const { lane, n } = L[kind];
      const geo = n === 1 ? '' : `top:${4 + lane[it.id] * LANE_H}px;height:${LANE_H - 3}px;`;
      return `<div class="bar ${kind}${isSel(kind, it.id)}" data-kind="${kind}" data-id="${it.id}"
         style="${geo}left:${it.start * px}px;width:${Math.max(4, (it.end - it.start) * px)}px;${color ? `--c:${color}` : ''}"
         title="${esc(label)} (${fmtTime(it.start)} - ${fmtTime(it.end)})">
         <span class="h-l"></span><span class="bar-label">${label}</span><span class="h-r"></span></div>`;
    };

    // ruler
    const step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120].find(s => s * px >= 70) || 300;
    let ticks = '';
    for (let t = 0; t <= end + step; t += step) {
      ticks += `<div class="tick" style="left:${t * px}px"><span>${fmtTime(t, false)}</span></div>`;
    }

    const people = Object.fromEntries(p.people.map(x => [x.id, x]));
    const caps = p.captions.map(c => {
      const person = people[c.person];
      const name = person ? person.name : 'No person';
      return bar('caption', c, `${EMOJI[c.expression] || ''} <b>${esc(name)}:</b> ${esc(c.text)}`, person ? person.color : '#888');
    }).join('');
    const ovs = p.overlays.map(o => bar('overlay', o, o.type === 'image' ? `🖼 ${esc(o.src.split('/').pop())}` : `T ${esc(o.text)}`)).join('');
    const fxs = p.effects.map(e => bar('effect', e, `✨ ${esc(this.app.meta.effects[e.type]?.label || e.type)}`)).join('');

    const starts = clipStarts(p.clips, this.T);
    const media = Object.fromEntries(p.media.map(m => [m.id, m]));
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

    this.inner.style.width = width + 'px';
    this.inner.innerHTML = `
      <div class="ruler" data-kind="ruler">${ticks}</div>
      <div class="track" data-track="captions" style="height:${trackHeight(L.caption.n)}px">${caps}</div>
      <div class="track" data-track="overlays" style="height:${trackHeight(L.overlay.n)}px">${ovs}</div>
      <div class="track" data-track="effects" style="height:${trackHeight(L.effect.n)}px">${fxs}</div>
      <div class="track" data-track="video">${clips}</div>
      <div class="playhead" style="left:${this.app.time * px}px"><span></span></div>`;
    this.playhead = this.inner.querySelector('.playhead');
    const heights = [L.caption.n, L.overlay.n, L.effect.n, 1].map(trackHeight);
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

  // ---- interaction --------------------------------------------------------
  bind() {
    this.scroll.addEventListener('wheel', (e) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        this.setZoom(this.pxps * (e.deltaY < 0 ? 1.15 : 1 / 1.15), this.x2t(e.clientX));
      }
    }, { passive: false });

    this.inner.addEventListener('pointerdown', (e) => this.onDown(e));

    // drops from the side panels
    this.inner.addEventListener('dragover', (e) => {
      if ([...e.dataTransfer.types].some(t => t === 'application/x-cliptool' || t === 'Files')) {
        e.preventDefault();
        this.inner.classList.add('drop-hover');
      }
    });
    this.inner.addEventListener('dragleave', () => this.inner.classList.remove('drop-hover'));
    this.inner.addEventListener('drop', (e) => {
      this.inner.classList.remove('drop-hover');
      e.preventDefault();
      const t = this.x2t(e.clientX);
      const clipEl = e.target.closest('.clip');
      if (e.dataTransfer.files.length) return this.app.dropFiles(e.dataTransfer.files, { time: t });
      const raw = e.dataTransfer.getData('application/x-cliptool');
      if (!raw) return;
      this.app.dropItem(JSON.parse(raw), { time: t, clipId: clipEl?.dataset.id });
    });
  }

  onDown(e) {
    if (e.button !== 0) return;
    const el = e.target.closest('[data-kind]');
    const kind = el?.dataset.kind;
    const app = this.app;
    if (!el || kind === 'ruler' || e.target.classList.contains('track') || e.target === this.inner) {
      // scrub
      app.seek(this.x2t(e.clientX));
      const move = (ev) => app.seek(this.x2t(ev.clientX));
      const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
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
    if (kind === 'clip') return this.dragClip(e, id, edge);
    const item = app.findItem(kind, id);
    if (!item) return;
    const x0 = e.clientX, s0 = item.start, e0 = item.end;
    const snaps = this.snapPoints(item);
    app.beginChange();
    let moved = false;
    const move = (ev) => {
      const dt = (ev.clientX - x0) / this.pxps;
      if (Math.abs(ev.clientX - x0) > 2) moved = true;
      if (!moved) return;
      if (edge === 'l') item.start = Math.min(e0 - 0.1, Math.max(0, this.snap(s0 + dt, snaps)));
      else if (edge === 'r') item.end = Math.max(s0 + 0.1, this.snap(e0 + dt, snaps));
      else {
        let ns = Math.max(0, s0 + dt);
        const sn = this.snap(ns, snaps), en = this.snap(ns + (e0 - s0), snaps);
        if (sn !== ns) ns = sn; else if (en !== ns + (e0 - s0)) ns = en - (e0 - s0);
        const shift = ns - item.start;
        item.start = ns;
        item.end = ns + (e0 - s0);
        if (item.words && shift) item.words.forEach(w => { w.start += shift; w.end += shift; });
      }
      if (item.words && edge) item.words = null;  // re-time words evenly after a resize
      el.style.left = item.start * this.pxps + 'px';
      el.style.width = Math.max(4, (item.end - item.start) * this.pxps) + 'px';
      app.preview.draw();
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      app.endChange(moved);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  snapPoints(except) {
    const pts = [this.app.time, 0];
    for (const it of [...this.p.captions, ...this.p.effects, ...this.p.overlays]) {
      if (it === except) continue;
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

  dragClip(e, id, edge) {
    const app = this.app, p = this.p;
    if (!edge) return;  // body click just selects
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
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
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
