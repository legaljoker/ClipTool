// Text styles, text animations and motion titles for the live preview.
// Mirrors cliptool/editor/textfx.py (tests check the title layouts match).

export const INTRO = 0.4;
export const OUTRO = 0.35;
export const TITLE_FONT = 'Montserrat Black';
export const SUB_FONT = 'Montserrat';

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// ---- measuring (same fonts as the renderer) -----------------------------------
const mctx = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null;
export function measure(text, font, size, bold) {
  if (!mctx) return text.length * size * 0.62;
  mctx.font = `${bold ? 700 : 400} ${size}px "${font}"`;
  return mctx.measureText(text).width;
}

export async function loadFonts(bundled) {
  if (typeof document === 'undefined') return;
  const css = [];
  for (const [family, files] of Object.entries(bundled)) {
    for (const [weight, file] of Object.entries(files)) {
      css.push(`@font-face{font-family:"${family}";src:url(/static/fonts/${file}) format("truetype");font-weight:${weight};font-display:block}`);
    }
  }
  const st = document.createElement('style');
  st.textContent = css.join('\n');
  document.head.appendChild(st);
  await Promise.all(Object.entries(bundled).flatMap(([family, files]) =>
    Object.keys(files).map(w => document.fonts.load(`${w} 40px "${family}"`).catch(() => null))));
}

export function textPx(o, styles, W, H) {
  const st = styles[o.style] || styles.bold;
  return (+o.size || 70) * Math.min(W, H) / 1080 * (st.size_scale || 1);
}

// CSS that matches the ASS style (k = css px per canvas px)
export function styleCss(o, styles, W, H, k) {
  const st = styles[o.style] || styles.bold;
  const u = Math.min(W, H) / 1080 * k;
  const color = o.color || st.color;
  const w = (o.outline_width ?? st.outline_width ?? 0) * u;
  const css = [`font-family:'${o.font || st.font}'`,  // single quotes: this goes inside style="..." `font-weight:${st.bold ? 700 : 400}`, `font-style:${st.italic ? 'italic' : 'normal'}`,
    `font-size:${textPx(o, styles, W, H) * k}px`, 'line-height:1.15', 'white-space:pre'];
  const shadows = [];
  if (st.hollow) css.push('color:transparent', `-webkit-text-stroke:${Math.max(1, w)}px ${color}`);
  else if (st.box) css.push(`color:${color}`, `background:${o.accent || st.box}`, `padding:${w * 0.9}px ${w}px`);
  else if (st.glow) {
    css.push(`color:${color}`);
    shadows.push(`0 0 ${w * 2}px ${o.accent || st.glow}`, `0 0 ${w * 4}px ${o.accent || st.glow}`);
  } else {
    css.push(`color:${color}`);
    if (w > 0) css.push(`-webkit-text-stroke:${w * 2}px ${o.outline || st.outline || '#000'}`, 'paint-order:stroke fill');
  }
  if (st.shadow && !st.box) shadows.push(`${st.shadow * u}px ${st.shadow * u}px 0 rgba(0,0,0,.5)`);
  if (shadows.length) css.push(`text-shadow:${shadows.join(',')}`);
  return css.join(';');
}

export function displayText(o, styles) {
  const st = styles[o.style] || styles.bold;
  const t = String(o.text || '');
  return st.uppercase ? t.toUpperCase() : t;
}

// ---- animations ---------------------------------------------------------------------
function slideOffset(kind, W, H, intro) {
  const dv = 0.18 * H, dh = 0.3 * W;
  const m = intro
    ? { slide_up: [0, dv], slide_down: [0, -dv], slide_left: [dh, 0], slide_right: [-dh, 0] }
    : { slide_up: [0, -dv], slide_down: [0, dv], slide_left: [-dh, 0], slide_right: [dh, 0] };
  return m[kind] || [0, 0];
}

// State of an animated text overlay at time t (offsets in canvas px).
export function textAnim(o, t, W, H) {
  const a = +o.start, b = +o.end;
  const dur = clamp(+(o.anim_dur ?? 0.4) || 0.4, 0.05, 3);
  const ain = o.anim_in || 'none', aout = o.anim_out || 'none';
  const dIn = ain !== 'none' ? Math.min(dur, (b - a) / 2) : 0;
  const dOut = aout !== 'none' ? Math.min(dur, (b - a) / 2) : 0;
  const s = { opacity: 1, dx: 0, dy: 0, scale: 1, blur: 0, chars: Infinity };
  const apply = (kind, u, intro) => {
    switch (kind) {
      case 'fade': s.opacity *= u; break;
      case 'pop': s.scale *= intro ? (u < 0.7 ? 1.1 * u / 0.7 : 1.1 - 0.1 * (u - 0.7) / 0.3)
                               : (u > 0.7 ? 1 + 0.1 * (1 - u) / 0.3 : 1.1 * u / 0.7); break;
      case 'zoom': s.scale *= 2.5 - 1.5 * u; s.opacity *= u; break;
      case 'blur': s.blur += (1 - u) * 20; s.opacity *= u; break;
      case 'typewriter': break;
      default: if (kind.startsWith('slide_')) {
        const [x, y] = slideOffset(kind, W, H, intro);
        s.dx += x * (1 - u); s.dy += y * (1 - u); s.opacity *= u;
      }
    }
  };
  if (dIn && t < a + dIn) {
    const u = clamp((t - a) / dIn, 0, 1);
    if (ain === 'typewriter') {
      const n = displayLen(o);
      s.chars = Math.min(n, Math.floor(u * n) + (u > 0 ? 1 : 0));
    } else apply(ain, u, true);
  }
  if (dOut && t > b - dOut) apply(aout, clamp((b - t) / dOut, 0, 1), false);
  return s;
}

function displayLen(o) { return String(o.text || '').replace(/\n/g, '').length; }

export function typewriterHtml(text, chars) {
  if (!isFinite(chars)) return esc(text);
  let count = 0, i = 0;
  while (i < text.length && count < chars) { if (text[i] !== '\n') count++; i++; }
  return esc(text.slice(0, i)) + `<span style="opacity:0">${esc(text.slice(i))}</span>`;
}

// ---- motion titles ---------------------------------------------------------------------
export function titleLayout(o, templates, W, H, measureFn = measure) {
  const id = o.template in templates ? o.template : 'bar';
  const tpl = templates[id];
  const s = Math.round((+o.size || 70) * Math.min(W, H) / 1080);
  const ss = Math.round(s * 0.38);
  const title = String(o.text || '').toUpperCase();
  const sub = String(o.subtitle || '').toUpperCase();
  const tw = measureFn(title, TITLE_FONT, s, false);
  const sw = sub ? measureFn(sub, SUB_FONT, ss, true) : 0;
  const accent = o.accent || tpl.accent, color = o.color || tpl.color, white = '#FFFFFF';
  const parts = [];
  let rot = 0, gw, gh;
  const T = (text, font, size, bold, x, y, col, anim, delay, align = 'center') =>
    parts.push({ kind: 'text', text, font, size, bold, x, y, color: col, anim, delay, align });
  if (id === 'bar' || id === 'slant') {
    const pad = 0.35 * s, bw = tw + 2 * pad, bh = 1.25 * s;
    gw = Math.max(bw, sw);
    gh = bh + (sub ? 0.25 * s + ss : 0);
    parts.push({ kind: 'rect', x: (gw - bw) / 2, y: 0, w: bw, h: bh, color: accent, anim: 'wipe', delay: 0 });
    T(title, TITLE_FONT, s, false, gw / 2, bh / 2, color, 'fade', 0.15);
    if (sub) T(sub, SUB_FONT, ss, true, gw / 2, bh + 0.25 * s + ss / 2, white, 'slide_up', 0.3);
    if (id === 'slant') rot = -5;
  } else if (id === 'box' || id === 'circle') {
    const block = sub ? ss + 0.12 * s + s : 1.1 * s;
    const tx = block + 0.3 * s;
    gw = tx + Math.max(tw, sw); gh = block;
    if (id === 'box') parts.push({ kind: 'rect', x: 0, y: 0, w: block, h: block, color: accent, anim: 'pop', delay: 0 });
    else parts.push({ kind: 'circle', x: block / 2, y: block / 2, r: block / 2, color: accent, anim: 'pop', delay: 0 });
    if (sub) {
      T(sub, SUB_FONT, ss, true, tx, ss / 2, white, 'slide_left', 0.15, 'left');
      T(title, TITLE_FONT, s, false, tx, ss + 0.12 * s + s / 2, color, 'slide_left', 0.25, 'left');
    } else T(title, TITLE_FONT, s, false, tx, block / 2, color, 'slide_left', 0.15, 'left');
  } else if (id === 'underline') {
    gw = Math.max(tw, sw);
    const lh = Math.max(3, 0.07 * s), ly = 1.08 * s;
    gh = ly + lh + (sub ? 0.25 * s + ss : 0);
    T(title, TITLE_FONT, s, false, gw / 2, s / 2, color, 'fade', 0.1);
    parts.push({ kind: 'rect', x: 0, y: ly, w: gw, h: lh, color: accent, anim: 'wipe', delay: 0 });
    if (sub) T(sub, SUB_FONT, ss, true, gw / 2, ly + lh + 0.25 * s + ss / 2, white, 'slide_up', 0.3);
  } else {  // line
    const L = 0.22 * W, lh = Math.max(3, 0.05 * s), tx = L + 0.25 * s;
    gw = tx + Math.max(tw, sw);
    gh = s + (sub ? 0.15 * s + ss : 0);
    parts.push({ kind: 'rect', x: 0, y: s / 2 - lh / 2, w: L, h: lh, color: accent, anim: 'wipe', delay: 0 });
    T(title, TITLE_FONT, s, false, tx, s / 2, color, 'slide_left', 0.15, 'left');
    if (sub) T(sub, SUB_FONT, ss, true, tx, s + 0.15 * s + ss / 2, white, 'fade', 0.3, 'left');
  }
  const X = (+(o.x ?? 0.5)) * W, Y = (+(o.y ?? 0.3)) * H;
  return { x0: X - gw / 2, y0: Y - gh / 2, w: gw, h: gh, rot, cx: X, cy: Y, parts, size: s };
}

// HTML for a motion title at time t (k = css px per canvas px).
export function titleHtml(o, templates, W, H, k, t) {
  const lay = titleLayout(o, templates, W, H);
  const a = +o.start, b = +o.end;
  const outDur = Math.min(OUTRO, (b - a) / 3);
  const exit = clamp((b - t) / outDur, 0, 1);
  const html = lay.parts.map((p) => {
    const local = t - (a + p.delay);
    if (local < 0) return '';
    const u = clamp(local / INTRO, 0, 1);
    let tf = '', op = exit, origin = 'center';
    if (p.anim === 'wipe') { tf = `scaleX(${u})`; origin = 'left center'; }
    else if (p.anim === 'pop') tf = `scale(${u < 0.7 ? 1.1 * u / 0.7 : 1.1 - 0.1 * (u - 0.7) / 0.3})`;
    else if (p.anim === 'fade') op *= u;
    else if (p.anim === 'slide_up') { tf = `translateY(${(1 - u) * 0.6 * p.size * k}px)`; op *= u; }
    else if (p.anim === 'slide_left') { tf = `translateX(${(1 - u) * 0.8 * p.size * k}px)`; op *= u; }
    if (p.kind === 'rect') {
      return `<i style="position:absolute;left:${p.x * k}px;top:${p.y * k}px;width:${p.w * k}px;height:${p.h * k}px;
        background:${p.color};opacity:${op};transform:${tf};transform-origin:${origin}"></i>`;
    }
    if (p.kind === 'circle') {
      return `<i style="position:absolute;left:${(p.x - p.r) * k}px;top:${(p.y - p.r) * k}px;width:${2 * p.r * k}px;height:${2 * p.r * k}px;
        border-radius:50%;background:${p.color};opacity:${op};transform:${tf}"></i>`;
    }
    const anchor = p.align === 'left' ? 'translate(0,-50%)' : 'translate(-50%,-50%)';
    return `<span style="position:absolute;left:${p.x * k}px;top:${p.y * k}px;font-family:'${p.font}';font-weight:${p.bold ? 700 : 400};
      font-size:${p.size * k}px;color:${p.color};white-space:pre;line-height:1;opacity:${op};transform:${anchor} ${tf}">${esc(p.text)}</span>`;
  }).join('');
  return { lay, html };
}
