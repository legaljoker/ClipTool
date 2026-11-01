// Auto Edit tab (one-click editing, sound effects, music, video check, hook versions)
// and the AI Editor tab (chat with Claude, which edits the project).
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
import { totalDuration } from './model.js';
const fmt = (t) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`;

async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// Run a server job with a progress window. Returns the finished job.
export async function runJob(app, url, body, title) {
  await app.flushSave();
  const job = await api('POST', url, body);
  app.modal(`<h3>${esc(title)}</h3><div class="spinner"></div>
    <div class="job-bar"><div id="job-fill" style="width:0%"></div></div>
    <p id="job-msg" class="hint" style="text-align:center">${esc(job.message)}</p>
    <div class="row"><div class="spacer"></div><button class="btn" id="job-cancel">Cancel</button></div>`);
  $('#job-cancel').onclick = () => api('POST', `/api/jobs/${job.id}/cancel`);
  const done = await app.pollJob(job.id, (j) => {
    const m = $('#job-msg'); if (m) m.textContent = j.message;
    const f = $('#job-fill'); if (f) f.style.width = `${Math.round((j.progress || 0) * 100)}%`;
  });
  app.closeModal();
  return done;
}

// Reload the project after the server changed it; the whole change is one undo step.
export async function reloadAsOneStep(app) {
  app.beginChange();
  app.project = await api('GET', `/api/projects/${app.pid}`);
  app.endChange(true);
}

// ---------------------------------------------------------------------------------------------
export function panelAuto(app) {
  const p = app.project, m = app.meta, s = app.settings || {};
  const ai = m.ai, eleven = m.eleven;
  const ck = (id, label, on, hint = '') => `<label class="check" title="${esc(hint)}"><input type="checkbox" id="ae-${id}" ${on ? 'checked' : ''}> ${label}</label>`;
  const music = p.media.filter(x => x.kind === 'audio' && !x.sfx_source);
  const sfx = Object.entries(m.sfx || {}).map(([k, v]) => `<div class="sfx-btn" title="${esc(v.help)}">
      <button class="btn small icon" data-sfx-play="${k}" title="Listen">▶</button>
      <button class="btn small" data-sfx-add="${k}" title="Add at the playhead">${esc(v.label)}</button></div>`).join('');
  return `<h3>🪄 Auto Edit</h3>
    <p class="hint">Edits the whole video for you in one go. Everything it adds stays editable, and one
      <b>Undo</b> takes it all back.${ai ? ' Claude plans the title, zooms and call-outs.' : ' Add a Claude API key in ⚙️ Settings to let AI plan the edit.'}</p>
    <div class="ae-group"><h4>Clean up</h4>
      ${ck('tighten', 'Cut pauses and dead air', true, 'Keeps a short natural breath at every cut')}
      ${ck('fillers', 'Remove “um” and “uh”', s.remove_fillers ?? true)}
      ${ck('restarts', 'Remove repeated words and false starts', s.remove_restarts ?? true)}
      ${ck('captions', 'Add captions if there are none', true)}
      ${ck('speakers', 'Work out who is talking (several people)', false)}
      ${ck('bleep', 'Bleep swear words', s.bleep_swears ?? false)}</div>
    <div class="ae-group"><h4>Make it engaging</h4>
      ${ck('punch_ins', 'Zoom in / out at cuts (hides jump cuts)', s.punch_ins ?? true)}
      ${ck('emphasis', 'Zoom punches on strong words', true)}
      ${ck('hook_title', 'Hook title at the start', true)}
      <input type="text" id="ae-hook_text" placeholder="Title text (empty = automatic)" style="width:100%;margin:2px 0 6px">
      ${ck('callouts', 'Call-out text (“FOLLOW FOR MORE”…)', true)}
      ${ck('cold_open', 'Cold open: repeat the best moment first', false, 'A 2-5 second teaser before the video starts')}
      ${ck('sfx', 'Sound effects on titles, zooms and call-outs', true)}
      <div class="field"><label>Background music (dips while people talk)</label>
        <select id="ae-music"><option value="">None</option>${music.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select></div>
      ${ai ? ck('use_ai', 'Let Claude plan the edit', true) : ''}</div>
    <button class="btn primary big" id="ae-run">🪄 Run Auto Edit</button>
    ${p.last_auto_report ? `<details class="ae-report"><summary>Last Auto Edit</summary>${p.last_auto_report.map(l => `<div>${esc(l)}</div>`).join('')}</details>` : ''}

    <h3 style="margin-top:18px">🔊 Sound effects</h3>
    <p class="hint">Click a name to put it at the playhead (${fmt(app.time)}). ▶ to listen.</p>
    <div class="sfx-grid">${sfx}</div>
    <div class="ae-group"><h4>Make a new sound or song ${eleven ? '' : '<span class="muted">(needs an ElevenLabs key in ⚙️ Settings)</span>'}</h4>
      <div class="row"><select id="gen-kind"><option value="sfx">Sound effect</option><option value="music">Background music</option></select>
        <input type="number" id="gen-secs" value="2" min="0.5" max="300" step="0.5" style="width:70px"> s</div>
      <textarea id="gen-prompt" rows="2" style="width:100%" placeholder="e.g. cartoon slide whistle going up  /  upbeat lo-fi hip hop, no vocals"></textarea>
      <button class="btn" id="gen-go" ${eleven ? '' : 'disabled'}>✨ Generate</button></div>

    <h3 style="margin-top:18px">🩺 Check the video</h3>
    <p class="hint">Finds black or frozen frames, flashes, long silences, clipping or too-quiet sound, and captions that
      don't match the speech, before you upload.</p>
    <button class="btn" id="qa-run">🩺 Check for problems</button>
    <div id="qa-out">${p.last_qa ? qaHtml(p.last_qa) : ''}</div>

    <h3 style="margin-top:18px">🎣 Hook versions</h3>
    <p class="hint">Test which opening works best: renders one video per hook, each followed by the same main part.
      Mark hooks first with Text &amp; Pics or let ClipTool pick the strongest lines.</p>
    <div class="row"><label>How many</label><input type="number" id="hv-n" value="3" min="2" max="6" style="width:60px">
      <label>Hook length</label><input type="number" id="hv-len" value="3" min="1.5" max="8" step="0.5" style="width:60px"> s</div>
    <button class="btn" id="hv-run">🎣 Make hook versions</button>`;
}

function qaHtml(r) {
  if (!r || !r.issues) return '';
  const icon = { error: '⛔', warning: '⚠️', info: 'ℹ️' };
  const rows = r.issues.map(i => `<div class="qa-row ${i.level}">${icon[i.level] || '•'}
      ${i.time != null ? `<a href="#" data-seek="${i.time}">${fmt(i.time)}</a>` : ''} ${esc(i.message)}</div>`).join('');
  const skipped = (r.skipped || []).map(x => `<div class="qa-row info">⏭ ${esc(x)}</div>`).join('');
  return `<div class="qa-box"><b>${r.issues.length ? `${r.issues.length} thing${r.issues.length > 1 ? 's' : ''} to look at` : '✅ No problems found'}</b>
    <span class="muted"> · checked ${esc(r.checked || '')}</span>${rows}${skipped}</div>`;
}

export function bindAuto(app, el) {
  $('#ae-run', el).onclick = async () => {
    const opts = {};
    $$('[id^="ae-"]', el).forEach(i => {
      const k = i.id.slice(3);
      if (k === 'run') return;
      opts[k] = i.type === 'checkbox' ? i.checked : i.value;
    });
    const done = await runJob(app, `/api/projects/${app.pid}/autoedit`, opts, '🪄 Auto Edit');
    if (done?.status !== 'done') return app.toast(done?.error || 'Auto Edit stopped.', true);
    await reloadAsOneStep(app);
    app.modal(`<h3>🪄 Auto Edit finished</h3><div class="ae-report">${done.result.report.map(l => `<div>${esc(l)}</div>`).join('')}</div>
      <p class="hint">Play it back. Everything can be changed on the timeline, or undone in one step.</p>
      <div class="row"><button class="btn" id="ae-undo">↶ Undo Auto Edit</button><div class="spacer"></div>
      <button class="btn primary" data-close>Looks good</button></div>`);
    $('#ae-undo').onclick = () => { app.undo(); app.closeModal(); };
    app.seek(0);
  };
  $$('[data-sfx-play]', el).forEach(b => b.onclick = () => { new Audio(`/api/sfx/${b.dataset.sfxPlay}.wav`).play(); });
  $$('[data-sfx-add]', el).forEach(b => b.onclick = async () => {
    await app.flushSave();
    await api('POST', `/api/projects/${app.pid}/sfx`, { name: b.dataset.sfxAdd, t: app.time, volume: 0.6 });
    await reloadAsOneStep(app);
    app.toast('Sound added to the audio track.');
  });
  $('#gen-kind', el).onchange = (e) => { $('#gen-secs', el).value = e.target.value === 'music' ? 30 : 2; };
  $('#gen-go', el).onclick = async () => {
    const kind = $('#gen-kind', el).value;
    const done = await runJob(app, `/api/projects/${app.pid}/generate-sound`,
      { kind, prompt: $('#gen-prompt', el).value, seconds: +$('#gen-secs', el).value }, kind === 'music' ? '🎵 Making music' : '✨ Making a sound');
    if (done?.status !== 'done') return app.toast(done?.error || 'Stopped.', true);
    await reloadAsOneStep(app);
    const mid = done.result.media.id;
    if (kind === 'music') {
      app.change(pp => {
        const m = pp.media.find(x => x.id === mid);
        const len = Math.min(+m.duration, totalDuration(pp, app.meta.transitions) || +m.duration);
        pp.audio.push({ id: 'a' + Math.random().toString(36).slice(2, 10), media: mid, name: m.name, in: 0, out: len, start: 0, end: len,
          volume: (app.settings || {}).music_volume || 0.15, fade_in: 0.5, fade_out: 1.5, duck: true });
      });
      app.toast('Music added under the whole video. It dips while people talk.');
    } else {
      app.addAudioItem(mid, app.time);
      app.toast('Sound added at the playhead.');
    }
  };
  $('#qa-run', el).onclick = async () => {
    const done = await runJob(app, `/api/projects/${app.pid}/qa`, {}, '🩺 Checking the video');
    if (done?.status !== 'done') return app.toast(done?.error || 'Check stopped.', true);
    app.project.last_qa = done.result;
    app.renderPanel();
  };
  $('#hv-run', el).onclick = async () => {
    const done = await runJob(app, `/api/projects/${app.pid}/hook-versions`,
      { count: +$('#hv-n', el).value, length: +$('#hv-len', el).value }, '🎣 Rendering hook versions');
    if (done?.status !== 'done') return app.toast(done?.error || 'Stopped.', true);
    app.modal(`<h3>🎣 ${done.result.files.length} hook versions</h3>
      ${done.result.files.map(f => `<div class="qa-row"><b>${esc(f.name)}</b><br><span class="muted">${esc(f.hook)}</span></div>`).join('')}
      <div class="row"><button class="btn" id="hv-open">📂 Open folder</button><div class="spacer"></div><button class="btn primary" data-close>Close</button></div>`);
    $('#hv-open').onclick = () => api('POST', `/api/projects/${app.pid}/open-folder`);
  };
  $$('[data-seek]', el).forEach(a => a.onclick = (e) => { e.preventDefault(); app.seek(+a.dataset.seek); });
}

// ---------------------------------------------------------------------------------------------
export function panelAI(app) {
  const chat = app.aiChat || [];
  const msgs = chat.map(m => `<div class="msg ${m.role}">${m.role === 'assistant' ? '🤖 ' : ''}${esc(m.text).replace(/\n/g, '<br>')}
      ${m.actions?.length ? `<div class="acts">${m.actions.map(a => `<div>✔ ${esc(a)}</div>`).join('')}</div>` : ''}</div>`).join('');
  return `<h3>🤖 AI Editor</h3>
    ${app.meta.ai ? '' : '<p class="warn">Add a Claude API key in ⚙️ Settings to use the AI Editor.</p>'}
    <p class="hint">Tell Claude what you want, like an editor sitting next to you. It edits the timeline itself:
      cutting, captions, zooms, titles, text, sound effects, music, frame style and more. Every answer is one Undo step.</p>
    <div class="chat" id="ai-chat">${msgs || `<div class="muted">Try:<br>• “Tighten this up and add punchy captions”<br>
      • “Make a hook title and zoom in on the funny parts”<br>• “Cut the part where I talk about the weather”<br>
      • “Make it feel like a MrBeast short”<br>• “Check the video and fix what's wrong”</div>`}</div>
    <textarea id="ai-input" rows="3" style="width:100%" placeholder="What should Claude do?" ${app.meta.ai ? '' : 'disabled'}></textarea>
    <div class="row"><button class="btn primary" id="ai-send" ${app.meta.ai ? '' : 'disabled'}>Send</button>
      <div class="spacer"></div><button class="btn small" id="ai-clear">New chat</button></div>`;
}

export function bindAI(app, el) {
  const box = $('#ai-chat', el);
  box.scrollTop = box.scrollHeight;
  const send = async () => {
    const text = $('#ai-input', el).value.trim();
    if (!text) return;
    app.aiChat = [...(app.aiChat || []), { role: 'user', text }];
    app.renderPanel();
    const done = await runJob(app, `/api/projects/${app.pid}/agent`, { message: text, time: app.time }, '🤖 Claude is editing');
    if (done?.status !== 'done') {
      app.aiChat.push({ role: 'assistant', text: `Something went wrong: ${done?.error || 'stopped'}` });
      return app.renderPanel();
    }
    if (done.result.changed) await reloadAsOneStep(app);
    app.aiChat.push({ role: 'assistant', text: done.result.reply, actions: done.result.actions });
    app.renderPanel();
  };
  $('#ai-send', el).onclick = send;
  $('#ai-input', el).onkeydown = (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send(); };
  $('#ai-clear', el).onclick = async () => {
    app.aiChat = [];
    await api('POST', `/api/projects/${app.pid}/agent/reset`, {});
    app.renderPanel();
  };
}
