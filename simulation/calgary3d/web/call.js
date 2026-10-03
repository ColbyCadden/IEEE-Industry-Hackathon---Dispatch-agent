/* Live voice call with the dispatch agent.
 *
 * A limited ElevenLabs key cannot create a ConvAI agent (convai_write) or transcribe
 * audio (speech_to_text), so this drives the dialogue itself:
 *   browser listens (Web Speech API)
 *     -> POST /call/next  (agents/dispatch/calls.py:next_question decides the reply)
 *     -> POST /tts        (ElevenLabs speaks the agent's line)
 * The agent asks which issue, asks what happened to it, then updates the map pin,
 * the priority list and every team's route.
 *
 * Upgrade path: with a key holding convai_write + speech_to_text, replace
 * next_question() with a ConvAI agent whose tools call
 * agents.dispatch.calls.handle_call(), and swap the browser recogniser for the
 * ElevenLabs STT websocket. Nothing else has to change.
 */
const $ = id => document.getElementById(id);
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

const st = {
  on: false, callId: null, phase: 'idle', lastQ: '', lastA: '',
  recog: null, listening: false, speaking: false, cand: [],
};

const css = document.createElement('style');
css.textContent = `
.dp-btn.call{background:rgba(52,168,83,.35);border-color:#34a853;flex:1}
.dp-btn.call.on{background:rgba(234,67,53,.5);border-color:#ea4335;animation:dpulse 1.2s infinite}
.dp-btn.hang{background:rgba(234,67,53,.4);border-color:#ea4335}
@keyframes dpulse{0%,100%{opacity:1}50%{opacity:.55}}
#dp-live{margin-top:8px;padding:8px;border-radius:8px;background:rgba(74,170,255,.12);border:1px solid rgba(74,170,255,.35);font-size:12px}
#dp-live.q{background:rgba(255,255,255,.07);border-color:rgba(255,255,255,.25)}
#dp-live .ag{color:#4af;font-weight:600;margin-bottom:2px}
#dp-live .you{margin-top:5px;color:#ffd60a}
#dp-live .mic{opacity:.75;margin-top:5px}
`;
document.head.appendChild(css);

function ui() {
  const box = $('dp-live');
  if (!st.on) { box.className = ''; box.innerHTML = ''; return; }
  box.className = st.listening ? '' : 'q';
  const cands = st.cand.length
    ? `<div style="margin-top:6px">${st.cand.map((c, i) =>
        `<button class="dp-btn dp-cand" data-cand="${i}" style="width:100%;margin-top:3px">${i + 1}. ${c.category.replace('_', ' ')} — ${c.location || c.community || c.id}</button>`).join('')}</div>`
    : '';
  box.innerHTML = `<div class="ag">\u{1F3A7} Dispatch agent${st.listening ? ' (listening...)' : ''}</div>
    <div>${st.lastQ}</div>${cands}
    <div class="you">${st.lastA ? 'You: ' + st.lastA : ''}</div>
    <div class="mic">${SR ? 'Mic on: speak your answer' : 'Speech input needs Chrome or Edge - type your answer below'}</div>`;
  box.querySelectorAll('[data-cand]').forEach(b => b.onclick = () => {
    const c = st.cand[+b.dataset.cand];
    b.blur();
    turn({ request_id: c.id });
  });
}

async function speak(text) {
  if (!text) return;
  st.speaking = true;
  try {
    const r = await fetch('/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }) });
    if (r.ok) { const a = new Audio(URL.createObjectURL(await r.blob())); await a.play().catch(() => {}); }
  } catch (e) { /* voice is optional */ }
  st.speaking = false;
}

async function turn(payload) {
  const res = await (await fetch('/call/next', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, call_id: st.callId }) })).json();
  if (res.error) { st.lastQ = 'Error: ' + res.error; st.on = false; ui(); return; }
  st.phase = res.state;
  st.lastQ = res.question || '';
  st.cand = res.candidates || [];
  ui();
  await speak(res.question);
  if (res.state === 'done' && res.result && res.result.applied) {
    window.__refreshRequests && window.__refreshRequests();
    window.dispatchEvent(new Event('dp-routes'));
    setTimeout(() => window.dispatchEvent(new Event('dp-routes')), 6000);
  }
  if (st.on) listen();
}

function listen() {
  if (!SR || !st.on || st.speaking) return;
  stop();
  st.recog = new SR();
  st.recog.lang = 'en-CA'; st.recog.interimResults = false; st.recog.continuous = false;
  st.recog.onstart = () => { st.listening = true; ui(); };
  st.recog.onend = () => { st.listening = false; ui(); };
  st.recog.onerror = e => { st.lastQ = 'Mic error: ' + e.error + '. Type your answer instead.'; ui(); };
  st.recog.onresult = e => {
    const t = e.results[0][0].transcript.trim();
    if (t) { st.lastA = t; ui(); turn({ utterance: t }); }
  };
  try { st.recog.start(); } catch (e) { /* already started */ }
}
function stop() { if (st.recog) { try { st.recog.stop(); } catch (e) {} } }

function attachCall({ button }) {
  const host = $('dp-body');
  if (!host) return;
  const row = document.createElement('div');
  row.className = 'dp-row';
  row.innerHTML = `<button class="dp-btn call" id="${button}">\u{1F4DE} Hoop on a call</button>
    <button class="dp-btn hang" id="${button}-end" disabled>Hang up</button>`;
  const live = document.createElement('div');
  live.id = 'dp-live';
  row.after(live);
  const anchor = host.querySelector('#dp-live') || host.firstChild;
  host.insertBefore(row, anchor);

  const b = $(button), e = $(button + '-end');
  b.onclick = () => {
    st.on = true; st.phase = 'idle'; st.lastA = ''; st.lastQ = 'Calling dispatch...';
    st.callId = 'c' + Date.now().toString(36);
    b.classList.add('on'); b.textContent = '\u{1F3A7} On call'; e.disabled = false;
    ui();
    turn({});
  };
  e.onclick = () => {
    st.on = false; stop();
    b.classList.remove('on'); b.textContent = '\u{1F4DE} Hoop on a call'; e.disabled = true;
    st.lastQ = ''; ui();
  };
}

/* dispatch.js builds the panel in a separate module; wait for it. */
(function boot() {
  if ($('dp-body')) return attachCall({ button: 'dp-call' });
  const t = setInterval(() => { if ($('dp-body')) { clearInterval(t); attachCall({ button: 'dp-call' }); } }, 250);
  setTimeout(() => clearInterval(t), 30000);
})();
