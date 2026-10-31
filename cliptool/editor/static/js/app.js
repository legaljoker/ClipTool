// ClipTool Editor - main app: panels, inspector, drag & drop, saving, jobs.
import { History, activeClips, clipLength, clipStarts, fmtTime, rippleDelete, totalDuration, uid } from './model.js';
import { Preview } from './preview.js';
import { EMOJI, Timeline } from './timeline.js';
import { canvasToBlob, drawCartoon, drawPhotoAvatar } from './faces.js';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const VIDEO_EXT = /\.(mp4|mov|mkv|webm|avi|m4v|flv|wmv|mts|ts)$/i;
const IMAGE_EXT = /\.(png|jpe?g|webp|gif)$/i;
const MAIN_EXPRESSIONS = ['neutral', 'happy', 'sad', 'mad', 'angry', 'rofl', 'lol', 'disappointed', 'face_slap'];

async function api(method, url, body, raw = false) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (raw) opts.body = body;
    else { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  }
  const res = await fetch(url, opts);
  const data = res.headers.get('Content-Type')?.includes('json') ? await res.json() : null;
  if (!res.ok) throw new Error(data?.error || res.statusText);
  return data;
}

function upload(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (_) { /* ignore */ }
      xhr.status < 300 ? resolve(data) : reject(new Error(data?.error || xhr.statusText));
    };
    xhr.onerror = () => reject(new Error('Upload failed'));
    xhr.send(file);
  });
}

class App {
  constructor() {
    this.project = null;
    this.pid = null;
    this.selection = null;
    this.time = 0;
    this.tab = 'media';
    this.snapOn = true;
    this.history = new History();
    this.saveTimer = null;
    this.version = 0;  // bumps on avatar uploads to refresh images
  }

  async init() {
    this.meta = await api('GET', '/api/meta');
    this.preview = new Preview(this, $('#stage'));
    this.timeline = new Timeline(this, $('#timeline'));
    $('#preset').innerHTML = Object.entries(this.meta.presets).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join('');
    this.bindChrome();
    const want = new URLSearchParams(location.hash.slice(1)).get('p');
    const list = await api('GET', '/api/projects');
    if (want && list.some(p => p.id === want)) await this.openProject(want);
    else this.showProjects(list);
  }

  // ---- projects -------------------------------------------------------------
  showProjects(list) {
    const items = list.map(p => `<button class="btn" data-open="${esc(p.id)}"><b>${esc(p.name)}</b>
      <span class="muted"> · ${p.clips} clip${p.clips === 1 ? '' : 's'} · ${esc(p.modified.replace('T', ' '))}</span></button>`).join('');
    this.modal(`<h3>ClipTool Editor</h3>
      <div class="field"><label>Start a new video</label>
        <div class="row"><input type="text" id="new-name" placeholder="Name, e.g. Golf funny moments" style="flex:1">
        <button class="btn primary" id="new-go">Create</button></div></div>
      ${list.length ? `<h4>Or open a project</h4><div class="proj-list">${items}</div>` : ''}
      ${this.project ? '<div class="row"><div class="spacer"></div><button class="btn" data-close>Cancel</button></div>' : ''}`);
    const go = async () => {
      const name = $('#new-name').value.trim() || 'My video';
      const r = await api('POST', '/api/projects', { name });
      this.closeModal();
      await this.openProject(r.id);
    };
    $('#new-go').onclick = go;
    $('#new-name').onkeydown = (e) => e.key === 'Enter' && go();
    $('#new-name').focus();
    $$('[data-open]').forEach(b => b.onclick = async () => { this.closeModal(); await this.openProject(b.dataset.open); });
  }

  async openProject(pid) {
    await this.flushSave();
    this.pid = pid;
    this.project = await api('GET', `/api/projects/${pid}`);
    this.history = new History();
    this.selection = null;
    location.hash = `p=${encodeURIComponent(pid)}`;
    $('#project-name').value = this.project.name;
    $('#preset').value = this.project.preset;
    this.preview.layout();
    this.preview.seek(0);
    this.renderAll();
    setTimeout(() => this.timeline.fit(), 50);
  }

  // ---- change tracking / saving ------------------------------------------------
  beginChange() { this._before = JSON.stringify(this.project); }

  endChange(changed = true) {
    if (changed && this._before && this._before !== JSON.stringify(this.project)) {
      this.history.push(this._before);
      this.dirty();
    }
    this._before = null;
    this.renderAll();
  }

  change(fn) {
    this.beginChange();
    fn(this.project);
    this.endChange(true);
  }

  dirty() {
    $('#save-state').textContent = 'Saving…';
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.flushSave(), 700);
  }

  async flushSave() {
    clearTimeout(this.saveTimer);
    if (!this.project || !this.pid) return;
    try {
      await api('PUT', `/api/projects/${this.pid}`, this.project);
      $('#save-state').textContent = 'Saved';
    } catch (e) {
      $('#save-state').textContent = 'Not saved!';
      this.toast(`Could not save: ${e.message}`, true);
    }
  }

  undo() {
    const s = this.history.undo(JSON.stringify(this.project));
    if (s) { this.project = JSON.parse(s); this.dirty(); this.renderAll(); }
  }

  redo() {
    const s = this.history.redo(JSON.stringify(this.project));
    if (s) { this.project = JSON.parse(s); this.dirty(); this.renderAll(); }
  }

  // ---- rendering the UI ---------------------------------------------------------
  renderAll() {
    if (!this.project) return;
    this.renderPanel();
    this.renderInspector();
    this.timeline.render();
    this.preview.layout();
    this.preview.sync(true);
    this.preview.draw();
    this.onTime(this.time);
  }

  onTime(t) {
    this.time = t;
    const d = totalDuration(this.project, this.meta.transitions);
    $('#time').textContent = `${fmtTime(t)} / ${fmtTime(d)}`;
    this.timeline.updatePlayhead(t);
  }

  onPlayState(playing) { $('#btn-play').textContent = playing ? '❚❚' : '▶'; }
  onZoom(pxps) { $('#zoom').value = pxps; }
  seek(t) { this.preview.seek(t); }

  select(sel, focus) {
    this.selection = sel;
    this.focus = focus;
    this.renderInspector();
    this.timeline.render();
    if (this.tab === 'captions') this.renderPanel();
    this.preview.lastHtml = null;
    this.preview.draw();
  }

  findItem(kind, id) {
    const p = this.project;
    const list = { caption: p.captions, effect: p.effects, overlay: p.overlays, clip: p.clips }[kind];
    return list ? list.find(x => x.id === id) : null;
  }

  avatarUrl(person, expr, talking) {
    const av = person.avatars || {};
    const chain = [expr, ...(this.meta.expression_fallbacks[expr] || []), 'neutral'];
    for (const e of chain) {
      if (talking && av[`${e}_talk`]) return `/files/${this.pid}/${av[`${e}_talk`]}?v=${this.version}`;
      if (av[e]) return `/files/${this.pid}/${av[e]}?v=${this.version}`;
    }
    const any = Object.entries(av).find(([k, v]) => v && !k.endsWith('_talk'));
    return any ? `/files/${this.pid}/${any[1]}?v=${this.version}` : null;
  }

  // ---- side panel -----------------------------------------------------------------
  renderPanel() {
    const el = $('#panel');
    const scroll = el.scrollTop;
    const fn = { media: this.panelMedia, captions: this.panelCaptions, people: this.panelPeople, style: this.panelStyle,
      effects: this.panelEffects, transitions: this.panelTransitions, text: this.panelText, export: this.panelExport }[this.tab];
    el.innerHTML = fn.call(this);
    this.bindPanel(el);
    el.scrollTop = scroll;
  }

  panelMedia() {
    const p = this.project;
    const cards = p.media.map(m => `<div class="card media-card" draggable="true" data-drag='${JSON.stringify({ kind: 'media', id: m.id })}'>
        ${m.thumb ? `<img src="/files/${this.pid}/${m.thumb}">` : '<div class="noimg"></div>'}
        <div><div class="name" title="${esc(m.name)}">${esc(m.name)}</div>
        <div class="muted">${fmtTime(m.duration, false)} · ${m.width}×${m.height}${m.proxy === null && !/\.(mp4|mov|m4v|webm)$/i.test(m.name) ? ' · preparing preview…' : ''}</div>
        <button class="btn small" data-add-media="${m.id}">+ Add to timeline</button></div></div>`).join('');
    return `<h3>Media</h3>
      <div class="dropzone" data-pick="video">Drop videos here<br><small>or click to choose files</small></div>
      <button class="btn" data-input-folder>📂 Add from the input folder</button>
      <div id="input-files"></div>
      <p class="hint">Drag a video onto the timeline, or press “+ Add to timeline”. It goes at the end (or where you drop it).</p>
      <div class="cards">${cards || '<p class="muted">No videos yet.</p>'}</div>`;
  }

  panelCaptions() {
    const p = this.project, st = p.caption_style;
    const people = p.people;
    const peopleOpts = (sel) => `<option value="">No person</option>` +
      people.map(x => `<option value="${x.id}" ${x.id === sel ? 'selected' : ''}>${esc(x.name)}</option>`).join('');
    const exprOpts = (sel) => this.meta.expressions.map(e =>
      `<option value="${e}" ${e === sel ? 'selected' : ''}>${EMOJI[e] || ''} ${esc(this.meta.expression_labels[e])}</option>`).join('');
    const rows = [...p.captions].sort((a, b) => a.start - b.start).map(c => {
      const person = people.find(x => x.id === c.person);
      const s = this.selection;
      return `<div class="cap-row ${s && s.kind === 'caption' && s.id === c.id ? 'selected' : ''}" data-cap="${c.id}" style="--c:${person ? person.color : '#666'}">
        <div class="meta"><span class="t">${fmtTime(c.start)}</span>
          <select data-cap-person="${c.id}">${peopleOpts(c.person)}</select>
          <select data-cap-expr="${c.id}">${exprOpts(c.expression)}</select></div>
        <input type="text" data-cap-text="${c.id}" value="${esc(c.text)}"></div>`;
    }).join('');
    return `<h3>Captions</h3>
      <div class="card">
        <b>Automatic captions</b>
        <p class="hint">Turns the speech in your clips into captions. The first time downloads the speech model (a few minutes).</p>
        <label class="check"><input type="checkbox" id="tr-speakers" checked> Work out who is talking (Person 1, Person 2…)</label>
        <div class="row"><label>How many people</label><input type="number" id="tr-num" min="1" max="8" placeholder="auto" style="width:70px"></div>
        <label class="check"><input type="checkbox" id="tr-expr" checked> Guess expressions (happy, mad, LOL…)</label><br>
        <label class="check"><input type="checkbox" id="tr-replace" checked> Replace the captions I have</label>
        <div class="row"><button class="btn primary" id="btn-transcribe">💬 Create captions from speech</button></div>
      </div>
      <div class="row"><button class="btn" id="btn-add-caption">+ Add caption at playhead</button>
        <button class="btn" data-tab-go="people">🙂 People & avatars</button></div>
      <h4>Caption look</h4>
      <p class="hint">Drag the caption on the video to place it.</p>
      <div class="row"><label>Font</label><input type="text" data-style="font" value="${esc(st.font)}" style="flex:1"></div>
      <div class="row"><label>Size</label><input type="range" min="30" max="160" data-style="size" value="${st.size}"></div>
      <div class="row"><label>Words at once</label><input type="range" min="1" max="8" data-style="max_words" value="${st.max_words}"><span>${st.max_words}</span></div>
      <div class="row"><label>Colors</label>
        <input type="color" data-style="color" value="${st.color}" title="Text">
        <input type="color" data-style="highlight" value="${st.highlight}" title="Word being spoken">
        <input type="color" data-style="outline" value="${st.outline}" title="Outline"></div>
      <div class="row"><label>Outline</label><input type="range" min="0" max="20" data-style="outline_width" value="${st.outline_width}"></div>
      <label class="check"><input type="checkbox" data-style-check="uppercase" ${st.uppercase ? 'checked' : ''}> ALL CAPS</label>
      <label class="check"><input type="checkbox" data-style-check="use_person_color" ${st.use_person_color ? 'checked' : ''}> Each person's own color</label>
      <label class="check"><input type="checkbox" data-style-check="pop" ${st.pop ? 'checked' : ''}> Pop the spoken word</label>
      <h4>Avatar with captions</h4>
      <label class="check"><input type="checkbox" data-style-check="show_avatar" ${st.show_avatar ? 'checked' : ''}> Show the speaker's avatar</label>
      <div class="row"><label>Avatar size</label><input type="range" min="0.08" max="0.5" step="0.01" data-style="avatar_size" value="${st.avatar_size}"></div>
      <div class="row"><label>Avatar place</label><select data-style="avatar_place">
        ${['above', 'left', 'right'].map(v => `<option ${st.avatar_place === v ? 'selected' : ''}>${v}</option>`).join('')}</select></div>
      <h4>All captions (${p.captions.length})</h4>
      <div class="cap-list">${rows || '<p class="muted">No captions yet.</p>'}</div>`;
  }

  panelPeople() {
    const p = this.project;
    const cards = p.people.map((person, i) => {
      const slots = this.meta.expressions.map(e => {
        const rel = person.avatars?.[e];
        return `<div class="av-slot ${rel ? 'has' : ''}" data-avatar="${person.id}" data-expr="${e}" title="${esc(this.meta.expression_labels[e])}: drop a picture or click">
          ${rel ? `<img src="/files/${this.pid}/${rel}?v=${this.version}">` : `<b style="font-size:20px">${EMOJI[e] || ''}</b>`}
          <span>${esc(this.meta.expression_labels[e])}</span></div>`;
      }).join('');
      const nCaps = p.captions.filter(c => c.person === person.id).length;
      return `<div class="card person" style="border-left:4px solid ${person.color}">
        <div class="row"><input type="text" data-person-name="${person.id}" value="${esc(person.name)}">
          <input type="color" data-person-color="${person.id}" value="${person.color}" title="Caption color"></div>
        <div class="muted">${nCaps} caption${nCaps === 1 ? '' : 's'}</div>
        <div class="row">
          <button class="btn small" data-cartoon="${person.id}">🎨 Cartoon faces</button>
          <button class="btn small" data-photo="${person.id}">📷 From a photo</button>
          <button class="btn small danger" data-del-person="${person.id}">Remove</button></div>
        <div class="av-grid">${slots}</div></div>`;
    }).join('');
    return `<h3>People & avatars</h3>
      <p class="hint">Make a person for everyone who talks. Pick who says each caption in the Captions tab;
      their avatar for that caption's expression shows next to the caption in the video.</p>
      <p class="hint">Drop a picture on an expression, or use <b>Cartoon faces</b> / <b>From a photo</b> to fill them all.
      Pictures with a transparent background look best.</p>
      <button class="btn primary" id="btn-add-person">+ Add person</button>
      <div style="margin-top:10px">${cards || '<p class="muted">No people yet.</p>'}</div>`;
  }

  panelStyle() {
    const fr = this.project.frame;
    const styles = { ...this.meta.frame_styles, ...this.meta.crop_modes };
    const ico = (k) => {
      const bg = { blur: 'linear-gradient(#556,#334)', blur_dark: 'linear-gradient(#223,#112)', blur_bw: 'linear-gradient(#999,#555)',
        pixelate: 'repeating-conic-gradient(#668 0 25%,#446 0 50%) 0 0/8px 8px', mirror: 'linear-gradient(#a33,#fd3 30%,#fd3 70%,#a33)',
        color: fr.color, gradient: `linear-gradient(${fr.color},${fr.color2})`, black: '#000', smart: 'linear-gradient(90deg,#e33,#fd3,#3c6,#39f)',
        crop: 'linear-gradient(90deg,#fd3,#3c6)' }[k];
      const fill = k === 'smart' || k === 'crop';
      return `<div class="style-ico" style="background:${bg}">${fill ? '' : '<i></i>'}</div>`;
    };
    const tiles = Object.entries(styles).map(([k, label]) =>
      `<div class="tile ${fr.style === k ? 'active' : ''}" data-frame="${k}"><div class="ico">${ico(k)}</div>${esc(k.replace('_', ' '))}<small>${esc(label)}</small></div>`).join('');
    return `<h3>Video style</h3>
      <p class="hint">How a wide video fits a tall TikTok / Shorts frame. The video stays in the middle and the space around it gets the style you pick.</p>
      <div class="field"><label>Video shape</label><select id="preset2">${Object.entries(this.meta.presets).map(([k, v]) =>
        `<option value="${k}" ${this.project.preset === k ? 'selected' : ''}>${esc(v.label)}</option>`).join('')}</select></div>
      <div class="tiles">${tiles}</div>
      <h4>Adjust</h4>
      <div class="row"><label>Video size</label><input type="range" min="0.6" max="2" step="0.01" data-frame-num="scale" value="${fr.scale}"><span>${Math.round(fr.scale * 100)}%</span></div>
      <div class="row"><label>Up / down</label><input type="range" min="-0.4" max="0.4" step="0.01" data-frame-num="offset_y" value="${fr.offset_y}"></div>
      <div class="row"><label>Blur amount</label><input type="range" min="4" max="60" data-frame-num="blur" value="${fr.blur}"></div>
      <div class="row"><label>Colors</label><input type="color" data-frame-color="color" value="${fr.color}"><input type="color" data-frame-color="color2" value="${fr.color2}"></div>
      <button class="btn small" data-frame-reset>Reset</button>`;
  }

  panelEffects() {
    const tiles = Object.entries(this.meta.effects).map(([k, e]) =>
      `<div class="tile" draggable="true" data-drag='${JSON.stringify({ kind: 'effect', type: k })}' data-add-effect="${k}" title="${esc(e.help)}">
        <div class="ico">${{ zoom_in: '🔍', zoom_out: '🔭', shake: '📳', flash: '⚡', black_white: '◐', sepia: '🟫', blur: '🌫', vignette: '⭕', invert: '🔄', glitch: '👾' }[k] || '✨'}</div>
        ${esc(e.label)}<small>${esc(e.help)}</small></div>`).join('');
    return `<h3>Effects</h3>
      <p class="hint">Drag an effect onto the timeline (or onto the video), or click it to add it at the playhead.
      Drag the ends of the purple bar to set how long it lasts. For zooms, drag the yellow circle on the video to pick the spot.</p>
      <div class="tiles">${tiles}</div>`;
  }

  panelTransitions() {
    const tiles = Object.entries(this.meta.transitions).map(([k, label]) =>
      `<div class="tile" draggable="true" data-drag='${JSON.stringify({ kind: 'transition', type: k })}' data-add-transition="${k}">
        <div class="ico">⇄</div>${esc(label)}</div>`).join('');
    return `<h3>Transitions</h3>
      <p class="hint">Drag a transition onto the joint between two clips on the timeline (the “+” at the start of a clip),
      or click one to use it on the selected clip.</p>
      <div class="row"><label>Length</label><input type="range" min="0.2" max="2" step="0.1" id="tr-len" value="${this.trLen || 0.5}"><span>${this.trLen || 0.5}s</span></div>
      <div class="row"><button class="btn small" id="tr-all">Use on every cut</button><button class="btn small" id="tr-none">Remove all</button></div>
      <div class="tiles">${tiles}</div>`;
  }

  panelText() {
    const list = this.project.overlays.map(o => `<div class="card row" data-sel-overlay="${o.id}" style="cursor:pointer">
      ${o.type === 'image' ? `<img src="/files/${this.pid}/${o.src}" style="width:40px;height:40px;object-fit:contain">` : '<b>T</b>'}
      <span style="flex:1">${esc(o.type === 'image' ? o.src.split('/').pop() : o.text)}</span>
      <span class="muted">${fmtTime(o.start, false)}–${fmtTime(o.end, false)}</span></div>`).join('');
    return `<h3>Text & pictures</h3>
      <div class="tiles">
        <div class="tile" draggable="true" data-drag='${JSON.stringify({ kind: 'text' })}' id="btn-add-text"><div class="ico">T</div>Add text<small>Titles, call-outs</small></div>
        <div class="tile" data-pick="image"><div class="ico">🖼</div>Add picture<small>Stickers, logos, emojis</small></div>
      </div>
      <div class="dropzone" data-pick="image" style="margin-top:10px">Drop pictures here, or straight onto the video where you want them</div>
      <p class="hint">Drag them on the video to move them; drag the yellow corner to resize. Their bar on the timeline sets when they show.</p>
      <div class="cards">${list}</div>`;
  }

  panelExport() {
    const p = this.project;
    return `<h3>Export</h3>
      <div class="field"><label>Video shape</label><b>${esc(this.meta.presets[p.preset].label)}</b>
        (${this.meta.presets[p.preset].width}×${this.meta.presets[p.preset].height})</div>
      <div class="field"><label>Quality</label><select id="quality">
        <option value="high">Upload quality (platform bitrate)</option><option value="draft">Quick draft (faster, smaller)</option></select></div>
      <label class="check"><input type="checkbox" id="loudnorm" ${p.export?.loudnorm !== false ? 'checked' : ''}> Even out the volume (-14 LUFS, platform standard)</label>
      <div class="row"><button class="btn primary" id="btn-render2">⬇ Render video</button>
        <button class="btn" id="btn-open-folder">📂 Open exports folder</button></div>
      <h4>Finished videos</h4><div id="exports" class="cards"><p class="muted">Loading…</p></div>`;
  }

  bindPanel(el) {
    const p = this.project;
    // generic: drag sources
    $$('[data-drag]', el).forEach(d => d.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('application/x-cliptool', d.dataset.drag);
      e.dataTransfer.effectAllowed = 'copy';
    }));
    $$('[data-pick]', el).forEach(z => {
      z.onclick = () => this.pickFiles(z.dataset.pick === 'video' ? 'video/*,.mkv,.ts,.mts' : 'image/*', (files) => this.dropFiles(files, {}));
      z.addEventListener('dragover', (e) => { e.preventDefault(); z.classList.add('over'); });
      z.addEventListener('dragleave', () => z.classList.remove('over'));
      z.addEventListener('drop', (e) => { e.preventDefault(); z.classList.remove('over'); this.dropFiles(e.dataTransfer.files, {}); });
    });
    $$('[data-tab-go]', el).forEach(b => b.onclick = () => this.setTab(b.dataset.tabGo));

    // media
    $$('[data-add-media]', el).forEach(b => b.onclick = () => this.addClip(b.dataset.addMedia));
    const inp = $('[data-input-folder]', el);
    if (inp) inp.onclick = async () => {
      const files = await api('GET', '/api/input-files');
      $('#input-files').innerHTML = files.length ? files.map(f => `<div class="row"><span style="flex:1">${esc(f.name)}</span>
        <button class="btn small" data-import="${esc(f.path)}">Import</button></div>`).join('') : '<p class="muted">The input folder is empty.</p>';
      $$('[data-import]').forEach(b => b.onclick = async () => {
        b.disabled = true; b.textContent = 'Importing…';
        try { this.mediaAdded(await api('POST', `/api/projects/${this.pid}/media-path`, { path: b.dataset.import }), true); }
        catch (e) { this.toast(e.message, true); }
        b.textContent = 'Added';
      });
    };

    // captions
    if (this.tab === 'captions') {
      $('#btn-transcribe').onclick = () => this.transcribe();
      $('#btn-add-caption').onclick = () => this.addCaption();
      $$('[data-style]', el).forEach(i => i.addEventListener(i.type === 'range' || i.type === 'color' ? 'input' : 'change', () => {
        const v = i.type === 'range' ? +i.value : i.value;
        this.liveStyle(() => { p.caption_style[i.dataset.style] = v; }, i);
      }));
      $$('[data-style-check]', el).forEach(i => i.onchange = () => this.change(pp => { pp.caption_style[i.dataset.styleCheck] = i.checked; }));
      $$('[data-cap]', el).forEach(r => r.addEventListener('click', (e) => {
        if (e.target.closest('select,input')) return;
        const c = this.findItem('caption', r.dataset.cap);
        this.seek(c.start + 0.01);
        this.select({ kind: 'caption', id: c.id });
      }));
      $$('[data-cap-person]', el).forEach(s => s.onchange = () => this.change(() => { this.findItem('caption', s.dataset.capPerson).person = s.value || null; }));
      $$('[data-cap-expr]', el).forEach(s => s.onchange = () => this.change(() => { this.findItem('caption', s.dataset.capExpr).expression = s.value; }));
      $$('[data-cap-text]', el).forEach(i => i.onchange = () => this.change(() => { this.findItem('caption', i.dataset.capText).text = i.value; }));
    }

    // people
    const addPerson = $('#btn-add-person', el);
    if (addPerson) addPerson.onclick = () => this.addPerson();
    $$('[data-person-name]', el).forEach(i => i.onchange = () => this.change(pp => { pp.people.find(x => x.id === i.dataset.personName).name = i.value || 'Person'; }));
    $$('[data-person-color]', el).forEach(i => i.onchange = () => this.change(pp => { pp.people.find(x => x.id === i.dataset.personColor).color = i.value; }));
    $$('[data-del-person]', el).forEach(b => b.onclick = () => {
      if (!confirm('Remove this person? Their captions keep their text.')) return;
      this.change(pp => {
        pp.people = pp.people.filter(x => x.id !== b.dataset.delPerson);
        pp.captions.forEach(c => { if (c.person === b.dataset.delPerson) c.person = null; });
      });
    });
    $$('[data-avatar]', el).forEach(slot => {
      const set = (files) => files[0] && this.uploadAvatar(slot.dataset.avatar, slot.dataset.expr, files[0]);
      slot.onclick = () => this.pickFiles('image/*', set, false);
      slot.addEventListener('dragover', (e) => { e.preventDefault(); slot.classList.add('over'); });
      slot.addEventListener('dragleave', () => slot.classList.remove('over'));
      slot.addEventListener('drop', (e) => { e.preventDefault(); e.stopPropagation(); slot.classList.remove('over'); set(e.dataTransfer.files); });
    });
    $$('[data-cartoon]', el).forEach(b => b.onclick = () => this.makeAvatarSet(b.dataset.cartoon, null));
    $$('[data-photo]', el).forEach(b => b.onclick = () => this.pickFiles('image/*', (files) => {
      if (!files[0]) return;
      const img = new Image();
      img.onload = () => this.makeAvatarSet(b.dataset.photo, img);
      img.src = URL.createObjectURL(files[0]);
    }, false));

    // style
    $$('[data-frame]', el).forEach(t => t.onclick = () => this.change(pp => { pp.frame.style = t.dataset.frame; }));
    $$('[data-frame-num]', el).forEach(i => i.addEventListener('input', () => this.liveStyle(() => { p.frame[i.dataset.frameNum] = +i.value; }, i, true)));
    $$('[data-frame-color]', el).forEach(i => i.addEventListener('input', () => this.liveStyle(() => { p.frame[i.dataset.frameColor] = i.value; }, i, true)));
    const reset = $('[data-frame-reset]', el);
    if (reset) reset.onclick = () => this.change(pp => { pp.frame = { ...this.meta.frame, style: pp.frame.style }; });
    const preset2 = $('#preset2', el);
    if (preset2) preset2.onchange = () => this.setPreset(preset2.value);

    // effects / transitions / text
    $$('[data-add-effect]', el).forEach(t => t.addEventListener('click', () => this.addEffect(t.dataset.addEffect, this.time)));
    $$('[data-add-transition]', el).forEach(t => t.addEventListener('click', () => {
      const sel = this.selection;
      let clip = sel && sel.kind === 'clip' ? this.findItem('clip', sel.id) : null;
      if (!clip) { const a = activeClips(p, this.time, this.meta.transitions); clip = a.length ? a[a.length - 1].clip : null; }
      if (!clip) return this.toast('Select a clip first.');
      this.setTransition(clip.id, t.dataset.addTransition);
    }));
    const trLen = $('#tr-len', el);
    if (trLen) {
      trLen.oninput = () => { this.trLen = +trLen.value; trLen.nextElementSibling.textContent = trLen.value + 's'; };
      $('#tr-all', el).onclick = () => this.change(pp => pp.clips.forEach((c, i) => { if (i > 0) c.transition = { type: c.transition?.type || 'fade', duration: this.trLen || 0.5 }; }));
      $('#tr-none', el).onclick = () => this.change(pp => pp.clips.forEach(c => { c.transition = null; }));
    }
    const addText = $('#btn-add-text', el);
    if (addText) addText.addEventListener('click', () => this.addText(this.time));
    $$('[data-sel-overlay]', el).forEach(r => r.onclick = () => {
      const o = this.findItem('overlay', r.dataset.selOverlay);
      this.seek(o.start + 0.01);
      this.select({ kind: 'overlay', id: o.id });
    });

    // export
    if (this.tab === 'export') {
      $('#btn-render2').onclick = () => this.render();
      $('#btn-open-folder').onclick = () => api('POST', `/api/projects/${this.pid}/open-folder`);
      $('#loudnorm').onchange = (e) => this.change(pp => { pp.export = { ...(pp.export || {}), loudnorm: e.target.checked }; });
      $('#quality').value = this.quality || 'high';
      $('#quality').onchange = (e) => { this.quality = e.target.value; };
      this.loadExports();
    }
  }

  // Slider drags: update live, one undo step per drag.
  liveStyle(fn, input, light = false) {
    if (!this._live) {
      this.beginChange();
      this._live = true;
      input.addEventListener('change', () => { this._live = false; this.endChange(true); }, { once: true });
    }
    fn();
    if (input.nextElementSibling && input.nextElementSibling.tagName === 'SPAN' && input.type === 'range') {
      input.nextElementSibling.textContent = input.dataset.frameNum === 'scale' ? Math.round(input.value * 100) + '%' : input.value;
    }
    this.preview.lastHtml = null;
    this.preview.draw();
    if (input.type === 'color' && !light) { /* change event finishes */ }
  }

  setTab(tab) {
    this.tab = tab;
    $$('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    this.renderPanel();
    this.preview.lastHtml = null;
    this.preview.draw();
  }

  async loadExports() {
    const list = await api('GET', `/api/projects/${this.pid}/exports`);
    const el = $('#exports');
    if (!el) return;
    el.innerHTML = list.length ? list.map(f => `<div class="card"><b>${esc(f.name)}</b>
      <div class="muted">${(f.size / 1048576).toFixed(1)} MB</div>
      <div class="row"><a class="btn small" href="/files/${this.pid}/${f.file}" target="_blank">▶ Watch</a>
      <a class="btn small" href="/files/${this.pid}/${f.file}" download>⬇ Download</a></div></div>`).join('')
      : '<p class="muted">Nothing rendered yet.</p>';
  }

  // ---- inspector ----------------------------------------------------------------------
  renderInspector() {
    const el = $('#inspector');
    const sel = this.selection;
    const item = sel ? this.findItem(sel.kind, sel.id) : null;
    if (!item) {
      el.innerHTML = `<h3>Tips</h3>
        <p class="hint">Click anything on the timeline or the video to change it here.</p>
        <table class="shortcuts">
          <tr><td><kbd>Space</kbd></td><td>Play / pause</td></tr>
          <tr><td><kbd>S</kbd></td><td>Cut the clip at the playhead</td></tr>
          <tr><td><kbd>Delete</kbd></td><td>Delete what's selected</td></tr>
          <tr><td><kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Y</kbd></td><td>Undo / redo</td></tr>
          <tr><td><kbd>←</kbd> <kbd>→</kbd></td><td>Step one frame (Shift: 1 second)</td></tr>
          <tr><td><kbd>+</kbd> <kbd>−</kbd></td><td>Zoom the timeline (or Ctrl + mouse wheel)</td></tr>
        </table>
        <h4>How to</h4>
        <p class="hint"><b>Cut a part out:</b> put the playhead at the start, press <kbd>S</kbd>; move to the end, press <kbd>S</kbd> again;
        click the middle piece and press <kbd>Delete</kbd>.</p>
        <p class="hint"><b>Trim:</b> drag the left or right edge of a clip.</p>
        <p class="hint"><b>See the real thing:</b> “Exact preview” renders the current frame exactly like the final video.</p>`;
      return;
    }
    const num = (k, v, step = 0.01, min = 0) => `<input type="number" step="${step}" min="${min}" data-num="${k}" value="${(+v).toFixed(2)}">`;
    let html = '';
    if (sel.kind === 'clip') {
      const p = this.project, i = p.clips.indexOf(item), m = p.media.find(x => x.id === item.media);
      const tr = item.transition || {};
      html = `<h3>🎬 Clip ${i + 1}</h3><div class="muted">${esc(m?.name)}</div>
        <div class="row"><label>Starts at</label>${num('in', item.in)}<span class="muted">s in the file</span></div>
        <div class="row"><label>Ends at</label>${num('out', item.out)}<span class="muted">of ${(+m?.duration || 0).toFixed(1)}s</span></div>
        <div class="row"><label>Length</label><b>${clipLength(item).toFixed(2)}s</b></div>
        <div class="row"><label>Volume</label><input type="range" min="0" max="2" step="0.05" data-num-live="volume" value="${item.volume ?? 1}"><span>${Math.round((item.volume ?? 1) * 100)}%</span></div>
        <h4>Transition into this clip</h4>
        ${i === 0 ? '<p class="hint">The first clip has nothing before it.</p>' : `
        <div class="row"><select id="clip-tr"><option value="">None (hard cut)</option>${Object.entries(this.meta.transitions).map(([k, v]) =>
          `<option value="${k}" ${tr.type === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></div>
        <div class="row"><label>Length</label><input type="range" min="0.2" max="2" step="0.1" id="clip-tr-len" value="${tr.duration || 0.5}"><span>${tr.duration || 0.5}s</span></div>`}
        <h4>Edit</h4>
        <div class="row"><button class="btn" data-act="split">✂ Cut at playhead</button></div>
        <div class="row"><button class="btn" data-act="left" ${i === 0 ? 'disabled' : ''}>◀ Move earlier</button>
          <button class="btn" data-act="right" ${i === p.clips.length - 1 ? 'disabled' : ''}>Move later ▶</button></div>
        <div class="row"><button class="btn danger" data-act="delete">🗑 Delete clip</button></div>`;
    } else if (sel.kind === 'caption') {
      const p = this.project;
      const person = p.people.find(x => x.id === item.person);
      const picks = this.meta.expressions.map(e => {
        const src = person ? this.avatarUrl(person, e, false) : null;
        return `<button data-expr="${e}" class="${item.expression === e ? 'active' : ''}" title="${esc(this.meta.expression_labels[e])}">
          ${src && person?.avatars?.[e] ? `<img src="${src}">` : `<b>${EMOJI[e] || ''}</b>`}${esc(this.meta.expression_labels[e])}</button>`;
      }).join('');
      html = `<h3>💬 Caption</h3>
        <div class="field"><label>Text</label><textarea id="cap-text">${esc(item.text)}</textarea></div>
        <div class="row"><label>From</label>${num('start', item.start)}</div>
        <div class="row"><label>To</label>${num('end', item.end)}</div>
        <div class="field"><label>Who is talking</label><div class="row"><select id="cap-person" style="flex:1">
          <option value="">No person</option>${p.people.map(x => `<option value="${x.id}" ${x.id === item.person ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}
          <option value="__new">+ New person…</option></select></div></div>
        <div class="field"><label>Expression (picks the avatar)</label><div class="emoji-pick">${picks}</div></div>
        <div class="row"><button class="btn" data-act="split-caption">✂ Split at playhead</button>
          <button class="btn danger" data-act="delete">🗑 Delete</button></div>`;
    } else if (sel.kind === 'effect') {
      const def = this.meta.effects[item.type] || { params: {} };
      const ranges = { amount: item.type === 'blur' ? [1, 40, 1] : item.type === 'glitch' ? [2, 60, 1] : [1.05, 3, 0.05],
        x: [0, 1, 0.01], y: [0, 1, 0.01], ramp: [0.05, 1.5, 0.05], intensity: [0.05, 1, 0.05], strength: [0.1, 1, 0.05] };
      const labels = { amount: 'Amount', x: 'Spot left-right', y: 'Spot up-down', ramp: 'Zoom speed', intensity: 'Strength', strength: 'Strength' };
      html = `<h3>✨ ${esc(def.label || item.type)}</h3><p class="hint">${esc(def.help || '')}</p>
        <div class="row"><label>From</label>${num('start', item.start)}</div>
        <div class="row"><label>To</label>${num('end', item.end)}</div>
        <div class="row"><label>Lasts</label><input type="range" min="0.1" max="10" step="0.1" data-duration value="${(item.end - item.start).toFixed(1)}"><span>${(item.end - item.start).toFixed(1)}s</span></div>
        ${Object.keys(def.params).map(k => { const [a, b, s] = ranges[k] || [0, 1, 0.01];
          return `<div class="row"><label>${labels[k] || k}</label><input type="range" min="${a}" max="${b}" step="${s}" data-param="${k}" value="${item.params[k]}"><span>${(+item.params[k]).toFixed(2)}</span></div>`; }).join('')}
        <div class="row"><button class="btn danger" data-act="delete">🗑 Delete</button></div>`;
    } else if (sel.kind === 'overlay') {
      html = item.type === 'image' ? `<h3>🖼 Picture</h3>
        <img src="/files/${this.pid}/${item.src}" style="max-width:100%;max-height:120px;display:block;margin:6px auto">
        <div class="row"><label>Size</label><input type="range" min="0.03" max="1" step="0.01" data-num-live="w" value="${item.w}"><span>${Math.round(item.w * 100)}%</span></div>`
        : `<h3>T Text</h3>
        <div class="field"><textarea id="ov-text">${esc(item.text)}</textarea></div>
        <div class="row"><label>Font</label><input type="text" id="ov-font" value="${esc(item.font || 'Arial')}" style="flex:1"></div>
        <div class="row"><label>Size</label><input type="range" min="20" max="220" data-num-live="size" value="${item.size}"><span>${item.size}</span></div>
        <div class="row"><label>Colors</label><input type="color" data-color="color" value="${item.color}"><input type="color" data-color="outline" value="${item.outline}"></div>
        <div class="row"><label>Outline</label><input type="range" min="0" max="20" data-num-live="outline_width" value="${item.outline_width ?? 5}"><span>${item.outline_width ?? 5}</span></div>`;
      html += `<div class="row"><label>From</label>${num('start', item.start)}</div>
        <div class="row"><label>To</label>${num('end', item.end)}</div>
        <div class="row"><button class="btn danger" data-act="delete">🗑 Delete</button></div>`;
    }
    el.innerHTML = html;
    this.bindInspector(el, item);
    if (this.focus === 'transition') $('#clip-tr', el)?.focus();
  }

  bindInspector(el, item) {
    $$('[data-num]', el).forEach(i => i.onchange = () => this.change(pp => {
      const k = i.dataset.num;
      let v = +i.value;
      if (this.selection.kind === 'clip') {
        const m = pp.media.find(x => x.id === item.media);
        if (k === 'in') v = clamp(v, 0, item.out - 0.2);
        if (k === 'out') v = clamp(v, item.in + 0.2, m.duration);
      } else {
        if (k === 'start') v = clamp(v, 0, item.end - 0.05);
        if (k === 'end') v = Math.max(item.start + 0.05, v);
        if (item.words) item.words = null;
      }
      item[k] = v;
    }));
    $$('[data-num-live]', el).forEach(i => i.addEventListener('input', () => {
      const k = i.dataset.numLive;
      this.liveStyle(() => { item[k] = +i.value; }, i);
      i.nextElementSibling.textContent = k === 'volume' || k === 'w' ? Math.round(i.value * 100) + '%' : i.value;
    }));
    $$('[data-color]', el).forEach(i => i.addEventListener('input', () => this.liveStyle(() => { item[i.dataset.color] = i.value; }, i)));
    $$('[data-param]', el).forEach(i => i.addEventListener('input', () => {
      this.liveStyle(() => { item.params[i.dataset.param] = +i.value; }, i);
      i.nextElementSibling.textContent = (+i.value).toFixed(2);
    }));
    const dur = $('[data-duration]', el);
    if (dur) dur.addEventListener('input', () => {
      this.liveStyle(() => { item.end = item.start + +dur.value; }, dur);
      dur.nextElementSibling.textContent = (+dur.value).toFixed(1) + 's';
      this.timeline.render();
    });
    const tr = $('#clip-tr', el);
    if (tr) tr.onchange = () => this.setTransition(item.id, tr.value, +$('#clip-tr-len', el).value);
    const trl = $('#clip-tr-len', el);
    if (trl) trl.addEventListener('input', () => {
      trl.nextElementSibling.textContent = trl.value + 's';
      if (item.transition) this.liveStyle(() => { item.transition.duration = +trl.value; this.timeline.render(); }, trl);
    });
    const ct = $('#cap-text', el);
    if (ct) ct.onchange = () => this.change(() => { item.text = ct.value.trim(); });
    const cp = $('#cap-person', el);
    if (cp) cp.onchange = () => {
      if (cp.value === '__new') { const pid = this.addPerson(); this.change(() => { item.person = pid; }); return; }
      this.change(() => { item.person = cp.value || null; });
    };
    $$('[data-expr]', el).forEach(b => b.onclick = () => this.change(() => { item.expression = b.dataset.expr; }));
    const ot = $('#ov-text', el);
    if (ot) ot.onchange = () => this.change(() => { item.text = ot.value; });
    const of = $('#ov-font', el);
    if (of) of.onchange = () => this.change(() => { item.font = of.value; });
    $$('[data-act]', el).forEach(b => b.onclick = () => {
      const act = b.dataset.act;
      if (act === 'delete') this.deleteSelected();
      if (act === 'split') this.split();
      if (act === 'split-caption') this.splitCaption(item);
      if (act === 'left' || act === 'right') this.moveClip(item.id, act === 'left' ? -1 : 1);
    });
  }

  // ---- editing actions ----------------------------------------------------------------------
  addClip(mediaId, atTime) {
    const p = this.project;
    const m = p.media.find(x => x.id === mediaId);
    if (!m) return;
    const clip = { id: uid('c'), media: m.id, in: 0, out: +m.duration, volume: 1, transition: null };
    this.change(pp => {
      let idx = pp.clips.length;
      if (atTime != null && pp.clips.length) {
        const starts = clipStarts(pp.clips, this.meta.transitions);
        idx = starts.findIndex((s, i) => atTime < s + clipLength(pp.clips[i]) / 2);
        if (idx < 0) idx = pp.clips.length;
      }
      pp.clips.splice(idx, 0, clip);
    });
    this.select({ kind: 'clip', id: clip.id });
    this.timeline.fit();
  }

  moveClip(id, dir) {
    this.change(pp => {
      const i = pp.clips.findIndex(c => c.id === id), j = i + dir;
      if (j < 0 || j >= pp.clips.length) return;
      [pp.clips[i], pp.clips[j]] = [pp.clips[j], pp.clips[i]];
    });
  }

  split() {
    const p = this.project, t = this.time;
    const act = activeClips(p, t, this.meta.transitions).filter(a => !a.transition);
    const a = act[act.length - 1];
    if (!a || t - a.start < 0.1 || a.end - t < 0.1) return this.toast('Move the playhead inside a clip to cut it.');
    this.change(pp => {
      const c = pp.clips[a.index];
      const cut = +c.in + (t - a.start);
      const second = { ...c, id: uid('c'), in: cut, transition: null };
      c.out = cut;
      pp.clips.splice(a.index + 1, 0, second);
    });
    this.toast('Cut! Click a piece and press Delete to remove it.');
  }

  splitCaption(cap) {
    const t = this.time;
    if (t <= cap.start + 0.05 || t >= cap.end - 0.05) return this.toast('Move the playhead inside the caption first.');
    this.change(pp => {
      const words = cap.text.split(/\s+/);
      const frac = (t - cap.start) / (cap.end - cap.start);
      const n = clamp(Math.round(words.length * frac), 1, words.length - 1);
      const second = { ...cap, id: uid('k'), start: t, text: words.slice(n).join(' '), words: null };
      cap.end = t; cap.text = words.slice(0, n).join(' '); cap.words = null;
      pp.captions.push(second);
    });
  }

  deleteSelected() {
    const sel = this.selection;
    if (!sel) return;
    this.change(pp => {
      if (sel.kind === 'clip') {
        const i = pp.clips.findIndex(c => c.id === sel.id);
        if (i < 0) return;
        const starts = clipStarts(pp.clips, this.meta.transitions);
        const a = starts[i], b = a + clipLength(pp.clips[i]);
        pp.clips.splice(i, 1);
        if (pp.clips[i] && i === 0) pp.clips[0].transition = null;
        rippleDelete(pp, a, b);
      } else {
        const key = { caption: 'captions', effect: 'effects', overlay: 'overlays' }[sel.kind];
        pp[key] = pp[key].filter(x => x.id !== sel.id);
      }
    });
    this.select(null);
  }

  setTransition(clipId, type, duration) {
    const p = this.project;
    const i = p.clips.findIndex(c => c.id === clipId);
    if (i <= 0) return this.toast('Transitions go between two clips - drop it on the second clip.');
    this.change(() => { p.clips[i].transition = type ? { type, duration: duration || this.trLen || 0.5 } : null; });
    this.select({ kind: 'clip', id: clipId });
  }

  addEffect(type, t, x, y) {
    const def = this.meta.effects[type];
    const dur = { flash: 0.4, shake: 1, zoom_in: 1.5, glitch: 0.6 }[type] || 2;
    const e = { id: uid('e'), type, start: +t.toFixed(2), end: +(t + dur).toFixed(2), params: { ...def.params } };
    if (x != null && 'x' in e.params) { e.params.x = +x.toFixed(3); e.params.y = +y.toFixed(3); }
    this.change(pp => pp.effects.push(e));
    this.select({ kind: 'effect', id: e.id });
  }

  addText(t, x = 0.5, y = 0.2) {
    const o = { id: uid('o'), type: 'text', text: 'YOUR TEXT', x, y, size: 64, color: '#FFFFFF', outline: '#000000',
      outline_width: 6, font: 'Arial', start: +t.toFixed(2), end: +(t + 3).toFixed(2) };
    this.change(pp => pp.overlays.push(o));
    this.select({ kind: 'overlay', id: o.id });
  }

  addCaption() {
    const p = this.project;
    const k = { id: uid('k'), start: +this.time.toFixed(2), end: +(this.time + 2).toFixed(2), text: 'New caption',
      person: p.people[0]?.id || null, expression: 'neutral', words: null };
    this.change(pp => pp.captions.push(k));
    this.select({ kind: 'caption', id: k.id });
  }

  addPerson() {
    const p = this.project;
    const n = p.people.length;
    const person = { id: uid('p'), name: `Person ${n + 1}`, color: this.meta.person_colors[n % this.meta.person_colors.length], avatars: {} };
    this.change(pp => pp.people.push(person));
    return person.id;
  }

  setPreset(preset) {
    this.change(pp => { pp.preset = preset; });
    $('#preset').value = preset;
    this.preview.layout();
  }

  // ---- files ---------------------------------------------------------------------------------
  pickFiles(accept, cb, multiple = true) {
    const inp = $('#file-pick');
    inp.accept = accept;
    inp.multiple = multiple;
    inp.value = '';
    inp.onchange = () => cb([...inp.files]);
    inp.click();
  }

  async dropFiles(files, { time, x, y }) {
    for (const f of files) {
      if (VIDEO_EXT.test(f.name)) {
        const t = this.toast(`Uploading ${f.name}…`, false, 0);
        try {
          const entry = await upload(`/api/projects/${this.pid}/media?name=${encodeURIComponent(f.name)}`, f,
            (fr) => { t.textContent = `Uploading ${f.name}… ${Math.round(fr * 100)}%`; });
          t.remove();
          this.mediaAdded(entry, true, time);
        } catch (e) { t.remove(); this.toast(`${f.name}: ${e.message}`, true); }
      } else if (IMAGE_EXT.test(f.name)) {
        try {
          const r = await upload(`/api/projects/${this.pid}/image?name=${encodeURIComponent(f.name)}`, f);
          const o = { id: uid('o'), type: 'image', src: r.path, x: x ?? 0.5, y: y ?? 0.3, w: 0.3,
            start: +(time ?? this.time).toFixed(2), end: +((time ?? this.time) + 3).toFixed(2) };
          this.change(pp => pp.overlays.push(o));
          this.select({ kind: 'overlay', id: o.id });
        } catch (e) { this.toast(`${f.name}: ${e.message}`, true); }
      } else {
        this.toast(`${f.name}: not a video or picture`, true);
      }
    }
  }

  mediaAdded(entry, addToTimeline, atTime) {
    this.project.media.push(entry);
    if (entry.proxy_job) this.pollJob(entry.proxy_job, null, (job) => {
      const m = this.project.media.find(x => x.id === entry.id);
      if (m && job.result) { m.proxy = job.result.proxy; this.dirty(); this.renderAll(); }
    });
    if (addToTimeline) this.addClip(entry.id, atTime);
    else this.renderAll();
    this.toast(`Added ${entry.name}`);
  }

  dropItem(item, { time, clipId, x, y }) {
    if (item.kind === 'effect') this.addEffect(item.type, time, x, y);
    else if (item.kind === 'transition') {
      if (!clipId) return this.toast('Drop transitions onto a clip in the Video row.');
      this.setTransition(clipId, item.type);
    } else if (item.kind === 'media') this.addClip(item.id, time);
    else if (item.kind === 'text') this.addText(time, x, y);
  }

  async uploadAvatar(personId, expr, blob, quiet = false) {
    const name = `${expr}.png`;
    const r = await upload(`/api/projects/${this.pid}/image?folder=avatars/${personId}&name=${name}`, blob);
    const person = this.project.people.find(x => x.id === personId);
    if (!quiet) this.beginChange();
    person.avatars = { ...(person.avatars || {}), [expr]: r.path };
    this.version = r.v;
    if (!quiet) this.endChange(true);
    return r.path;
  }

  async makeAvatarSet(personId, photo) {
    const person = this.project.people.find(x => x.id === personId);
    const t = this.toast(`Making ${person.name}'s avatars…`, false, 0);
    this.beginChange();
    try {
      for (const e of this.meta.expressions) {
        for (const talk of [false, true]) {
          const cv = photo ? drawPhotoAvatar(photo, e, talk, person.color) : drawCartoon(e, talk);
          await this.uploadAvatar(personId, talk ? `${e}_talk` : e, await canvasToBlob(cv), true);
        }
      }
    } finally {
      t.remove();
      this.endChange(true);
    }
    this.toast(`${person.name} has a full set of avatars. Talking versions flip while they speak.`);
  }

  // ---- jobs ----------------------------------------------------------------------------------
  async pollJob(id, onUpdate, onDone) {
    for (;;) {
      await new Promise(r => setTimeout(r, 700));
      let job;
      try { job = await api('GET', `/api/jobs/${id}`); } catch (e) { return; }
      onUpdate && onUpdate(job);
      if (job.status !== 'running') { onDone && onDone(job); return job; }
    }
  }

  async transcribe() {
    await this.flushSave();
    const opts = { speakers: $('#tr-speakers').checked, expressions: $('#tr-expr').checked, replace: $('#tr-replace').checked,
      num_speakers: +$('#tr-num').value || null };
    const job = await api('POST', `/api/projects/${this.pid}/transcribe`, opts);
    this.modal(`<h3>Creating captions…</h3><div class="spinner"></div><p id="job-msg" class="hint" style="text-align:center">${esc(job.message)}</p>`);
    const done = await this.pollJob(job.id, (j) => { const m = $('#job-msg'); if (m) m.textContent = j.message; });
    this.closeModal();
    if (done.status === 'done') {
      this.beginChange();
      this.project = await api('GET', `/api/projects/${this.pid}`);
      this.endChange(true);
      this.toast(done.message + (done.result.people > 1 ? ` Found ${done.result.people} people - check People to name them.` : ''));
    } else this.toast(done.error || 'Captions failed', true);
  }

  async render() {
    await this.flushSave();
    if (!this.project.clips.length) return this.toast('Add a clip to the timeline first.', true);
    const job = await api('POST', `/api/projects/${this.pid}/render`, { quality: this.quality || 'high' });
    const bar = $('#render-progress');
    bar.classList.remove('hidden');
    $('#btn-render').disabled = true;
    $('#btn-cancel-render').onclick = () => api('POST', `/api/jobs/${job.id}/cancel`);
    const done = await this.pollJob(job.id, (j) => {
      $('.bar-fill', bar).style.width = (j.progress * 100) + '%';
      $('span', bar).textContent = j.message;
    });
    bar.classList.add('hidden');
    $('#btn-render').disabled = false;
    if (done.status === 'done') {
      this.modal(`<h3>✅ Your video is ready</h3><p>${esc(done.result.name)}</p>
        <video src="/files/${this.pid}/${done.result.file}" controls style="max-height:60vh;max-width:100%;display:block;margin:auto"></video>
        <div class="row"><a class="btn primary" href="/files/${this.pid}/${done.result.file}" download>⬇ Download</a>
        <button class="btn" id="open-exports">📂 Open folder</button><div class="spacer"></div><button class="btn" data-close>Close</button></div>
        <p class="hint">Saved in ${esc(done.result.folder)}</p>`);
      $('#open-exports').onclick = () => api('POST', `/api/projects/${this.pid}/open-folder`);
      if (this.tab === 'export') this.loadExports();
    } else if (done.status !== 'cancelled') this.toast(done.error || 'Render failed', true);
  }

  async exactPreview() {
    await this.flushSave();
    if (!this.project.clips.length) return;
    const t = this.time;
    this.modal(`<h3>Exact preview at ${fmtTime(t)}</h3><div class="spinner"></div><p class="hint">Rendering this frame exactly like the final video…</p>`);
    try {
      const res = await fetch(`/api/projects/${this.pid}/frame?t=${t.toFixed(3)}&_=${Date.now()}`);
      if (!res.ok) throw new Error((await res.json()).error);
      const url = URL.createObjectURL(await res.blob());
      this.modal(`<h3>Exact preview at ${fmtTime(t)}</h3><img class="frame" src="${url}">
        <div class="row"><span class="hint">This is exactly how the rendered video will look.</span><div class="spacer"></div><button class="btn" data-close>Close</button></div>`);
    } catch (e) {
      this.modal(`<h3>Preview failed</h3><pre style="white-space:pre-wrap">${esc(e.message)}</pre><button class="btn" data-close>Close</button>`);
    }
  }

  // ---- chrome: modal, toasts, keyboard, preview drag --------------------------------------------
  modal(html) {
    const m = $('#modal');
    $('.modal-body', m).innerHTML = html;
    m.classList.remove('hidden');
    $$('[data-close]', m).forEach(b => b.onclick = () => this.closeModal());
  }

  closeModal() { $('#modal').classList.add('hidden'); }

  toast(msg, error = false, ms = 3500) {
    const t = document.createElement('div');
    t.className = 'toast' + (error ? ' error' : '');
    t.textContent = msg;
    $('#toasts').appendChild(t);
    if (ms) setTimeout(() => t.remove(), error ? 7000 : ms);
    return t;
  }

  bindChrome() {
    $$('#tabs button').forEach(b => b.onclick = () => this.setTab(b.dataset.tab));
    $('#btn-projects').onclick = async () => this.showProjects(await api('GET', '/api/projects'));
    $('#project-name').onchange = (e) => this.change(pp => { pp.name = e.target.value || 'Untitled'; });
    $('#preset').onchange = (e) => this.setPreset(e.target.value);
    $('#btn-undo').onclick = () => this.undo();
    $('#btn-redo').onclick = () => this.redo();
    $('#btn-render').onclick = () => this.render();
    $('#btn-exact').onclick = () => this.exactPreview();
    $('#btn-play').onclick = () => this.preview.toggle();
    $('#btn-split').onclick = () => this.split();
    $('#btn-delete').onclick = () => this.deleteSelected();
    $('#snap').onchange = (e) => { this.snapOn = e.target.checked; };
    $('#zoom').oninput = (e) => this.timeline.setZoom(+e.target.value, this.time);
    $('#btn-zoom-in').onclick = () => this.timeline.setZoom(this.timeline.pxps * 1.4, this.time);
    $('#btn-zoom-out').onclick = () => this.timeline.setZoom(this.timeline.pxps / 1.4, this.time);
    $('#btn-fit').onclick = () => this.timeline.fit();
    window.addEventListener('beforeunload', () => this.flushSave());

    document.addEventListener('keydown', (e) => {
      if (e.target.closest('input, textarea, select') || !this.project) return;
      const k = e.key;
      if (k === ' ') { e.preventDefault(); this.preview.toggle(); }
      else if (k === 's' || k === 'S') { if (!e.ctrlKey && !e.metaKey) this.split(); else { e.preventDefault(); this.flushSave(); } }
      else if (k === 'Delete' || k === 'Backspace') { e.preventDefault(); this.deleteSelected(); }
      else if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? this.redo() : this.undo(); }
      else if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 'y') { e.preventDefault(); this.redo(); }
      else if (k === 'ArrowLeft' || k === 'ArrowRight') { e.preventDefault(); this.seek(this.time + (k === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 1 : 1 / 30)); }
      else if (k === '+' || k === '=') this.timeline.setZoom(this.timeline.pxps * 1.3, this.time);
      else if (k === '-') this.timeline.setZoom(this.timeline.pxps / 1.3, this.time);
      else if (k === 'Escape') this.select(null);
    });

    // drag things around on the video
    const box = $('.frame-box');
    const layer = $('.overlay-layer');
    const norm = (ev) => { const r = box.getBoundingClientRect(); return [clamp((ev.clientX - r.left) / r.width, 0, 1), clamp((ev.clientY - r.top) / r.height, 0, 1)]; };
    layer.addEventListener('pointerdown', (e) => {
      const el = e.target.closest('[data-kind]');
      if (!el) return;
      e.preventDefault();
      const kind = el.dataset.kind, id = el.dataset.id, p = this.project;
      const resize = e.target.dataset.resize;
      let target, keyX = 'x', keyY = 'y';
      if (kind === 'caption-pos') { target = p.caption_style; if (id) this.selection = { kind: 'caption', id }; }
      else if (kind === 'overlay') { target = this.findItem('overlay', id); this.selection = { kind: 'overlay', id }; }
      else if (kind === 'zoom-target') { target = this.findItem('effect', id).params; }
      if (!target) return;
      this.renderInspector();
      this.timeline.render();
      const [x0, y0] = norm(e), sx = target[keyX], sy = target[keyY];
      const s0 = { w: target.w, size: target.size };
      this.beginChange();
      let moved = false;
      const move = (ev) => {
        const [x, y] = norm(ev);
        moved = true;
        if (resize) {
          if (target.type === 'image') target.w = clamp(s0.w + (x - x0) * 2, 0.03, 1.2);
          else target.size = clamp(Math.round(s0.size * (1 + (x - x0) * 3)), 12, 300);
        } else {
          target[keyX] = +clamp(sx + x - x0, 0, 1).toFixed(4);
          target[keyY] = +clamp(sy + y - y0, 0, 1).toFixed(4);
        }
        this.preview.lastHtml = null;
        this.preview.draw();
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        this.endChange(moved);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    });
    // drops onto the video
    const stage = $('#stage');
    stage.addEventListener('dragover', (e) => { e.preventDefault(); box.classList.add('drop-hover'); });
    stage.addEventListener('dragleave', () => box.classList.remove('drop-hover'));
    stage.addEventListener('drop', (e) => {
      e.preventDefault();
      box.classList.remove('drop-hover');
      const [x, y] = norm(e);
      if (e.dataTransfer.files.length) return this.dropFiles(e.dataTransfer.files, { x, y });
      const raw = e.dataTransfer.getData('application/x-cliptool');
      if (raw) this.dropItem(JSON.parse(raw), { time: this.time, x, y });
    });
    // stop the browser opening files dropped anywhere else
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => e.preventDefault());
  }
}

const app = new App();
window.clipApp = app;
app.init().catch((e) => {
  document.body.innerHTML = `<div style="padding:40px"><h2>ClipTool Editor could not start</h2><pre>${esc(e.stack || e)}</pre></div>`;
});
