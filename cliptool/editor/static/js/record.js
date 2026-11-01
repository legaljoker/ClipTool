// Voice and webcam recording (MediaRecorder). Recordings are uploaded to the project
// and land on the timeline like any other media.

export const MAX_SECONDS = 300;

function pickMime(candidates) {
  return candidates.find(t => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || '';
}

export async function listDevices(kind) {
  // Device names are only visible after the user has allowed access once.
  let devices = await navigator.mediaDevices.enumerateDevices();
  if (!devices.some(d => d.kind === kind && d.label)) {
    try {
      const s = await navigator.mediaDevices.getUserMedia(kind === 'videoinput' ? { video: true } : { audio: true });
      s.getTracks().forEach(t => t.stop());
      devices = await navigator.mediaDevices.enumerateDevices();
    } catch (e) { /* permission refused: list stays unnamed */ }
  }
  return devices.filter(d => d.kind === kind).map((d, i) => ({ id: d.deviceId, label: d.label || `${kind === 'videoinput' ? 'Camera' : 'Microphone'} ${i + 1}` }));
}

export class Recording {
  constructor({ video = false, micId = null, camId = null, onTick = null, onLevel = null, onAutoStop = null }) {
    this.video = video; this.micId = micId; this.camId = camId;
    this.onTick = onTick; this.onLevel = onLevel; this.onAutoStop = onAutoStop;
    this.chunks = []; this.stream = null; this.recorder = null; this.seconds = 0;
  }

  async open(previewEl) {
    const audio = this.micId ? { deviceId: { exact: this.micId }, echoCancellation: false, noiseSuppression: true } : true;
    const video = this.video ? (this.camId ? { deviceId: { exact: this.camId }, width: { ideal: 1280 }, height: { ideal: 720 } } : true) : false;
    this.stream = await navigator.mediaDevices.getUserMedia({ audio, video });
    if (previewEl) { previewEl.srcObject = this.stream; previewEl.muted = true; await previewEl.play().catch(() => {}); }
    if (this.onLevel && this.stream.getAudioTracks().length) {
      const ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(this.stream);
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      this.audioCtx = ctx;
      const loop = () => {
        if (!this.stream) return;
        an.getFloatTimeDomainData(buf);
        let peak = 0;
        for (const v of buf) peak = Math.max(peak, Math.abs(v));
        this.onLevel(peak);
        this.levelRaf = requestAnimationFrame(loop);
      };
      loop();
    }
    return this.stream;
  }

  start() {
    const mime = this.video
      ? pickMime(['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'])
      : pickMime(['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']);
    this.mime = mime || (this.video ? 'video/webm' : 'audio/webm');
    this.recorder = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
    this.chunks = [];
    this.recorder.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.done = new Promise((res) => { this.recorder.onstop = () => res(new Blob(this.chunks, { type: this.mime })); });
    this.recorder.start(250);
    this.started = performance.now();
    this.timer = setInterval(() => {
      this.seconds = (performance.now() - this.started) / 1000;
      this.onTick && this.onTick(this.seconds);
      if (this.seconds >= MAX_SECONDS) { this.onAutoStop && this.onAutoStop(); }
    }, 200);
  }

  get recording() { return this.recorder && this.recorder.state === 'recording'; }

  async stop() {
    clearInterval(this.timer);
    if (!this.recorder || this.recorder.state === 'inactive') return null;
    this.recorder.stop();
    return this.done;
  }

  close() {
    clearInterval(this.timer);
    cancelAnimationFrame(this.levelRaf);
    if (this.stream) this.stream.getTracks().forEach(t => t.stop());
    this.stream = null;
    if (this.audioCtx) this.audioCtx.close().catch(() => {});
  }
}

export function fmtClock(s) {
  s = Math.max(0, Math.floor(s));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
