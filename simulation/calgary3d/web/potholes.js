/* 311 request pins - display only, no effect on traffic.
 * One colour per category, toggled from the control panel. Closed requests are
 * drawn small and dim. Polls /requests.json every 60s. */
import * as THREE from 'three';

const CATS = {
  pothole:          ['Potholes',          0xff3b30],
  debris:           ['Debris',            0xff9500],
  sign:             ['Signs',             0x0a84ff],
  signal:           ['Traffic signals',   0xffd60a],
  dead_animal:      ['Dead animals',      0xbf5af2],
  road_maintenance: ['Road maintenance',  0x30d158],
  markings:         ['Pavement markings', 0x64d2ff],
  trash:            ['Trash / waste',     0x8e8e93],
};
const groups = {};
const counts = {};
const geom = {
  head: new THREE.SphereGeometry(6, 12, 12),
  cone: new THREE.ConeGeometry(4, 20, 10),
};

function pin(color, closed) {
  const m = new THREE.MeshStandardMaterial({ color, emissive: color,
    emissiveIntensity: closed ? 0.05 : 0.45, transparent: closed, opacity: closed ? 0.45 : 1 });
  const g = new THREE.Group();
  const head = new THREE.Mesh(geom.head, m); head.position.y = 24;
  const cone = new THREE.Mesh(geom.cone, m); cone.rotation.x = Math.PI; cone.position.y = 10;
  g.add(head, cone);
  if (closed) g.scale.setScalar(0.6);
  return g;
}

async function refresh() {
  try {
    const d = await (await fetch('/requests.json')).json();
    const CEN = window.__dbg.CEN;
    Object.values(groups).forEach(g => g.clear());
    Object.keys(counts).forEach(k => counts[k] = [0, 0]);
    d.requests.forEach(r => {
      const g = groups[r.category]; if (!g) return;
      const closed = /closed/i.test(r.status);
      const p = pin(CATS[r.category][1], closed);
      p.position.set(r.x - CEN.x, 0, -(r.y - CEN.y));
      p.userData = r;
      g.add(p);
      counts[r.category][closed ? 1 : 0]++;
    });
    Object.entries(counts).forEach(([k, [o, c]]) => {
      const el = document.getElementById('cnt-' + k);
      if (el) el.textContent = `${o} open / ${c} closed`;
    });
  } catch (e) { console.warn('[311 pins]', e); }
}

(async function init() {
  while (!window.__dbg || !window.__dbg.scene || !window.__dbg.CEN || (window.__dbg.roadMeshMap || {}).size === 0)
    await new Promise(r => setTimeout(r, 500));
  const body = document.querySelector('#panel-body');
  const box = document.createElement('div');
  box.innerHTML = '<strong>311 requests (display only)</strong>';
  Object.entries(CATS).forEach(([k, [label, col]]) => {
    groups[k] = new THREE.Group();
    counts[k] = [0, 0];
    window.__dbg.scene.add(groups[k]);
    const row = document.createElement('div');
    row.className = 'ctrl-group checkbox-row';
    row.innerHTML = `<label><input type="checkbox" checked>
      <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#${col.toString(16).padStart(6, '0')}"></span>
      ${label}</label> <span id="cnt-${k}" style="font-size:11px;opacity:.7"></span>`;
    row.querySelector('input').onchange = e => { groups[k].visible = e.target.checked; };
    box.appendChild(row);
  });
  if (body) body.insertBefore(box, body.firstChild);
  refresh();
  setInterval(refresh, 60000);
})();
