// Live preview: draws the edit on a <canvas> (clips, frame style, transitions, effects)
// and shows captions / avatars / stickers / text as draggable elements on top.
import { activeClips, captionAt, totalDuration } from './model.js';

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export class Preview {
  constructor(app, stage) {
    this.app = app;
    this.stage = stage;
    this.box = stage.querySelector('.frame-box');
    this.canvas = stage.querySelector('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.comp = document.createElement('canvas');
    this.cctx = this.comp.getContext('2d');
    this.tiny = document.createElement('canvas');
    this.layer = stage.querySelector('.overlay-layer');
    this.videos = [0, 1].map(() => {
      const v = document.createElement('video');
      v.preload = 'auto';
      v.playsInline = true;
      v.crossOrigin = 'anonymous';
      v.addEventListener('seeked', () => this.draw());
      v.addEventListener('loadeddata', () => this.draw());
      return v;
    });
    this.time = 0;
    this.playing = false;
    this.unit = 1;
    new ResizeObserver(() => this.layout()).observe(stage);
    requestAnimationFrame(() => this.tick());
  }

  get p() { return this.app.project; }
  get meta() { return this.app.meta; }

  size() {
    const pr = this.meta.presets[this.p.preset];
    return { W: pr.width, H: pr.height };
  }

  layout() {
    if (!this.p) return;
    const { W, H } = this.size();
    const sw = this.stage.clientWidth - 24, sh = this.stage.clientHeight - 24;
    const scale = Math.min(sw / W, sh / H);
    const dw = Math.max(50, Math.floor(W * scale)), dh = Math.max(50, Math.floor(H * scale));
    this.box.style.width = dw + 'px';
    this.box.style.height = dh + 'px';
    const internal = Math.min(1, 960 / Math.max(W, H)) * 1;  // preview resolution
    const cw = Math.round(W * internal), ch = Math.round(H * internal);
    for (const c of [this.canvas, this.comp]) {
      if (c.width !== cw || c.height !== ch) { c.width = cw; c.height = ch; }
    }
    this.unit = (dw / W) * Math.min(W, H) / 1080;  // css px per "1080-frame px"
    this.box.style.setProperty('--u', this.unit);
    this.draw();
  }

  // ---- playback ----------------------------------------------------------
  duration() { return totalDuration(this.p, this.meta.transitions); }

  play() {
    if (!this.p.clips.length) return;
    if (this.time >= this.duration() - 0.05) this.time = 0;
    this.playing = true;
    this.wallStart = performance.now();
    this.timeStart = this.time;
    this.app.onPlayState(true);
  }

  pause() {
    this.playing = false;
    this.videos.forEach(v => v.pause());
    this.app.onPlayState(false);
  }

  toggle() { this.playing ? this.pause() : this.play(); }

  seek(t) {
    this.time = clamp(t, 0, Math.max(0, this.duration()));
    if (this.playing) { this.wallStart = performance.now(); this.timeStart = this.time; }
    this.sync(true);
    this.draw();
    this.app.onTime(this.time);
  }

  mediaUrl(m) {
    return `/api/projects/${this.app.pid}/media/${m.id}` + (m.proxy ? '?v=proxy' : '');
  }

  sync(force = false) {
    const p = this.p;
    if (!p) return;
    const act = activeClips(p, this.time, this.meta.transitions);
    this.active = act;
    const media = Object.fromEntries(p.media.map(m => [m.id, m]));
    this.videos.forEach((v, slot) => {
      const a = act.find(x => x.index % 2 === slot);
      if (!a) { if (!v.paused) v.pause(); v.dataset.clip = ''; return; }
      const m = media[a.clip.media];
      if (!m) return;
      const url = this.mediaUrl(m);
      if (v.dataset.url !== url) { v.dataset.url = url; v.src = url; }
      v.dataset.clip = a.clip.id;
      v.volume = clamp(+a.clip.volume || 0, 0, 1);
      const drift = Math.abs(v.currentTime - a.local);
      if (this.playing) {
        if (v.paused) { v.currentTime = a.local; v.play().catch(() => {}); }
        else if (drift > 0.3) v.currentTime = a.local;
      } else {
        if (!v.paused) v.pause();
        if (force ? drift > 0.01 : drift > 0.04) v.currentTime = a.local;
      }
    });
  }

  tick() {
    if (this.playing) {
      this.time = this.timeStart + (performance.now() - this.wallStart) / 1000;
      const d = this.duration();
      if (this.time >= d) { this.time = d; this.pause(); }
      this.sync();
      this.draw();
      this.app.onTime(this.time);
    }
    requestAnimationFrame(() => this.tick());
  }

  videoFor(entry) {
    return this.videos.find(v => v.dataset.clip === entry.clip.id && v.readyState >= 2);
  }

  // ---- drawing -----------------------------------------------------------
  draw() {
    if (!this.p) return;
    const { comp, cctx: c } = this;
    const cw = comp.width, ch = comp.height;
    c.save();
    c.fillStyle = '#000';
    c.fillRect(0, 0, cw, ch);
    const act = activeClips(this.p, this.time, this.meta.transitions);
    for (const a of act.sort((x, y) => x.index - y.index)) {
      const v = this.videoFor(a);
      const m = this.p.media.find(mm => mm.id === a.clip.media);
      if (!v || !m) continue;
      if (a.transition) this.drawTransition(c, v, m, a.transition, a.progress);
      else this.drawClip(c, v, m);
    }
    c.restore();
    this.drawEffects();
    this.renderLayer();
  }

  drawClip(c, v, m) {
    const fr = this.p.frame;
    const cw = c.canvas.width, ch = c.canvas.height;
    const vw = v.videoWidth || m.width, vh = v.videoHeight || m.height;
    if (!vw || !vh) return;
    const style = fr.style || 'blur';
    const cover = () => {
      const s = Math.max(cw / vw, ch / vh);
      c.drawImage(v, (cw - vw * s) / 2, (ch - vh * s) / 2, vw * s, vh * s);
    };
    if (style === 'crop' || style === 'smart' || Math.abs(vw / vh - cw / ch) < 0.02 && Math.abs((+fr.scale || 1) - 1) < 0.01) {
      cover();
      return;
    }
    const k = cw / 1080;
    c.save();
    switch (style) {
      case 'blur_dark': c.filter = `blur(${(+fr.blur || 20) * 1.6 * k}px) brightness(0.75)`; cover(); break;
      case 'blur_bw': c.filter = `grayscale(1) blur(${(+fr.blur || 20) * k}px) brightness(0.92)`; cover(); break;
      case 'pixelate': {
        const t = this.tiny, tw = Math.max(2, Math.round(cw / 36)), th = Math.max(2, Math.round(ch / 36));
        t.width = tw; t.height = th;
        const tc = t.getContext('2d');
        const s = Math.max(tw / vw, th / vh);
        tc.drawImage(v, (tw - vw * s) / 2, (th - vh * s) / 2, vw * s, vh * s);
        c.imageSmoothingEnabled = false;
        c.filter = 'brightness(0.92)';
        c.drawImage(t, 0, 0, cw, ch);
        break;
      }
      case 'mirror': {
        const h = vh * (cw / vw), y0 = (ch - h) / 2;
        c.filter = 'blur(1.5px) brightness(0.82)';
        for (const [y, flip] of [[y0 - h, true], [y0, false], [y0 + h, true]]) {
          c.save();
          if (flip) { c.translate(0, y + h); c.scale(1, -1); c.drawImage(v, 0, 0, cw, h); }
          else c.drawImage(v, 0, y, cw, h);
          c.restore();
        }
        break;
      }
      case 'color': c.fillStyle = fr.color || '#101018'; c.fillRect(0, 0, cw, ch); break;
      case 'gradient': {
        const g = c.createLinearGradient(0, 0, 0, ch);
        g.addColorStop(0, fr.color || '#6a11cb'); g.addColorStop(1, fr.color2 || '#2575fc');
        c.fillStyle = g; c.fillRect(0, 0, cw, ch); break;
      }
      case 'black': c.fillStyle = '#000'; c.fillRect(0, 0, cw, ch); break;
      default: c.filter = `blur(${(+fr.blur || 20) * k}px) brightness(0.94)`; cover();
    }
    c.restore();
    const sc = +fr.scale || 1;
    const s = Math.min(cw * sc / vw, ch * sc / vh);
    const w = vw * s, h = vh * s;
    c.drawImage(v, (cw - w) / 2, (ch - h) / 2 + (+fr.offset_y || 0) * ch, w, h);
  }

  drawTransition(c, v, m, type, p) {
    const cw = c.canvas.width, ch = c.canvas.height;
    const off = document.createElement('canvas');
    off.width = cw; off.height = ch;
    const o = off.getContext('2d');
    this.drawClip(o, v, m);
    const prev = document.createElement('canvas');
    prev.width = cw; prev.height = ch;
    prev.getContext('2d').drawImage(c.canvas, 0, 0);
    c.save();
    const clip = (fn) => { c.save(); c.beginPath(); fn(); c.clip(); c.drawImage(off, 0, 0); c.restore(); };
    const diag = Math.hypot(cw, ch) / 2;
    switch (type) {
      case 'fadeblack': case 'fadewhite': {
        const col = type === 'fadeblack' ? '#000' : '#fff';
        if (p < 0.5) { c.globalAlpha = p * 2; c.fillStyle = col; c.fillRect(0, 0, cw, ch); }
        else { c.fillStyle = col; c.fillRect(0, 0, cw, ch); c.globalAlpha = (p - 0.5) * 2; c.drawImage(off, 0, 0); }
        break;
      }
      case 'wipeleft': clip(() => c.rect(cw * (1 - p), 0, cw * p, ch)); break;
      case 'wiperight': clip(() => c.rect(0, 0, cw * p, ch)); break;
      case 'wipeup': clip(() => c.rect(0, ch * (1 - p), cw, ch * p)); break;
      case 'wipedown': clip(() => c.rect(0, 0, cw, ch * p)); break;
      case 'slideleft': case 'slideright': case 'slideup': case 'slidedown': {
        const dx = type === 'slideleft' ? -1 : type === 'slideright' ? 1 : 0;
        const dy = type === 'slideup' ? -1 : type === 'slidedown' ? 1 : 0;
        c.clearRect(0, 0, cw, ch);
        c.drawImage(prev, dx * cw * p, dy * ch * p);
        c.drawImage(off, dx * cw * (p - 1), dy * ch * (p - 1));
        break;
      }
      case 'circleopen': clip(() => c.arc(cw / 2, ch / 2, diag * p, 0, Math.PI * 2)); break;
      case 'circleclose': {
        c.drawImage(off, 0, 0);
        c.save(); c.beginPath(); c.arc(cw / 2, ch / 2, diag * (1 - p), 0, Math.PI * 2); c.clip();
        c.drawImage(prev, 0, 0); c.restore();
        break;
      }
      case 'radial': clip(() => { c.moveTo(cw / 2, ch / 2); c.arc(cw / 2, ch / 2, diag * 1.5, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * p); c.closePath(); }); break;
      case 'zoomin': {
        c.clearRect(0, 0, cw, ch);
        const z = 1 + p;
        c.globalAlpha = 1 - p;
        c.drawImage(prev, cw / 2 - cw * z / 2, ch / 2 - ch * z / 2, cw * z, ch * z);
        c.globalAlpha = p; c.drawImage(off, 0, 0);
        break;
      }
      case 'pixelize': {
        const k = Math.max(1, Math.round(40 * (1 - Math.abs(p - 0.5) * 2)));
        const t = this.tiny; t.width = Math.max(2, cw / k | 0); t.height = Math.max(2, ch / k | 0);
        const tc = t.getContext('2d');
        tc.drawImage(p < 0.5 ? prev : off, 0, 0, t.width, t.height);
        c.imageSmoothingEnabled = false; c.drawImage(t, 0, 0, cw, ch);
        break;
      }
      default: c.globalAlpha = p; c.drawImage(off, 0, 0);  // fade / dissolve
    }
    c.restore();
  }

  effectState() {
    const t = this.time;
    let z = 1, cx = 0.5, cy = 0.5, sx = 0, sy = 0;
    const filters = [];
    let flash = 0, vignette = false, glitch = 0;
    for (const e of this.p.effects) {
      if (t < e.start || t > e.end) continue;
      const a = +e.start, b = +e.end, prm = e.params || {};
      switch (e.type) {
        case 'zoom_in': {
          const r = Math.max(0.01, Math.min(+prm.ramp || 0.25, (b - a) / 2));
          z += (Math.max(1, +prm.amount || 1.35) - 1) * clamp((t - a) / r, 0, 1) * clamp((b - t) / r, 0, 1);
          cx += (+prm.x || 0.5) - 0.5; cy += (+prm.y || 0.5) - 0.5; break;
        }
        case 'zoom_out':
          z += (Math.max(1, +prm.amount || 1.35) - 1) * clamp((b - t) / (b - a), 0, 1);
          cx += (+prm.x || 0.5) - 0.5; cy += (+prm.y || 0.5) - 0.5; break;
        case 'shake': {
          const k = clamp(+prm.intensity || 0.5, 0, 1);
          z += 0.03 + 0.08 * k; sx += 0.025 * k * Math.sin(t * 53); sy += 0.02 * k * Math.cos(t * 41); break;
        }
        case 'flash': flash += clamp(+prm.strength || 0.8, 0, 1) * clamp(1 - (t - a) / (b - a), 0, 1); break;
        case 'black_white': filters.push('grayscale(1)'); break;
        case 'sepia': filters.push('sepia(0.9)'); break;
        case 'blur': filters.push(`blur(${(+prm.amount || 8) * this.canvas.width / 1080}px)`); break;
        case 'invert': filters.push('invert(1)'); break;
        case 'vignette': vignette = true; break;
        case 'glitch': glitch = (+prm.amount || 12) * this.canvas.width / 1080; break;
      }
    }
    return { z: Math.max(1, z), cx, cy, sx, sy, filters, flash, vignette, glitch };
  }

  drawEffects() {
    const { ctx, comp, canvas } = this;
    const cw = canvas.width, ch = canvas.height;
    const fx = this.effectState();
    const sw = cw / fx.z, sh = ch / fx.z;
    const x = clamp(fx.cx * cw - sw / 2 + fx.sx * cw, 0, cw - sw);
    const y = clamp(fx.cy * ch - sh / 2 + fx.sy * ch, 0, ch - sh);
    ctx.save();
    ctx.clearRect(0, 0, cw, ch);
    ctx.filter = fx.filters.join(' ') || 'none';
    ctx.drawImage(comp, x, y, sw, sh, 0, 0, cw, ch);
    ctx.filter = 'none';
    if (fx.glitch) {
      ctx.globalCompositeOperation = 'screen';
      ctx.globalAlpha = 0.45;
      ctx.drawImage(comp, x, y, sw, sh, -fx.glitch, 0, cw, ch);
      ctx.drawImage(comp, x, y, sw, sh, fx.glitch, 0, cw, ch);
      ctx.globalCompositeOperation = 'source-over';
      ctx.globalAlpha = 1;
    }
    if (fx.vignette) {
      const g = ctx.createRadialGradient(cw / 2, ch / 2, Math.min(cw, ch) * 0.3, cw / 2, ch / 2, Math.hypot(cw, ch) / 2);
      g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, 'rgba(0,0,0,0.7)');
      ctx.fillStyle = g; ctx.fillRect(0, 0, cw, ch);
    }
    if (fx.flash > 0) { ctx.fillStyle = `rgba(255,255,255,${clamp(fx.flash, 0, 1)})`; ctx.fillRect(0, 0, cw, ch); }
    ctx.restore();
    if (!this.p.clips.length) {
      ctx.fillStyle = '#9aa0b4';
      ctx.font = `${Math.round(cw / 22)}px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.fillText('Drop videos here or use Media', cw / 2, ch / 2);
    }
  }

  // ---- DOM layer: captions, avatars, stickers, text ------------------------
  renderLayer() {
    const p = this.p, app = this.app, t = this.time;
    const st = p.caption_style;
    const parts = [];
    const sel = app.selection;
    const cap = captionAt(p, t);
    const esc = (s) => String(s).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
    // stickers, then text (same stacking order as the final render: pictures < captions < text)
    const ordered = [...p.overlays.filter(o => o.type === 'image'), ...p.overlays.filter(o => o.type !== 'image')];
    const texts = [];
    for (const o of ordered) {
      const visible = t >= o.start && t <= o.end;
      const selected = sel && sel.kind === 'overlay' && sel.id === o.id;
      if (!visible && !selected) continue;
      const cls = `ov ${selected ? 'selected' : ''} ${visible ? '' : 'ghost'}`;
      if (o.type === 'image') {
        parts.push(`<div class="${cls}" data-kind="overlay" data-id="${o.id}" style="left:${o.x * 100}%;top:${o.y * 100}%;width:${o.w * 100}%">
          <img src="/files/${app.pid}/${o.src}" draggable="false"><span class="handle" data-resize="1"></span></div>`);
      } else {
        texts.push(`<div class="${cls} text-ov" data-kind="overlay" data-id="${o.id}" style="left:${o.x * 100}%;top:${o.y * 100}%;
          font-size:calc(var(--u) * ${o.size || 70} * 1px);color:${o.color || '#fff'};font-family:${esc(o.font || 'Arial')};
          -webkit-text-stroke:calc(var(--u) * ${(o.outline_width ?? 5) * 2}px) ${o.outline || '#000'}">${esc(o.text || '').replace(/\n/g, '<br>')}<span class="handle" data-resize="1"></span></div>`);
      }
    }
    // caption (+ avatar)
    const capSel = sel && sel.kind === 'caption' && cap && sel.id === cap.caption.id;
    if (cap) {
      const person = p.people.find(x => x.id === cap.caption.person);
      const base = st.use_person_color && person ? person.color : st.color;
      const words = cap.words.map((w, j) => {
        const txt = esc(st.uppercase ? w.text.toUpperCase() : w.text);
        return j === cap.active ? `<span class="hl" style="color:${st.highlight}">${txt}</span>` : txt;
      }).join(' ');
      let avatar = '';
      if (st.show_avatar && person) {
        const src = this.app.avatarUrl(person, cap.caption.expression || 'neutral', this.playing && Math.floor(t / (+st.talk_flap || 0.15)) % 2 === 0);
        if (src) avatar = `<img class="cap-avatar place-${st.avatar_place}" src="${src}" draggable="false"
          style="width:calc(var(--u) * ${st.avatar_size * 1080 * this.size().W / Math.min(this.size().W, this.size().H)}px)">`;
      }
      parts.push(`<div class="caption ${capSel ? 'selected' : ''}" data-kind="caption-pos" data-id="${cap.caption.id}"
        style="left:${st.x * 100}%;top:${st.y * 100}%;font-family:${esc(st.font)};font-size:calc(var(--u) * ${st.size}px);
        color:${base};-webkit-text-stroke:calc(var(--u) * ${st.outline_width * 2}px) ${st.outline}">${avatar}<span class="words">${words}</span></div>`);
    } else if (!this.playing && (p.captions.length || app.tab === 'captions')) {
      parts.push(`<div class="caption placeholder" data-kind="caption-pos" style="left:${st.x * 100}%;top:${st.y * 100}%;
        font-size:calc(var(--u) * ${st.size}px)">CAPTIONS HERE</div>`);
    }
    parts.push(...texts);
    // zoom centre handle for a selected zoom effect
    if (sel && sel.kind === 'effect') {
      const e = p.effects.find(x => x.id === sel.id);
      if (e && (e.type === 'zoom_in' || e.type === 'zoom_out')) {
        parts.push(`<div class="zoom-target" data-kind="zoom-target" data-id="${e.id}" style="left:${e.params.x * 100}%;top:${e.params.y * 100}%" title="Drag to choose where to zoom"></div>`);
      }
    }
    const html = parts.join('');
    if (html !== this.lastHtml) {
      this.layer.innerHTML = html;
      this.lastHtml = html;
    }
  }
}
