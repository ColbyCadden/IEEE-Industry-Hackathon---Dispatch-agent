/* Dispatch panel: team routes + live calls. Display/planning only - never touches traffic.
 *  - Team picker draws that crew's route on the map with numbered stops and ETAs.
 *  - Calls: speak (browser speech recognition) or type a live update; the server classifies it,
 *    updates the map/priority list, re-plans routes, and ElevenLabs speaks the reply. */
import * as THREE from 'three';

const group = new THREE.Group();
let routes = null, lastGen = null, recog = null, listening = false;
const selected = new Set();      // team names currently shown; empty = none
let selInit = false;             // first load selects every team
const beacons = [];              // animated task beacons

const css = document.createElement('style');
css.textContent = `
#dispatch-panel{position:fixed;left:12px;bottom:12px;width:360px;max-height:62vh;display:flex;flex-direction:column;
 background:rgba(20,25,35,.9);backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,.12);border-radius:14px;
 z-index:210;font-size:12px;color:#fff}
#dispatch-panel.min #dp-body{display:none}
#dp-head{display:flex;justify-content:space-between;align-items:center;padding:9px 12px;cursor:pointer;font-weight:600;font-size:13px}
#dp-body{overflow-y:auto;padding:0 12px 12px}
#dp-body h4{margin:10px 0 4px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;opacity:.6}
#dp-body select,#dp-body input[type=text]{width:100%;box-sizing:border-box;background:rgba(255,255,255,.08);color:#fff;
 border:1px solid rgba(255,255,255,.18);border-radius:6px;padding:6px;font-size:12px}
#dp-body select option{background:#1a1a2e}
.dp-row{display:flex;gap:6px;margin-top:6px}
.dp-btn{background:rgba(74,170,255,.25);border:1px solid rgba(74,170,255,.6);color:#fff;border-radius:6px;padding:6px 10px;cursor:pointer;font-size:12px}
.dp-btn:hover{background:rgba(74,170,255,.45)}
.dp-btn.rec{background:rgba(234,67,53,.5);border-color:#ea4335}
.dp-stop{display:flex;gap:6px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.07)}
.dp-num{flex:0 0 18px;height:18px;border-radius:50%;text-align:center;line-height:18px;font-weight:700;color:#000}
.dp-meta{opacity:.65;font-size:11px}
.dp-change{padding:3px 6px;margin-top:3px;border-left:3px solid #fbbc04;background:rgba(255,255,255,.05);font-size:11px}
.dp-reply{margin-top:6px;padding:6px;background:rgba(52,168,83,.18);border-radius:6px}
.dp-cand{display:block;width:100%;text-align:left;margin-top:4px}
#dp-body .dp-tg{margin-top:8px;border-left:4px solid var(--c);padding-left:7px}
.dp-tgh{font-weight:700;color:var(--c)}
.dp-card{display:flex;gap:6px;align-items:flex-start;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.07)}
.dp-card .dp-act{margin-left:auto;display:flex;flex-direction:column;gap:3px}
.dp-mini{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.25);color:#fff;border-radius:5px;padding:2px 7px;font-size:11px;cursor:pointer}
.dp-mini:hover{background:rgba(255,255,255,.22)}
.dp-mini.fix{background:rgba(52,168,83,.35);border-color:#34a853}
.dp-mini.fix:hover{background:rgba(52,168,83,.6)}
#dp-pop{position:fixed;z-index:300;min-width:230px;max-width:290px;padding:10px 12px;border-radius:12px;color:#fff;font-size:12px;
 background:rgba(20,25,35,.96);border:2px solid var(--c,#4af);box-shadow:0 8px 30px rgba(0,0,0,.5)}
#dp-pop .t{font-weight:700;font-size:13px;color:var(--c)}
#dp-pop .x{position:absolute;top:4px;right:8px;cursor:pointer;opacity:.7}
#dp-pop .dp-btn{width:100%;margin-top:8px;padding:8px;font-size:13px;background:rgba(52,168,83,.4);border-color:#34a853}
#dp-teams{display:flex;flex-direction:column;gap:3px;margin-top:4px}
.dp-team{display:flex;align-items:center;gap:7px;padding:4px 6px;border-radius:6px;cursor:pointer;border:1px solid transparent;user-select:none}
.dp-team:hover{background:rgba(255,255,255,.07)}
.dp-team.on{background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.25)}
.dp-team input{accent-color:#4af;margin:0}
.dp-sw{width:11px;height:11px;border-radius:50%;flex:0 0 11px}
.dp-team .dp-tm{margin-left:auto;opacity:.65;font-size:11px}
`;
document.head.appendChild(css);

const el = document.createElement('div');
el.id = 'dispatch-panel';
el.innerHTML = `
<div id="dp-head"><span>🚧 Dispatch &amp; Calls</span><span id="dp-toggle">−</span></div>
<div id="dp-body">
 <h4>Team routes</h4>
 <div class="dp-row" style="margin-top:0"><button class="dp-btn" id="dp-all">Select all</button><button class="dp-btn" id="dp-none">Deselect all</button></div>
 <div id="dp-teams"></div>
 <div id="dp-summary" class="dp-meta" style="margin-top:6px"></div>
 <div id="dp-stops"></div>
 <h4>Live calls</h4>
 <div class="dp-meta">Say or type an update, e.g. “the debris at 728 6 street southwest was picked up”.</div>
 <div class="dp-row"><input type="text" id="dp-text" placeholder="Caller update…">
  <button class="dp-btn" id="dp-send">Send</button><button class="dp-btn" id="dp-mic" title="Speak">🎤</button></div>
 <div id="dp-reply"></div>
 <h4>Plan changes</h4>
 <div id="dp-changes" class="dp-meta">none yet</div>
 <h4>Priority list in use</h4>
 <div id="dp-prio" class="dp-meta"></div>
</div>`;
document.body.appendChild(el);
const $ = id => document.getElementById(id);
['keydown', 'keyup', 'keypress'].forEach(ev => el.addEventListener(ev, e => e.stopPropagation()));
$('dp-head').onclick = () => { el.classList.toggle('min'); $('dp-toggle').textContent = el.classList.contains('min') ? '+' : '−'; };

function label(n, color) {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d');
  g.fillStyle = color; g.beginPath(); g.arc(32, 32, 28, 0, 7); g.fill();
  g.lineWidth = 4; g.strokeStyle = '#000'; g.stroke();
  g.fillStyle = '#000'; g.font = 'bold 36px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(String(n), 32, 34);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false, transparent: true }));
  sp.scale.set(26, 26, 1); sp.renderOrder = 1000;
  return sp;
}

function textLabel(lines, color) {
  const c = document.createElement('canvas'); c.width = 512; c.height = 128;
  const g = c.getContext('2d');
  g.fillStyle = 'rgba(15,18,28,.88)'; g.strokeStyle = color; g.lineWidth = 6;
  g.beginPath(); g.roundRect(4, 4, 504, 120, 18); g.fill(); g.stroke();
  g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillStyle = color; g.font = 'bold 44px sans-serif'; g.fillText(lines[0], 256, 44);
  g.fillStyle = '#fff'; g.font = '34px sans-serif'; g.fillText(lines[1], 256, 90);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false, transparent: true }));
  sp.scale.set(92, 23, 1); sp.renderOrder = 1001;
  return sp;
}
const short = l => (l || '').replace(/,\s*CALGARY.*$/i, '').slice(0, 26) || 'no street address';
const catName = c => (c || '').replace('_', ' ');
const hitMeshes = [];

function beacon(color, num, big, stop, team) {
  // Tall glowing pillar + pulsing ground ring + numbered head, visible from far away.
  const g = new THREE.Group();
  const H = 170;
  const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .55, depthTest: false });
  const pillar = new THREE.Mesh(new THREE.CylinderGeometry(3.5, 3.5, H, 12, 1, true), mat);
  pillar.position.y = H / 2; pillar.renderOrder = 998;
  const core = new THREE.Mesh(new THREE.CylinderGeometry(1.2, 1.2, H, 8),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: .9, depthTest: false }));
  core.position.y = H / 2; core.renderOrder = 999;
  const ring = new THREE.Mesh(new THREE.RingGeometry(14, 20, 40),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .8, side: THREE.DoubleSide, depthTest: false }));
  ring.rotation.x = -Math.PI / 2; ring.position.y = 2; ring.renderOrder = 998;
  const head = label(num, color); head.scale.set(big ? 44 : 34, big ? 44 : 34, 1); head.position.y = H + 22;
  g.add(pillar, core, ring, head);
  if (stop) {
    const tag = textLabel([`${team.team} · #${stop.order} ${catName(stop.category)}`, short(stop.location)], color);
    tag.position.y = H + 58; g.add(tag);
  }
  if (stop) {   // fat invisible cylinder so the whole pillar is easy to click
    const hit = new THREE.Mesh(new THREE.CylinderGeometry(12, 12, H + 40, 8),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));
    hit.position.y = (H + 40) / 2; hit.userData.stop = stop; hit.userData.team = team;
    g.add(hit); hitMeshes.push(hit);
  }
  beacons.push({ ring, head, base: head.scale.x, t: Math.random() * 6 });
  return g;
}

function draw() {
  group.clear();
  beacons.length = 0; hitMeshes.length = 0;
  const CEN = window.__dbg.CEN;
  const P = (x, y, h = 4) => new THREE.Vector3(x - CEN.x, h, -(y - CEN.y));
  const all = routes?.teams || [];
  const many = selected.size > 1;
  all.forEach(t => {
    const on = selected.has(t.team);
    if (on && t.path.length > 1) {
      const pts = t.path.map(p => P(p[0], p[1]));
      const curve = new THREE.CatmullRomCurve3(pts, false, 'catmullrom', 0);
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, pts.length * 2, many ? 2.2 : 3.4, 6, false),
        new THREE.MeshBasicMaterial({ color: t.color, depthTest: false, transparent: true, opacity: .9 }));
      tube.renderOrder = 999; group.add(tube);
    }
    if (on) { const depot = label('S', t.color); depot.position.copy(P(t.start.x, t.start.y, 28)); group.add(depot); }
    // Every dispatch task always gets a beacon; deselected teams' tasks go grey with no number.
    t.stops.forEach(s => {
      const b = on ? beacon(t.color, s.order, true, s, t) : beacon('#9a9aa5', '•', false, s, { ...t, color: '#9a9aa5' });
      b.position.copy(P(s.x, s.y, 0));
      group.add(b);
    });
  });
}

(function pulse() {
  const t = performance.now() / 1000;
  beacons.forEach(b => {
    const k = (t * 0.9 + b.t) % 1;
    b.ring.scale.setScalar(1 + k * 1.8);
    b.ring.material.opacity = .85 * (1 - k);
    const s = b.base * (1 + 0.08 * Math.sin(t * 4 + b.t));
    b.head.scale.set(s, s, 1);
  });
  requestAnimationFrame(pulse);
})();

function render() {
  if (!routes) return;
  const names = routes.teams.map(t => t.team);
  if (!selInit) { names.forEach(n => selected.add(n)); selInit = true; }
  [...selected].forEach(n => { if (!names.includes(n)) selected.delete(n); });
  $('dp-teams').innerHTML = routes.teams.map(t => `
    <label class="dp-team ${selected.has(t.team) ? 'on' : ''}" data-team="${t.team}">
      <input type="checkbox" ${selected.has(t.team) ? 'checked' : ''}>
      <span class="dp-sw" style="background:${t.color}"></span>${t.team}
      <span class="dp-tm">${t.stops.length} stops · ${t.total_min} min</span></label>`).join('');
  $('dp-teams').querySelectorAll('.dp-team').forEach(row => {
    row.querySelector('input').onchange = e => {
      const n = row.dataset.team;
      e.target.checked ? selected.add(n) : selected.delete(n);
      render();
    };
  });
  const ts = routes.teams.filter(t => selected.has(t.team));
  if (!ts.length) {
    $('dp-summary').textContent = 'No team selected - tick one or more teams to see their routes.';
  } else if (ts.length === 1) {
    const t = ts[0];
    $('dp-summary').innerHTML = `${t.distance_km} km · drive ${t.drive_min} min + work ${t.work_min} min = <b>${t.total_min} min</b>${t.closed_edges_on_route ? ` · ${t.closed_edges_on_route} closed road(s) on route` : ''}`;
  } else {
    const fin = Math.max(...ts.map(t => t.total_min));
    $('dp-summary').innerHTML = `${ts.length} teams · ${ts.reduce((n, t) => n + t.stops.length, 0)} stops · last one done in <b>${fin} min</b>`
      + (ts.length === routes.teams.length ? ` · weighted arrival ${routes.objective.weighted_arrival_min} vs naive ${routes.baseline_objective.weighted_arrival_min}` : '');
  }
  $('dp-stops').innerHTML = ts.map(t => `
    <div class="dp-tg" style="--c:${t.color}">
      <div class="dp-tgh">${t.team} fixes ${t.stops.length} issue${t.stops.length === 1 ? '' : 's'} · ${t.total_min} min · ${t.distance_km} km</div>
      ${t.stops.length ? t.stops.map(st => `<div class="dp-card"><div class="dp-num" style="background:${t.color}">${st.order}</div>
        <div><div><b>${catName(st.category)}</b> · priority ${st.priority}</div>
        <div class="dp-meta">${st.location || st.request_id}</div>
        <div class="dp-meta">arrive +${st.arrive_min} min · work ${st.service_min} · done +${st.done_min}</div></div>
        <div class="dp-act"><button class="dp-mini fix" data-fix="${st.request_id}">✔ Fix</button>
        <button class="dp-mini" data-loc="${st.x},${st.y}">⌖ Show</button></div></div>`).join('')
      : '<div class="dp-meta">No issues assigned</div>'}
    </div>`).join('');
  $('dp-changes').innerHTML = routes.changes.length ? routes.changes.map(c => `<div class="dp-change">${c}</div>`).join('') : 'none yet';
  $('dp-prio').textContent = `${routes.inputs.priority_rows} rows from: ${routes.inputs.priority_source.join(', ') || '?'}`
    + (routes.inputs.priority_source.some(s => /fake/i.test(s)) ? '  (DEMO list - real priority agent not connected)' : '');
  draw();
}

async function poll() {
  try {
    const r = await (await fetch('/routes.json', { cache: 'no-store' })).json();
    if (r.generated_at && r.generated_at !== lastGen) { lastGen = r.generated_at; routes = r; render(); }
  } catch (e) { console.warn('[dispatch]', e); }
}
$('dp-all').onclick = () => { (routes?.teams || []).forEach(t => selected.add(t.team)); render(); };
$('dp-none').onclick = () => { selected.clear(); render(); };

async function speak(text) {
  try {
    const r = await fetch('/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
    if (!r.ok) return;
    new Audio(URL.createObjectURL(await r.blob())).play();
  } catch (e) { /* voice is optional */ }
}

async function sendCall(payload) {
  $('dp-reply').innerHTML = '<div class="dp-meta">processing…</div>';
  const res = await (await fetch('/call', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json();
  if (res.error) { $('dp-reply').innerHTML = `<div class="dp-reply" style="background:rgba(234,67,53,.25)">${res.error}</div>`; return; }
  let html = `<div class="dp-reply">🤖 ${res.reply}</div>`;
  (res.candidates || []).forEach(c => {
    html += `<button class="dp-btn dp-cand" data-id="${c.id}">${c.category.replace('_', ' ')} — ${c.location || c.id}</button>`;
  });
  $('dp-reply').innerHTML = html;
  $('dp-reply').querySelectorAll('.dp-cand').forEach(b => b.onclick = () =>
    sendCall({ transcript: payload.transcript, request_id: b.dataset.id, action: res.action }));
  speak(res.reply);
  if (res.applied) {
    window.__refreshRequests && window.__refreshRequests();
    [4000, 9000].forEach(ms => setTimeout(poll, ms));       // planner runs in the background
  }
}
$('dp-send').onclick = () => { const t = $('dp-text').value.trim(); if (t) { sendCall({ transcript: t, caller: 'web' }); $('dp-text').value = ''; } };
$('dp-text').addEventListener('keydown', e => { if (e.key === 'Enter') $('dp-send').click(); });

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
$('dp-mic').onclick = () => {
  if (!SR) { $('dp-reply').innerHTML = '<div class="dp-reply">Speech input needs Chrome or Edge. Type the update instead.</div>'; return; }
  if (listening) { recog.stop(); return; }
  recog = new SR(); recog.lang = 'en-CA'; recog.interimResults = false;
  recog.onstart = () => { listening = true; $('dp-mic').classList.add('rec'); };
  recog.onend = () => { listening = false; $('dp-mic').classList.remove('rec'); };
  recog.onerror = e => { $('dp-reply').innerHTML = `<div class="dp-reply">Mic error: ${e.error}. Type the update instead.</div>`; };
  recog.onresult = e => { const t = e.results[0][0].transcript; $('dp-text').value = t; sendCall({ transcript: t, caller: 'voice' }); };
  recog.start();
};


// ---------- click a marker -> popup -> fix on the spot ----------
function closePop() { document.getElementById('dp-pop')?.remove(); }
function openPop(stop, team, cx, cy) {
  closePop();
  const d = document.createElement('div'); d.id = 'dp-pop'; d.style.setProperty('--c', team.color);
  d.style.left = Math.min(cx + 14, innerWidth - 300) + 'px'; d.style.top = Math.min(cy + 14, innerHeight - 190) + 'px';
  d.innerHTML = `<span class="x">✕</span><div class="t">${catName(stop.category)} · priority ${stop.priority}</div>
    <div>${stop.location || stop.request_id}</div>
    <div class="dp-meta">${team.team} · stop #${stop.order} · arrives +${stop.arrive_min} min · ${stop.service_min} min of work</div>
    <button class="dp-btn">✔ Fix it now (mark resolved)</button>`;
  document.body.appendChild(d);
  ['mousedown', 'click', 'keydown'].forEach(ev => d.addEventListener(ev, e => e.stopPropagation()));
  d.querySelector('.x').onclick = closePop;
  d.querySelector('.dp-btn').onclick = () => { closePop(); fixNow(stop.request_id, stop); };
}
async function fixNow(id, stop) {
  $('dp-reply').innerHTML = `<div class="dp-meta">fixing ${stop ? catName(stop.category) : id} and re-planning routes…</div>`;
  el.classList.remove('min'); $('dp-toggle').textContent = '−';
  const res = await (await fetch('/call', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript: 'Fixed on site from the dispatch map', caller: 'dispatch-map', request_id: id, action: 'resolved', wait: true }) })).json();
  if (res.error || !res.applied) { $('dp-reply').innerHTML = `<div class="dp-reply" style="background:rgba(234,67,53,.25)">${res.error || res.reply}</div>`; return; }
  $('dp-reply').innerHTML = `<div class="dp-reply">✔ ${res.reply}</div>`;
  window.__refreshRequests && window.__refreshRequests();
  poll();
}
$('dp-stops').addEventListener('click', e => {
  const f = e.target.closest('[data-fix]'), l = e.target.closest('[data-loc]');
  if (f) fixNow(f.dataset.fix, null);
  if (l) {
    const [x, y] = l.dataset.loc.split(',').map(Number), CEN = window.__dbg.CEN, cam = window.__dbg.camera;
    if (!cam) return;
    const tx = x - CEN.x, tz = -(y - CEN.y), from = cam.position.clone(), to = new THREE.Vector3(tx, 230, tz + 140), t0 = Date.now();
    (function step() {
      const k = Math.min((Date.now() - t0) / 1200, 1);
      cam.position.lerpVectors(from, to, k); cam.lookAt(tx, 0, tz);
      if (k < 1) requestAnimationFrame(step);
    })();
  }
});
const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
document.addEventListener('click', e => {
  if (document.pointerLockElement || !(e.target instanceof HTMLCanvasElement) || !hitMeshes.length) return;
  mouse.set((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
  ray.setFromCamera(mouse, window.__dbg.camera);
  const hit = ray.intersectObjects(hitMeshes, false)[0];
  if (hit) {            // swallow the click so it doesn't also lock the mouse into fly mode
    e.stopPropagation(); e.preventDefault();
    openPop(hit.object.userData.stop, hit.object.userData.team, e.clientX, e.clientY);
  } else closePop();
}, true);

(async function init() {
  while (!window.__dbg || !window.__dbg.scene || !window.__dbg.CEN) await new Promise(r => setTimeout(r, 500));
  window.__dbg.scene.add(group);
  poll(); setInterval(poll, 8000);
  window.addEventListener('dp-routes', poll);   // a live call just re-planned
})();
