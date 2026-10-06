// Voice call widget for the Report an update box (a Streamlit v2 component).
// Hands-free and half-duplex: the agent speaks, then the mic listens until the supervisor stops
// talking (0.9 s of quiet), sends that turn to Python, and waits for the next reply.
// Python sends data = {active, reply_id, reply_port, reply_token, reply_text, end}: the reply streams
// from the local ElevenLabs relay at http://127.0.0.1:<port>/tts/<token>.mp3, so it starts playing
// before it's finished. This sends back "utterance" {id, b64, mime} and "event" {id, type: start | end | silence}.

const QUIET_MS = 900;       // this long without speech ends the supervisor's turn
const MIN_SPEECH_MS = 250;  // shorter blips are noise, not speech
const NO_SPEECH_MS = 12000; // nothing said for this long: tell Python, which asks again or hangs up
const MAX_TURN_MS = 30000;

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const S = (window.__cityLinkCall ||= { phase: "idle", lastReply: null, seq: 0, note: "" });
  S.send = setTriggerValue;

  let root = parentElement.querySelector(".cl-call");
  if (!root) {
    root = document.createElement("div");
    root.className = "cl-call";
    root.innerHTML = `
      <button class="cl-btn" type="button"></button>
      <div class="cl-status"><span class="cl-dot"></span><span class="cl-text"></span></div>
      <div class="cl-meter"><div class="cl-level"></div></div>
      <div class="cl-note"></div>`;
    parentElement.appendChild(root);
    root.querySelector(".cl-btn").addEventListener("click", () => (S.phase === "idle" ? start() : hangUp(true)));
  }
  S.root = root;
  render();

  if (S.phase !== "idle" && data && data.reply_id != null && data.reply_id !== S.lastReply) {
    S.lastReply = data.reply_id;
    const url = data.reply_port && data.reply_token
      ? `http://127.0.0.1:${data.reply_port}/tts/${data.reply_token}.mp3` : null;
    play(url, data.reply_text, data.end);
  }
}

function S_() { return window.__cityLinkCall; }

function render() {
  const S = S_(), root = S.root;
  if (!root) return;
  const labels = { idle: "", connecting: "Connecting…", speaking: "City Link is speaking…",
                   listening: "Listening… speak now", thinking: "Thinking…" };
  root.dataset.phase = S.phase;
  root.querySelector(".cl-btn").textContent = S.phase === "idle" ? "Start voice call" : "End call";
  root.querySelector(".cl-text").textContent = labels[S.phase] || "";
  root.querySelector(".cl-note").textContent = S.note || "";
  if (S.phase !== "listening") root.querySelector(".cl-level").style.width = "0%";
}

async function start() {
  const S = S_();
  S.note = "";
  S.phase = "connecting";
  render();
  try {
    S.stream = await navigator.mediaDevices.getUserMedia(
      { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) {
    S.phase = "idle";
    S.note = location.protocol === "https:" || location.hostname === "localhost"
      ? "The microphone is blocked. Allow it from the icon in the address bar, then start the call again."
      : "The microphone only works on localhost or https. Open the dashboard at http://localhost:8501.";
    render();
    return;
  }
  S.ctx = new AudioContext();
  await S.ctx.resume();
  S.analyser = S.ctx.createAnalyser();
  S.analyser.fftSize = 1024;
  S.ctx.createMediaStreamSource(S.stream).connect(S.analyser);
  S.floor = await noiseFloor(400);
  S.audio = S.audio || new Audio();
  S.phase = "thinking";
  render();
  S.send("event", { id: ++S.seq, type: "start" });
}

function rms() {
  const S = S_(), buf = new Float32Array(S.analyser.fftSize);
  S.analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (const v of buf) sum += v * v;
  return Math.sqrt(sum / buf.length);
}

function noiseFloor(ms) {
  return new Promise((resolve) => {
    const vals = [], t = setInterval(() => vals.push(rms()), 50);
    setTimeout(() => { clearInterval(t); vals.sort((a, b) => a - b); resolve(vals[Math.floor(vals.length / 2)] || 0.005); }, ms);
  });
}

function play(url, text, end) {
  const S = S_();
  S.phase = "speaking";
  render();
  let done = false;
  const next = () => {
    if (done || S.phase === "idle") return;
    done = true;
    if (end) hangUp(false);
    else setTimeout(listen, 250);  // a beat after the agent stops, so its own voice isn't recorded
  };
  if (url) {
    const fallback = () => { if (!done) speakInBrowser(text, next); };
    S.audio.onended = next;
    S.audio.onerror = fallback;  // relay or ElevenLabs down: the browser's voice keeps the call going
    S.audio.src = url;
    S.audio.play().catch(fallback);
  } else {
    speakInBrowser(text, next);  // ElevenLabs unavailable: the browser's own voice keeps the call going
  }
}

function speakInBrowser(text, done) {
  if (!text || !window.speechSynthesis) { done(); return; }
  const u = new SpeechSynthesisUtterance(text);
  u.onend = done;
  u.onerror = done;
  speechSynthesis.speak(u);
}

function listen() {
  const S = S_();
  if (S.phase === "idle") return;
  S.phase = "listening";
  render();
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  const mime = types.find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || "";
  const rec = new MediaRecorder(S.stream, mime ? { mimeType: mime } : undefined);
  const chunks = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.start(100);
  S.rec = rec;

  const t0 = performance.now();
  let started = false, lastVoice = 0, speechMs = 0;
  const level = S.root.querySelector(".cl-level");
  S.timer = setInterval(() => {
    const now = performance.now(), r = rms(), loud = r > Math.max(0.012, S.floor * 3);
    level.style.width = Math.min(100, Math.round(r * 900)) + "%";
    if (loud) {
      started = true;
      lastVoice = now;
      speechMs += 50;
    } else if (!started) {
      S.floor = 0.95 * S.floor + 0.05 * r;  // follow the room's background noise
    }
    if (started && now - lastVoice > QUIET_MS) {
      if (speechMs >= MIN_SPEECH_MS) finish(true);
      else { started = false; speechMs = 0; }  // a cough or a click: keep listening
    } else if (!started && now - t0 > NO_SPEECH_MS) finish(false);
    else if (now - t0 > MAX_TURN_MS) finish(started);
  }, 50);

  function finish(gotSpeech) {
    clearInterval(S.timer);
    S.phase = "thinking";
    render();
    rec.onstop = () => {
      if (S.phase === "idle") return;
      if (!gotSpeech) { S.send("event", { id: ++S.seq, type: "silence" }); return; }
      const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
      const reader = new FileReader();
      reader.onload = () => S.send("utterance", {
        id: ++S.seq, b64: String(reader.result).split(",")[1], mime: blob.type });
      reader.readAsDataURL(blob);
    };
    rec.stop();
  }
}

function hangUp(tellPython) {
  const S = S_();
  clearInterval(S.timer);
  if (S.rec && S.rec.state !== "inactive") { S.rec.onstop = null; S.rec.stop(); }
  if (S.audio) S.audio.pause();
  if (window.speechSynthesis) speechSynthesis.cancel();
  if (S.stream) S.stream.getTracks().forEach((t) => t.stop());
  if (S.ctx) S.ctx.close();
  S.stream = S.ctx = S.rec = null;
  S.phase = "idle";
  render();
  if (tellPython) S.send("event", { id: ++S.seq, type: "end" });
}
