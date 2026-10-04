/* Today's dispatch plan in the 3D city - display only, no effect on traffic.
 * The dashboard (dispatch/sim3d.py) writes dispatch_plan.json: the plan's jobs inside the simulated
 * downtown, in scene coordinates, with each crew's colour. Tall beams so they read from the drone view.
 * Polls every 10 s, so a replan on the dashboard shows up here. */
import * as THREE from 'three';

const group = new THREE.Group();
const geom = {
  beam: new THREE.CylinderGeometry(5, 5, 220, 12),
  head: new THREE.SphereGeometry(24, 20, 20),
  ring: new THREE.RingGeometry(34, 48, 40),
};
let last = '';

function marker(job) {
  const color = new THREE.Color(job.color);
  const m = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.6 });
  const g = new THREE.Group();
  const beam = new THREE.Mesh(geom.beam, m); beam.position.y = 110;
  const head = new THREE.Mesh(geom.head, m); head.position.y = 236;
  const ring = new THREE.Mesh(geom.ring, new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide,
    transparent: true, opacity: 0.7 }));
  ring.rotation.x = -Math.PI / 2; ring.position.y = 1;
  g.add(beam, head, ring);
  if (job.safety) head.scale.setScalar(1.35);
  g.userData = job;
  return g;
}

async function refresh() {
  try {
    const r = await fetch('dispatch_plan.json?t=' + Date.now());
    if (!r.ok) return;
    const text = await r.text();
    if (text === last) return;
    last = text;
    const { jobs } = JSON.parse(text);
    const CEN = window.__dbg.CEN;
    group.clear();
    const seen = {};
    jobs.forEach(j => {
      const p = marker(j);
      const key = Math.round(j.x / 20) + ',' + Math.round(j.y / 20);
      const k = seen[key] = (seen[key] || 0) + 1;      // jobs at the same spot: fan them out so each shows
      const a = k * 2.1, r = k > 1 ? 45 : 0;
      p.position.set(j.x - CEN.x + r * Math.cos(a), 0, -(j.y - CEN.y) + r * Math.sin(a));
      group.add(p);
    });
    const list = document.getElementById('plan-pins-list');
    if (list) list.innerHTML = jobs.length ? jobs.map(j =>
      `<div><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${j.color}"></span>
       Crew ${j.crew} · ${j.type}${j.safety ? ' ⚠' : ''} <span style="opacity:.6">(${j.community.toLowerCase()})</span></div>`
    ).join('') : '<div style="opacity:.6">No jobs downtown on this plan</div>';
  } catch (e) { console.warn('[plan pins]', e); }
}

// Embedded in the dashboard (?embed=1): the dashboard has its own caller agent, so hide the sim's call panel.
const EMBED = new URLSearchParams(location.search).has('embed');
if (EMBED) {
  const css = document.createElement('style');
  css.textContent = '#dispatch-panel { display: none !important; }';
  document.head.appendChild(css);
}

(async function init() {
  while (!window.__dbg || !window.__dbg.scene || !window.__dbg.CEN || (window.__dbg.roadMeshMap || {}).size === 0)
    await new Promise(r => setTimeout(r, 500));
  window.__dbg.scene.add(group);
  const body = document.querySelector('#panel-body');
  const box = document.createElement('div');
  box.className = 'ctrl-group';
  box.innerHTML = `<label class="checkbox-row"><input type="checkbox" checked> <strong>Today's dispatch plan</strong></label>
    <div id="plan-pins-list" style="font-size:12px;line-height:1.6;margin-top:4px"></div>`;
  box.querySelector('input').onchange = e => { group.visible = e.target.checked; };
  if (body) body.insertBefore(box, body.firstChild);
  refresh();
  setInterval(refresh, 10000);
})();
