// Built-in cartoon avatar faces, drawn on a canvas, one per expression.
// Also builds avatar sets from a person's own photo (photo + expression badge).

const TAU = Math.PI * 2;

function face(ctx, s, expr, talk, skin) {
  const c = ctx;
  const cx = s / 2, cy = s / 2, r = s * 0.42;
  c.save();
  if (expr === 'rofl') { c.translate(cx, cy); c.rotate(-0.45); c.translate(-cx, -cy); }
  // head
  const g = c.createRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 0.1, cx, cy, r);
  const base = expr === 'angry' ? '#ff6a4d' : skin;
  g.addColorStop(0, lighten(base, 0.35));
  g.addColorStop(1, base);
  c.fillStyle = g;
  c.strokeStyle = 'rgba(0,0,0,0.55)';
  c.lineWidth = s * 0.02;
  c.beginPath(); c.arc(cx, cy, r, 0, TAU); c.fill(); c.stroke();

  const ink = '#2b1d0e';
  c.strokeStyle = ink; c.fillStyle = ink; c.lineCap = 'round'; c.lineJoin = 'round';
  const ex = s * 0.14, ey = cy - s * 0.07, lw = s * 0.035;
  c.lineWidth = lw;
  const eyes = (fn) => { fn(cx - ex, -1); fn(cx + ex, 1); };
  const line = (x1, y1, x2, y2) => { c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke(); };

  // eyes + brows
  switch (expr) {
    case 'happy': case 'excited':
      eyes((x) => { c.beginPath(); c.arc(x, ey + s * 0.02, s * 0.045, Math.PI * 1.1, Math.PI * 1.9); c.stroke(); });
      if (expr === 'excited') eyes((x) => star(c, x, ey - s * 0.005, s * 0.05, '#ffcc00', ink));
      break;
    case 'lol': case 'rofl':
      eyes((x, k) => { line(x - k * s * 0.05, ey - s * 0.035, x + k * s * 0.03, ey); line(x + k * s * 0.03, ey, x - k * s * 0.05, ey + s * 0.035); });
      break;
    case 'sad': case 'disappointed':
      eyes((x, k) => {
        if (expr === 'disappointed') { line(x - s * 0.045, ey + s * 0.01, x + s * 0.045, ey + s * 0.01); c.beginPath(); c.arc(x, ey + s * 0.015, s * 0.022, 0, Math.PI); c.fill(); }
        else { c.beginPath(); c.arc(x, ey + s * 0.01, s * 0.03, 0, TAU); c.fill(); }
        line(x - k * s * 0.06, ey - s * 0.06, x + k * s * 0.03, ey - s * 0.085);
      });
      break;
    case 'mad': case 'angry':
      eyes((x, k) => {
        c.beginPath(); c.arc(x, ey + s * 0.015, s * 0.03, 0, TAU); c.fill();
        c.lineWidth = lw * (expr === 'angry' ? 1.5 : 1.1);
        line(x - k * s * 0.07, ey - s * 0.085, x + k * s * 0.03, ey - s * 0.035);
        c.lineWidth = lw;
      });
      break;
    case 'surprised': case 'scared':
      eyes((x) => {
        c.fillStyle = '#fff'; c.beginPath(); c.arc(x, ey, s * 0.055, 0, TAU); c.fill(); c.stroke();
        c.fillStyle = ink; c.beginPath(); c.arc(x, ey + s * 0.005, s * 0.022, 0, TAU); c.fill();
        line(x - s * 0.05, ey - s * 0.1, x + s * 0.05, ey - s * 0.11);
      });
      break;
    case 'face_slap':
      eyes((x, k) => { line(x - s * 0.045, ey - k * 0.0, x + s * 0.045, ey); });
      break;
    default:  // neutral
      eyes((x) => { c.beginPath(); c.arc(x, ey, s * 0.032, 0, TAU); c.fill(); });
  }

  // mouth
  const my = cy + s * 0.13;
  const open = (w, h, tongue = true) => {
    c.fillStyle = '#5a1a14';
    c.beginPath(); c.ellipse(cx, my, w, h, 0, 0, TAU); c.fill(); c.stroke();
    if (tongue) { c.fillStyle = '#ff7b8a'; c.beginPath(); c.ellipse(cx, my + h * 0.45, w * 0.55, h * 0.4, 0, 0, TAU); c.fill(); }
  };
  switch (expr) {
    case 'happy': case 'excited':
      if (talk || expr === 'excited') { grin(c, cx, my - s * 0.02, s * 0.14, s * (talk ? 0.11 : 0.09)); }
      else { c.beginPath(); c.arc(cx, my - s * 0.07, s * 0.13, Math.PI * 0.18, Math.PI * 0.82); c.stroke(); }
      break;
    case 'lol': grin(c, cx, my - s * 0.03, s * 0.16, s * (talk ? 0.15 : 0.12)); tears(c, cx, ey, s, 1); break;
    case 'rofl': grin(c, cx, my - s * 0.03, s * 0.17, s * (talk ? 0.16 : 0.13)); tears(c, cx, ey, s, 2); break;
    case 'sad':
      if (talk) open(s * 0.07, s * 0.05, false);
      else { c.beginPath(); c.arc(cx, my + s * 0.08, s * 0.1, Math.PI * 1.2, Math.PI * 1.8); c.stroke(); }
      tears(c, cx, ey, s, 1, true);
      break;
    case 'disappointed':
      if (talk) open(s * 0.06, s * 0.04, false);
      else { c.beginPath(); c.arc(cx, my + s * 0.06, s * 0.07, Math.PI * 1.25, Math.PI * 1.75); c.stroke(); }
      sweat(c, cx + s * 0.27, cy - s * 0.18, s);
      break;
    case 'mad':
      if (talk) open(s * 0.08, s * 0.045, false);
      else { c.beginPath(); c.moveTo(cx - s * 0.09, my + s * 0.02); c.quadraticCurveTo(cx, my - s * 0.02, cx + s * 0.09, my + s * 0.02); c.stroke(); }
      break;
    case 'angry':
      teeth(c, cx, my, s * 0.13, s * (talk ? 0.09 : 0.06));
      steam(c, cx, cy, s);
      break;
    case 'surprised': open(s * 0.055, s * (talk ? 0.09 : 0.07), false); break;
    case 'scared': wavy(c, cx, my, s * 0.12, s * (talk ? 0.03 : 0.018)); sweat(c, cx - s * 0.28, cy - s * 0.2, s); break;
    case 'face_slap':
      if (talk) open(s * 0.06, s * 0.04, false);
      else line(cx - s * 0.06, my, cx + s * 0.06, my);
      hand(c, cx, cy, s, skin);
      break;
    default:
      if (talk) open(s * 0.07, s * 0.055, false);
      else line(cx - s * 0.08, my, cx + s * 0.08, my);
  }
  // blush
  if (['happy', 'lol', 'rofl', 'excited'].includes(expr)) {
    c.fillStyle = 'rgba(255,90,90,0.35)';
    for (const k of [-1, 1]) { c.beginPath(); c.ellipse(cx + k * s * 0.24, my - s * 0.04, s * 0.05, s * 0.03, 0, 0, TAU); c.fill(); }
  }
  c.restore();
}

function grin(c, cx, y, w, h) {
  c.fillStyle = '#5a1a14';
  c.beginPath(); c.moveTo(cx - w, y); c.quadraticCurveTo(cx, y + h * 2.1, cx + w, y); c.closePath(); c.fill(); c.stroke();
  c.fillStyle = '#fff';
  c.beginPath(); c.moveTo(cx - w * 0.85, y + h * 0.05); c.lineTo(cx + w * 0.85, y + h * 0.05);
  c.quadraticCurveTo(cx, y + h * 0.55, cx - w * 0.85, y + h * 0.05); c.fill();
  c.fillStyle = '#ff7b8a';
  c.beginPath(); c.ellipse(cx, y + h * 0.85, w * 0.45, h * 0.3, 0, 0, TAU); c.fill();
}

function teeth(c, cx, y, w, h) {
  c.fillStyle = '#fff';
  c.beginPath(); c.rect(cx - w, y - h / 2, w * 2, h); c.fill(); c.stroke();
  c.lineWidth /= 2;
  for (let i = -2; i <= 2; i++) { c.beginPath(); c.moveTo(cx + i * w / 3, y - h / 2); c.lineTo(cx + i * w / 3, y + h / 2); c.stroke(); }
  c.beginPath(); c.moveTo(cx - w, y); c.lineTo(cx + w, y); c.stroke();
  c.lineWidth *= 2;
}

function wavy(c, cx, y, w, a) {
  c.beginPath();
  for (let i = 0; i <= 24; i++) {
    const x = cx - w + (2 * w * i) / 24;
    const yy = y + Math.sin(i / 24 * Math.PI * 4) * a;
    i ? c.lineTo(x, yy) : c.moveTo(x, yy);
  }
  c.stroke();
}

function tears(c, cx, ey, s, n, single = false) {
  c.save();
  c.fillStyle = 'rgba(80,170,255,0.9)';
  for (const k of single ? [1] : [-1, 1]) {
    for (let j = 0; j < n; j++) {
      const x = cx + k * (s * 0.2 + j * s * 0.05), y = ey + s * 0.06 + j * s * 0.07;
      c.beginPath(); c.moveTo(x, y); c.quadraticCurveTo(x + s * 0.04, y + s * 0.06, x, y + s * 0.08);
      c.quadraticCurveTo(x - s * 0.04, y + s * 0.06, x, y); c.fill();
    }
  }
  c.restore();
}

function sweat(c, x, y, s) {
  c.save();
  c.fillStyle = 'rgba(120,190,255,0.95)';
  c.beginPath(); c.moveTo(x, y); c.quadraticCurveTo(x + s * 0.05, y + s * 0.07, x, y + s * 0.09);
  c.quadraticCurveTo(x - s * 0.05, y + s * 0.07, x, y); c.fill();
  c.restore();
}

function steam(c, cx, cy, s) {
  c.save();
  c.strokeStyle = 'rgba(255,255,255,0.9)';
  c.lineWidth = s * 0.025;
  for (const k of [-1, 1]) {
    const x = cx + k * s * 0.36, y = cy - s * 0.34;
    c.beginPath(); c.moveTo(x, y); c.quadraticCurveTo(x + k * s * 0.05, y - s * 0.04, x, y - s * 0.08);
    c.quadraticCurveTo(x - k * s * 0.05, y - s * 0.12, x, y - s * 0.16); c.stroke();
  }
  c.restore();
}

function star(c, x, y, r, fill, ink) {
  c.save();
  c.fillStyle = fill;
  c.beginPath();
  for (let i = 0; i < 10; i++) {
    const a = -Math.PI / 2 + i * Math.PI / 5, rr = i % 2 ? r * 0.45 : r;
    c.lineTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr);
  }
  c.closePath(); c.fill(); c.strokeStyle = ink; c.lineWidth = r * 0.12; c.stroke();
  c.restore();
}

function hand(c, cx, cy, s, skin) {
  c.save();
  c.translate(cx + s * 0.1, cy - s * 0.02);
  c.rotate(-0.25);
  c.fillStyle = lighten(skin, 0.1);
  c.strokeStyle = 'rgba(0,0,0,0.6)';
  c.lineWidth = s * 0.015;
  // palm
  c.beginPath(); c.ellipse(0, s * 0.02, s * 0.13, s * 0.15, 0, 0, TAU); c.fill(); c.stroke();
  // fingers
  for (let i = 0; i < 4; i++) {
    c.beginPath(); c.ellipse(-s * 0.09 + i * s * 0.06, -s * 0.15, s * 0.028, s * 0.08, 0, 0, TAU); c.fill(); c.stroke();
  }
  c.beginPath(); c.ellipse(-s * 0.16, s * 0.02, s * 0.03, s * 0.07, -0.8, 0, TAU); c.fill(); c.stroke();
  c.restore();
  // red slap mark
  c.save(); c.fillStyle = 'rgba(255,40,40,0.35)';
  c.beginPath(); c.ellipse(cx - s * 0.24, cy + s * 0.05, s * 0.06, s * 0.05, 0, 0, TAU); c.fill(); c.restore();
}

function lighten(hex, k) {
  const n = parseInt((hex || '#FFD54A').replace('#', ''), 16);
  const r = n >> 16 & 255, g = n >> 8 & 255, b = n & 255;
  const f = (v) => Math.round(v + (255 - v) * k);
  return `rgb(${f(r)},${f(g)},${f(b)})`;
}

export const SKIN = '#FFD54A';

export function drawCartoon(expr, talk, skin = SKIN, size = 256) {
  const cv = document.createElement('canvas');
  cv.width = cv.height = size;
  face(cv.getContext('2d'), size, expr, talk, skin);
  return cv;
}

// A person's photo in a circle with a small cartoon badge showing the expression.
export function drawPhotoAvatar(img, expr, talk, ring = '#FFFFFF', size = 256) {
  const cv = document.createElement('canvas');
  cv.width = cv.height = size;
  const c = cv.getContext('2d');
  const r = size * 0.4, cx = size * 0.45, cy = size * 0.45;
  c.save();
  c.beginPath(); c.arc(cx, cy, r, 0, TAU); c.closePath(); c.clip();
  const s = Math.max((2 * r) / img.width, (2 * r) / img.height);
  c.drawImage(img, cx - img.width * s / 2, cy - img.height * s / 2, img.width * s, img.height * s);
  if (talk) { c.fillStyle = 'rgba(255,255,255,0.08)'; c.fillRect(0, 0, size, size); }
  c.restore();
  c.lineWidth = size * 0.035; c.strokeStyle = ring;
  c.beginPath(); c.arc(cx, cy, r, 0, TAU); c.stroke();
  const badge = drawCartoon(expr, talk, SKIN, Math.round(size * 0.48));
  c.drawImage(badge, size - badge.width - size * 0.01, size - badge.height - size * 0.01);
  return cv;
}

export function canvasToBlob(cv) {
  return new Promise((res) => cv.toBlob(res, 'image/png'));
}
