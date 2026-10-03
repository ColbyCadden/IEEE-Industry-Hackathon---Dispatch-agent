/* 311 Mission Control: a city-wide replay of the dispatch agent's day.
 * Data comes from data/mission.js (built by build.py from dispatch/outputs). Everything here is
 * presentation: scenes, camera, deck.gl layers and the HUD. No numbers are computed that the
 * engine did not produce, except job finish times (from the illustrative timeline in build.py). */
(() => {
'use strict';
const M = window.MISSION;
if (!M) { document.body.innerHTML = '<p style="padding:2em">Missing data/mission.js - run <code>python mission/build.py</code>.</p>'; return; }

// --- palette & helpers -------------------------------------------------------
const PALETTE = ['#22d3ee', '#a78bfa', '#a3e635', '#fb923c', '#f472b6', '#facc15', '#60a5fa', '#34d399'];
const rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
const RED = [255, 59, 78], GREY = [107, 114, 128], WHITE = [255, 255, 255];
const crewHex = c => PALETTE[(c - 1) % PALETTE.length];
const crewRgb = c => rgb(crewHex(c));
const clamp01 = x => Math.max(0, Math.min(1, x));
const ease = x => { x = clamp01(x); return x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
const lerp = (a, b, s) => a + (b - a) * s;
const mix = (a, b, s) => a.map((v, i) => Math.round(lerp(v, b[i], s)));
const hhmm = t => { t = Math.max(0, t); return `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor(t % 3600 / 60)).padStart(2, '0')}`; };
const title = s => s.toLowerCase().replace(/\b\w/g, m => m.toUpperCase());
const $ = id => document.getElementById(id);

// --- derived data -------------------------------------------------------------
const meta = M.meta, metrics = M.metrics, ev = M.event;
const jobs = M.jobs;
const crews = M.crews;
const crewById = Object.fromEntries(crews.map(c => [c.crew, c]));
const outCrew = ev.event === 'crew_out' || ev.event === 'crew_partial' ? ev.crew : null;
const safetyJobs = jobs.filter(j => j.safety && j.am);
const SAFETY = meta.safety_open;
const FIFO_SAFE = metrics.fifo.safety;
const UNSERVED = SAFETY - FIFO_SAFE;
const doneJobs = jobs.filter(j => j.done != null).sort((a, b) => a.done - b.done);
const lastSafety = Math.max(...jobs.filter(j => j.safety && j.noon).map(j => j.done));
const moved = jobs.filter(j => j.status === 'moved');
const dropped = jobs.filter(j => j.status === 'dropped');
const tripByCrew = Object.fromEntries(M.trips.map(t => [t.crew, t]));
const T_PLAN = 7 * 3600, T_SICK = meta.sick_call, T_ROLL = meta.day_start - 600, T_END = meta.day_end;
const noonCount = c => jobs.filter(j => j.noon === c).length;
const eventText = ev.event === 'crew_out' ? `Crew ${ev.crew} called in sick. Out for the day.`
  : ev.event === 'crew_partial' ? `Crew ${ev.crew} is short-handed: ${Math.round(ev.capacity * 100)}% capacity.`
  : 'No disruption today.';
const movedTo = [...new Set(moved.map(j => j.noon))];

// bezier "comet" paths for reassigned jobs: job location -> receiving crew's base, arcing up
const comets = moved.map(j => {
  const a = [j.lon, j.lat], b = crewById[j.noon].base;
  const km = Math.hypot((b[0] - a[0]) * 70, (b[1] - a[1]) * 111);
  const path = [], ts = [];
  for (let i = 0; i <= 48; i++) {
    const s = i / 48;
    path.push([lerp(a[0], b[0], s), lerp(a[1], b[1], s), Math.sin(Math.PI * s) * km * 320]);
    ts.push(s * 1000);
  }
  return { job: j, path, ts, color: crewRgb(j.noon) };
});

// --- scenes -------------------------------------------------------------------
const SCENES = [
  { key: 'intro', label: 'City', dur: 8, step: 'The problem',
    title: `Calgary has ${meta.open} open road & waste problems`,
    sub: `${SAFETY} are safety hazards: potholes and missing or damaged signs. ${crews.length} crews, ${metrics['8am'].n} job slots today.` },
  { key: 'fifo', label: 'Oldest-first', dur: 9, step: 'Today’s default: oldest ticket first',
    title: `Oldest-first leaves ${UNSERVED} hazards behind`,
    sub: `Crews take tickets in the order they arrived. Only ${FIFO_SAFE} of ${SAFETY} hazards get a crew today. The red beacons wait.` },
  { key: 'agent', label: 'Agent plan', dur: 9, step: 'Our priority agent',
    title: `The agent covers all ${metrics['8am'].safety} of ${SAFETY}`,
    sub: 'Same crews, same slots. Tickets are ranked by hazard, days waiting and repeat reports, then split by zone.' },
  { key: 'sick', label: 'Sick call', dur: 12, step: `Disruption · ${hhmm(T_SICK)}`,
    title: `Crew ${ev.crew} is out. Replan.`,
    sub: `${metrics.noon.moved} safety jobs move to the nearest crew with room; ${metrics.noon.dropped} lower-priority jobs are deferred. Safety jobs lost: ${metrics.noon.safety_dropped}.` },
  { key: 'day', label: 'Run the day', dur: 34, step: 'Execution',
    title: 'Crews roll out on the replanned routes',
    sub: 'Real Calgary road routes, nearest job first. Watch each hazard beacon turn green as it is fixed.' },
  { key: 'final', label: 'Results', dur: null, step: 'Result',
    title: `All ${metrics.noon.safety} hazards fixed by ${hhmm(lastSafety)}`,
    sub: `Even with a crew down. Oldest-first would have left ${UNSERVED} waiting with every crew on the road.` },
];
const SI = Object.fromEntries(SCENES.map((s, i) => [s.key, i]));

const S = {
  scene: 0, sceneAt: performance.now(), t: T_PLAN, playing: true, pitch: false, speed: 1,
  orbit: true, idleUntil: 0, chase: null, uiHidden: false, last: performance.now(), feedN: -1, finaleShown: false,
};
const FREEZE = new URLSearchParams(location.search).get('p');  // ?shot&p=6 freezes scene time (screenshots)
const secs = () => FREEZE != null ? +FREEZE : (performance.now() - S.sceneAt) / 1000;
const sceneKey = () => SCENES[S.scene].key;
const afterSick = () => S.scene > SI.sick || (S.scene === SI.sick && secs() > 1);

// --- map ------------------------------------------------------------------------
const lons = jobs.map(j => j.lon), lats = jobs.map(j => j.lat);
const BOUNDS = [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]];
const DOWNTOWN = [-114.0665, 51.0465];
const FALLBACK_STYLE = { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#05070d' } }] };

const map = new maplibregl.Map({
  container: 'map', style: 'https://tiles.openfreemap.org/styles/dark',
  center: DOWNTOWN, zoom: 15.3, pitch: 72, bearing: -28, maxPitch: 85,
  attributionControl: { compact: true }, antialias: true, fadeDuration: 0,
  preserveDrawingBuffer: /shot/.test(location.search),  // ?shot: lets headless screenshots capture the canvas
});
let styleFailed = false;
map.on('error', e => {
  if (!styleFailed && !map.isStyleLoaded() && /style|fetch|Failed/i.test(String(e.error && e.error.message))) {
    styleFailed = true; map.setStyle(FALLBACK_STYLE);
  }
});

const ROAD_TINT = {  // navy road grid so the city reads at overview zoom
  highway_minor: '#111a2b', highway_major_subtle: '#1b2a46', highway_motorway_subtle: '#25395f',
  highway_major_casing: 'rgba(70,105,160,0.55)', highway_motorway_casing: 'rgba(90,130,195,0.65)',
  highway_major_inner: '#0e1626', highway_path: '#0d1422',
};
map.on('style.load', () => {
  if (styleFailed) return;
  const layers = map.getStyle().layers;
  for (const l of layers) {  // moodier base: deep navy water, near-black land
    if (l.type === 'background') map.setPaintProperty(l.id, 'background-color', '#060a12');
    if (/water/.test(l.id) && l.type === 'fill') map.setPaintProperty(l.id, 'fill-color', '#0a1a2e');
    if (ROAD_TINT[l.id]) map.setPaintProperty(l.id, 'line-color', ROAD_TINT[l.id]);
    if (l.id === 'building' || (l['source-layer'] === 'building' && l.type === 'fill')) map.setLayoutProperty(l.id, 'visibility', 'none');
  }
  const firstLine = (layers.find(l => l.type === 'line') || {}).id;
  try {  // terrain relief (Bow & Elbow valleys, Nose Hill) as shading only - keeps deck layers on flat ground
    map.addSource('dem', { type: 'raster-dem', encoding: 'terrarium', tileSize: 256, maxzoom: 13,
      tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'] });
    map.addLayer({ id: 'relief', type: 'hillshade', source: 'dem', paint: {
      'hillshade-exaggeration': 0.45, 'hillshade-shadow-color': '#000000',
      'hillshade-highlight-color': '#1c2b45', 'hillshade-accent-color': '#0b1220' } }, firstLine);
  } catch (e) { console.warn('relief', e); }
  const src = Object.keys(map.getStyle().sources).find(k => map.getStyle().sources[k].type === 'vector');
  if (src) map.addLayer({
    id: 'towers', type: 'fill-extrusion', source: src, 'source-layer': 'building', minzoom: 12,
    paint: {
      'fill-extrusion-color': ['interpolate', ['linear'], ['coalesce', ['get', 'render_height'], 8],
        0, '#0f1626', 30, '#16213a', 90, '#21345a', 200, '#33528a'],
      'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 8],
      'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
      'fill-extrusion-opacity': 0.95,
      'fill-extrusion-vertical-gradient': true,
    },
  });
  map.setLight({ anchor: 'map', color: '#bcd4ff', intensity: 0.42, position: [1.4, 210, 40] });
});

const overlay = new deck.MapboxOverlay({
  interleaved: true, layers: [],
  getTooltip: ({ object }) => object && object.id && object.type ? {
    html: tooltip(object), className: 'deck-tooltip', style: {} } : null,
});
map.addControl(overlay);

['mousedown', 'wheel', 'touchstart', 'dragstart'].forEach(e => map.on(e, () => {
  S.idleUntil = performance.now() + 9000;
}));

function tooltip(j) {
  const crew = sceneKey() === 'fifo' ? j.fifo : (afterSick() ? j.noon : j.am);
  const status = j.status === 'dropped' && afterSick() ? '<span style="color:#9ca3af">Deferred by replan</span>'
    : j.status === 'moved' && afterSick() ? `Moved from Crew ${j.am} → Crew ${j.noon}` : '';
  const when = j.done != null ? (S.t >= j.done ? `Fixed ${hhmm(j.done)}` : `ETA ${hhmm(j.arrive)}`) : '';
  return `<div style="font-weight:700;font-size:13px">${j.safety ? '⚠ ' : ''}${j.type}</div>
    <div style="color:#8b97b3">${title(j.community)} · ${j.id}</div>
    <div style="margin-top:6px;font-family:'JetBrains Mono',monospace">P ${j.P.toFixed(2)}${crew ? ` · Crew ${crew}` : ''}</div>
    ${status ? `<div style="margin-top:4px">${status}</div>` : ''}${when ? `<div style="margin-top:4px;color:#3ee08f">${when}</div>` : ''}`;
}

// --- camera -----------------------------------------------------------------------
function overview(extra = {}) {
  const cam = map.cameraForBounds(BOUNDS, { padding: { top: 90, bottom: 150, left: 200, right: 290 } }) || { center: [-114.07, 51.03], zoom: 10.6 };
  return { center: cam.center, zoom: Math.min(cam.zoom, 11.2) + 0.3, pitch: 52, bearing: map.getBearing(), ...extra };
}
function cameraFor(key) {
  S.chase = null;
  if (key === 'intro') {
    map.jumpTo({ center: DOWNTOWN, zoom: 15.3, pitch: 72, bearing: -28 });
    setTimeout(() => { if (sceneKey() === 'intro') map.flyTo({ ...overview({ bearing: 12 }), duration: 6500, curve: 1.6, essential: true }); }, 1400);
  } else if (key === 'sick' && outCrew) {
    const a = crewById[outCrew].base, b = crewById[movedTo[0] || outCrew].base;
    map.flyTo({ center: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], zoom: 11.5, pitch: 58, bearing: map.getBearing() + 20, duration: 2600, essential: true });
  } else {
    map.flyTo({ ...overview(), duration: 2600, essential: true });
  }
}

// --- truck positions ------------------------------------------------------------------
function truckPos(crew, t) {
  const trip = tripByCrew[crew];
  if (!trip) return crewById[crew].base;
  const { path, ts } = trip;
  if (t <= ts[0]) return path[0];
  if (t >= ts[ts.length - 1]) return path[path.length - 1];
  let lo = 0, hi = ts.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (ts[m] <= t) lo = m; else hi = m; }
  const s = (t - ts[lo]) / ((ts[hi] - ts[lo]) || 1);
  return [lerp(path[lo][0], path[hi][0], s), lerp(path[lo][1], path[hi][1], s)];
}
function crewState(c, t) {
  if (c === outCrew && afterSick()) return { text: ev.event === 'crew_out' ? 'Out sick · jobs reassigned' : 'Reduced capacity', cls: 'out' };
  const mine = jobs.filter(j => (afterSick() ? j.noon : j.am) === c);
  const doneN = mine.filter(j => j.done != null && t >= j.done).length;
  if (S.scene < SI.day || t < meta.day_start) return { text: `Ready at base · ${mine.length} jobs`, doneN, n: mine.length };
  const on = mine.find(j => t >= j.arrive && t < j.done);
  if (on) return { text: `On site · ${on.type}, ${title(on.community)}`, doneN, n: mine.length };
  const next = mine.filter(j => j.arrive > t).sort((a, b) => a.arrive - b.arrive)[0];
  if (next) return { text: `En route → ${title(next.community)}`, doneN, n: mine.length };
  return { text: 'All jobs done · heading back', doneN, n: mine.length };
}

// --- layers -------------------------------------------------------------------------------
const routeData = name => Object.entries(M.routes[name] || {}).map(([c, path]) => ({ c: +c, path }));
const ROUTES = { fifo: routeData('fifo'), am: routeData('am'), noon: routeData('noon') };

function routeLayers(id, data, opacity, colorFn, glow = 1) {
  if (opacity <= 0.01 || !data.length) return [];
  const color = colorFn || (d => crewRgb(d.c));
  return [
    new deck.PathLayer({ id: id + '-glow', data, getPath: d => d.path, getColor: d => [...color(d), 46], getWidth: 9 * glow,
      widthUnits: 'pixels', capRounded: true, jointRounded: true, opacity, updateTriggers: { getColor: color } }),
    new deck.PathLayer({ id: id + '-core', data, getPath: d => d.path, getColor: d => [...color(d), 210], getWidth: 1.6,
      widthUnits: 'pixels', capRounded: true, jointRounded: true, opacity, updateTriggers: { getColor: color } }),
  ];
}

function buildLayers(now) {
  const key = sceneKey(), p = secs(), t = S.t, pulse = (Math.sin(now / 260) + 1) / 2, frame = Math.floor(now / 33);
  const L = [];
  const inDay = S.scene >= SI.day;

  // 1. the backlog: every open dispatchable problem
  L.push(new deck.ScatterplotLayer({
    id: 'backlog', data: M.backlog, getPosition: d => [d[0], d[1]], getRadius: 55, radiusMinPixels: 1.6,
    getFillColor: d => d[2] ? [255, 120, 130, 150] : [170, 190, 230, 110],
    opacity: key === 'intro' ? ease(p / 2.5) : inDay ? 0.35 : 0.6,
  }));

  // 2. planned routes, cross-faded between plans
  if (key === 'fifo') L.push(...routeLayers('r-fifo', ROUTES.fifo, ease(p / 1.5)));
  if (key === 'agent') {
    L.push(...routeLayers('r-fifo', ROUTES.fifo, 1 - ease(p / 1.2)));
    L.push(...routeLayers('r-am', ROUTES.am, ease((p - 0.6) / 1.5)));
  }
  if (key === 'sick') {
    const outAlpha = p < 4 ? 1 : 1 - ease((p - 4) / 1.5);
    const swap = ease((p - 6.5) / 1.8);
    L.push(...routeLayers('r-am', ROUTES.am.filter(d => d.c !== outCrew), 1 - swap));
    L.push(...routeLayers('r-noon', ROUTES.noon, swap));
    L.push(...routeLayers('r-out', ROUTES.am.filter(d => d.c === outCrew), outAlpha,
      () => p < 1 ? crewRgb(outCrew) : (pulse > .5 ? RED : [120, 20, 30]), 1.6));
  }
  if (inDay) L.push(...routeLayers('r-noon', ROUTES.noon, key === 'final' ? 0.45 : 0.32));

  // 3. hazard beacons (3D columns)
  L.push(new deck.ColumnLayer({
    id: 'hazards', data: safetyJobs, diskResolution: 20, radius: 190, extruded: true, pickable: true,
    getPosition: j => [j.lon, j.lat], getElevation: j => hazElev(j, key, p, t, pulse), getFillColor: j => hazColor(j, key, p, t, pulse),
    material: { ambient: 0.75, diffuse: 0.55, shininess: 48, specularColor: [255, 255, 255] },
    updateTriggers: { getElevation: frame, getFillColor: frame },
  }));

  // 4. job markers
  const jobData = jobs.filter(j => key === 'intro' ? false : key === 'fifo' ? j.fifo : (j.am || j.noon));
  L.push(new deck.ScatterplotLayer({
    id: 'jobs', data: jobData, pickable: true, stroked: true, radiusUnits: 'pixels', lineWidthUnits: 'pixels',
    getPosition: j => [j.lon, j.lat], getRadius: j => j.safety ? 5.5 : 4,
    getFillColor: j => jobFill(j, key, p, t), getLineColor: j => jobLine(j, key, p, t), getLineWidth: 1.5,
    updateTriggers: { getFillColor: frame, getLineColor: frame },
  }));

  // 5. deferred / moved labels
  if (key === 'sick' && p > 5 || inDay) {
    const show = [...dropped.map(j => ({ j, txt: 'DEFERRED', c: [156, 163, 175] })),
      ...moved.map(j => ({ j, txt: `→ CREW ${j.noon}`, c: crewRgb(j.noon) }))];
    L.push(new deck.TextLayer({
      id: 'tags', data: show, getPosition: d => [d.j.lon, d.j.lat], getText: d => d.txt, getColor: d => [...d.c, 235],
      getSize: 11, getPixelOffset: [0, 17], fontFamily: 'Inter, sans-serif', fontWeight: 700, characterSet: 'auto',
      fontSettings: { sdf: true }, outlineWidth: 4, outlineColor: [5, 7, 13, 230],
      opacity: inDay ? 0.55 : ease((p - 5) / 1),
    }));
  }

  if (key === 'sick' && p > 5 || inDay) L.push(new deck.ScatterplotLayer({
    id: 'deferred', data: dropped, stroked: true, filled: false, radiusUnits: 'pixels', lineWidthUnits: 'pixels',
    getPosition: j => [j.lon, j.lat], getRadius: 9, getLineColor: [156, 163, 175, 220], getLineWidth: 1.5,
    opacity: inDay ? 0.6 : ease((p - 5) / 1) }));

  // 6. reassignment comets
  if (key === 'sick' && p > 2.6) {
    const ct = (p - 2.6) / 3.2 * 1000;
    L.push(new deck.TripsLayer({ id: 'comets-glow', data: comets, getPath: d => d.path, getTimestamps: d => d.ts,
      getColor: d => d.color, opacity: 0.35, widthMinPixels: 14, capRounded: true, trailLength: 380, currentTime: ct, fadeTrail: true }));
    L.push(new deck.TripsLayer({ id: 'comets', data: comets, getPath: d => d.path, getTimestamps: d => d.ts,
      getColor: d => d.color, widthMinPixels: 3.5, capRounded: true, trailLength: 380, currentTime: ct, fadeTrail: true }));
    if (ct > 1000) L.push(new deck.ArcLayer({ id: 'links', data: moved, getSourcePosition: j => [j.lon, j.lat],
      getTargetPosition: j => crewById[j.noon].base, getSourceColor: crewRgb(outCrew), getTargetColor: j => crewRgb(j.noon),
      getWidth: 1.5, getHeight: 0.6, opacity: 0.5 * ease((ct - 1000) / 400) }));
  }

  // 7. driving trails
  if (inDay && t > meta.day_start) {
    L.push(new deck.TripsLayer({ id: 'trail-glow', data: M.trips, getPath: d => d.path, getTimestamps: d => d.ts,
      getColor: d => crewRgb(d.crew), opacity: 0.22, widthMinPixels: 13, capRounded: true, jointRounded: true,
      trailLength: 2700, currentTime: t, fadeTrail: true }));
    L.push(new deck.TripsLayer({ id: 'trail', data: M.trips, getPath: d => d.path, getTimestamps: d => d.ts,
      getColor: d => crewRgb(d.crew), widthMinPixels: 3.2, capRounded: true, jointRounded: true,
      trailLength: 2700, currentTime: t, fadeTrail: true }));
  }

  // 8. completion shockwaves
  if (inDay) {
    const waves = doneJobs.filter(j => t >= j.done && t < j.done + 1500);
    L.push(new deck.ScatterplotLayer({ id: 'waves', data: waves, stroked: true, filled: false, lineWidthUnits: 'pixels',
      getPosition: j => [j.lon, j.lat], getRadius: j => 120 + (t - j.done) / 1500 * (j.safety ? 1500 : 800),
      getLineColor: j => [...(j.safety ? [62, 224, 143] : WHITE), Math.round(255 * (1 - (t - j.done) / 1500))],
      getLineWidth: j => j.safety ? 3 : 1.5, updateTriggers: { getRadius: frame, getLineColor: frame } }));
  }

  // 9. crew bases + trucks
  const bases = crews.map(c => ({ ...c, out: c.crew === outCrew && afterSick() }));
  L.push(new deck.ScatterplotLayer({ id: 'bases', data: bases, stroked: true, filled: true, radiusUnits: 'pixels', lineWidthUnits: 'pixels',
    getPosition: c => c.base, getRadius: 10, getFillColor: c => [...(c.out ? GREY : crewRgb(c.crew)), 40],
    getLineColor: c => c.out ? (key === 'sick' && p < 4 && pulse > .5 ? RED : GREY) : crewRgb(c.crew), getLineWidth: 2,
    updateTriggers: { getFillColor: frame, getLineColor: frame } }));
  L.push(new deck.TextLayer({ id: 'base-labels', data: bases, getPosition: c => c.base,
    getText: c => c.out ? `CREW ${c.crew} · OUT` : `CREW ${c.crew} · ${c.zone}`,
    getColor: c => c.out ? [255, 110, 120, 240] : [...crewRgb(c.crew), 230], getSize: 11.5, getPixelOffset: [0, -22],
    fontFamily: 'Inter, sans-serif', fontWeight: 800, characterSet: 'auto', fontSettings: { sdf: true },
    outlineWidth: 4, outlineColor: [5, 7, 13, 230], opacity: inDay ? 0.6 : (key === 'intro' ? ease((p - 4) / 1.5) : 1),
    updateTriggers: { getText: frame, getColor: frame } }));

  if (inDay || key === 'sick' || key === 'agent') {
    const trucks = crews.filter(c => tripByCrew[c.crew] || (c.crew === outCrew)).map(c => ({
      c: c.crew, pos: c.crew === outCrew && afterSick() ? c.base : (inDay ? truckPos(c.crew, t) : c.base),
      out: c.crew === outCrew && afterSick() }));
    L.push(new deck.ScatterplotLayer({ id: 'truck-halo', data: trucks, radiusUnits: 'pixels', getPosition: d => d.pos,
      getRadius: d => d.out ? 0 : 16 + pulse * 6, getFillColor: d => [...crewRgb(d.c), 70],
      updateTriggers: { getPosition: frame, getRadius: frame } }));
    L.push(new deck.ScatterplotLayer({ id: 'trucks', data: trucks, radiusUnits: 'pixels', lineWidthUnits: 'pixels', stroked: true,
      getPosition: d => d.pos, getRadius: 7.5, getFillColor: d => d.out ? GREY : crewRgb(d.c), getLineColor: WHITE, getLineWidth: 2,
      updateTriggers: { getPosition: frame, getFillColor: frame } }));
  }
  return L;
}

function hazElev(j, key, p, t, pulse) {
  const base = 650, tall = 2600;
  if (key === 'intro') return tall * ease((p - 1.5 - (j.lat - BOUNDS[0][1]) * 6) / 2);
  if (key === 'fifo') return j.fifo ? lerp(tall, base, ease(p / 1.5)) : tall * (0.88 + 0.12 * pulse);
  if (key === 'agent') return j.fifo ? base : lerp(tall, base, ease((p - 1.4) / 1.4));
  if (key === 'sick') return base;
  return j.done != null && t >= j.done ? base * (1 - ease((t - j.done) / 900)) : base;
}
function hazColor(j, key, p, t, pulse) {
  if (key === 'intro') return [...RED, 230];
  if (key === 'fifo') return j.fifo ? [...mix(RED, crewRgb(j.fifo), ease(p / 1.5)), 230] : [...mix([150, 20, 35], RED, pulse), 240];
  if (key === 'agent') return [...(j.fifo ? crewRgb(j.am) : mix(RED, crewRgb(j.am), ease((p - 1.4) / 1.4))), 230];
  if (key === 'sick') return [...(j.status === 'moved' ? mix(crewRgb(j.am), crewRgb(j.noon), ease((p - 5.5) / 1)) : crewRgb(j.am)), 230];
  const c = crewRgb(j.noon || j.am);
  return [...(j.done != null && t >= j.done ? mix(c, [62, 224, 143], ease((t - j.done) / 400)) : c), 235];
}
function jobFill(j, key, p, t) {
  if (key === 'fifo') return [...crewRgb(j.fifo), 255];
  if (key === 'agent') return j.am ? [...crewRgb(j.am), 255] : [0, 0, 0, 0];
  if (key === 'sick') {
    if (j.status === 'dropped') return [...mix(crewRgb(j.am), GREY, ease((p - 5) / 1.2)), 255];
    if (j.status === 'moved') return [...mix(crewRgb(j.am), crewRgb(j.noon), ease((p - 5.5) / 1)), 255];
    return [...crewRgb(j.am), 255];
  }
  if (j.status === 'dropped') return [...GREY, 160];
  if (j.done != null && t >= j.done) return [235, 255, 245, 255];
  return [...crewRgb(j.noon || j.am), 255];
}
function jobLine(j, key, p, t) {
  if (S.scene >= SI.day && j.done != null && t >= j.done) return [...crewRgb(j.noon), 255];
  return [5, 7, 13, 255];
}

// --- HUD -------------------------------------------------------------------------------------
const btns = SCENES.map((s, i) => {
  const b = document.createElement('button');
  b.textContent = `${i + 1} ${s.label}`;
  b.onclick = () => { S.pitch = false; go(i); };
  $('scene-btns').appendChild(b);
  return b;
});
const speedBtns = [0.5, 1, 2, 4].map(v => {
  const b = document.createElement('button');
  b.textContent = `${v}×`;
  b.onclick = () => { S.speed = v; speedBtns.forEach(x => x.classList.toggle('on', x === b)); };
  $('speeds').appendChild(b);
  if (v === 1) b.classList.add('on');
  return b;
});
const scrub = $('scrub');
scrub.min = T_ROLL; scrub.max = T_END; scrub.step = 60;
scrub.oninput = () => {
  if (S.scene < SI.day) go(SI.day, { camera: false });
  S.t = +scrub.value; S.playing = false; S.pitch = false; S.finaleShown = false; syncPlay();
  $('finale').classList.add('hidden');
};
$('btn-play').onclick = () => togglePlay();
$('btn-pitch').onclick = () => startPitch();
$('btn-replay').onclick = () => startPitch();

const rosterRows = crews.map(c => {
  const row = document.createElement('div');
  row.className = 'crew';
  row.style.color = crewHex(c.crew);
  row.innerHTML = `<i class="sw"></i><div class="nm" style="color:var(--text)">Crew ${c.crew} <span>· ${c.zone}</span></div>
    <div class="ct"></div><div class="st"></div><div class="pb"><i></i></div>`;
  row.onclick = () => follow(c.crew);
  $('roster-rows').appendChild(row);
  return { c: c.crew, row, ct: row.querySelector('.ct'), st: row.querySelector('.st'), pb: row.querySelector('.pb i') };
});

$('fineprint').textContent = `Routes: ${meta.routing} (OSRM / OpenStreetMap) · Timeline illustrative: ${meta.service_min} min per job, ` +
  `drive time ×${meta.traffic_factor} · Plans & metrics: dispatch engine output`;

function setText(id, v) { const el = $(id); if (el.textContent !== String(v)) el.textContent = v; }

function updateHud(now) {
  const key = sceneKey(), p = secs(), t = S.t;
  // clock
  setText('clock-time', hhmm(t));
  setText('clock-label', S.scene < SI.sick ? 'Planning' : S.scene === SI.sick ? 'Disruption' : t < meta.day_start ? 'Rolling out' : t >= T_END - 1800 ? 'Shift complete' : 'Live');
  $('clock-bar').style.width = `${clamp01((t - T_PLAN) / (T_END - T_PLAN)) * 100}%`;

  // KPIs
  const cover = key === 'intro' ? 0 : key === 'fifo' ? Math.round(FIFO_SAFE * ease(p / 1.5))
    : key === 'agent' ? Math.round(lerp(FIFO_SAFE, metrics['8am'].safety, ease((p - 1.4) / 1.4))) : metrics.noon.safety;
  setText('kv-cover', cover);
  setText('kv-cover-of', `/${SAFETY}`);
  setText('kn-cover', key === 'fifo' ? `${UNSERVED} hazards left waiting` : key === 'intro' ? 'no plan yet' : 'every hazard has a crew');
  $('kv-cover').className = key === 'fifo' ? 'bad' : cover === SAFETY ? 'good' : '';
  const fixed = doneJobs.filter(j => j.safety && S.scene >= SI.day && t >= j.done).length;
  const jd = doneJobs.filter(j => S.scene >= SI.day && t >= j.done).length;
  setText('kv-fixed', fixed); setText('kv-fixed-of', `/${metrics.noon.safety}`);
  setText('kn-fixed', S.scene < SI.day || t < meta.day_start ? 'crews not yet rolling' : fixed === metrics.noon.safety ? `all done at ${hhmm(lastSafety)}` : 'and counting');
  $('kv-fixed').className = fixed && fixed === metrics.noon.safety ? 'good' : '';
  const nJobs = afterSick() ? metrics.noon.n : metrics['8am'].n;
  setText('kv-jobs', jd); setText('kv-jobs-of', `/${nJobs}`);
  setText('kn-jobs', `${afterSick() ? crews.length - (ev.event === 'crew_out' ? 1 : 0) : crews.length} crews on the road`);
  $('k-replan').classList.toggle('hidden', !(S.scene > SI.sick || (key === 'sick' && p > 7)));
  setText('kv-moved', metrics.noon.moved); setText('kv-dropped', metrics.noon.dropped); setText('kv-sdrop', metrics.noon.safety_dropped);
  setText('kn-replan', `Crew ${ev.crew} out · P ${metrics['8am'].P} → ${metrics.noon.P}`);

  // radio card
  const radioOn = key === 'sick';
  $('radio').classList.toggle('hidden', !radioOn);
  $('alert').classList.toggle('on', key === 'sick' && p < 4.2);
  if (radioOn) {
    setText('radio-time', hhmm(T_SICK));
    setText('radio-text', eventText);
    const res = $('radio-result');
    res.classList.toggle('hidden', p < 7.2);
    const html = `Replanned: <b>${metrics.noon.moved} moved</b> to Crew ${movedTo.join(', ')} · <b>${metrics.noon.dropped} deferred</b> · <b>${metrics.noon.safety_dropped} safety jobs lost</b>`;
    if (res.innerHTML !== html) res.innerHTML = html;
  }

  // roster (10 Hz is plenty)
  if (now - (updateHud.r || 0) > 100) {
    updateHud.r = now;
    for (const r of rosterRows) {
      const s = crewState(r.c, t);
      r.row.classList.toggle('out', s.cls === 'out');
      r.row.classList.toggle('following', S.chase === r.c);
      if (r.st.textContent !== s.text) r.st.textContent = s.text;
      const ct = s.cls === 'out' ? '—' : `${s.doneN}/${s.n}`;
      if (r.ct.textContent !== ct) r.ct.textContent = ct;
      r.pb.style.width = s.n ? `${s.doneN / s.n * 100}%` : '0';
    }
  }

  // feed
  const items = [];
  if (S.scene >= SI.sick) items.push({ t: T_SICK, html: `<b class="warn">Crew ${ev.crew}</b> out · replan: ${metrics.noon.moved} moved, ${metrics.noon.dropped} deferred` });
  if (S.scene >= SI.day) {
    if (t >= meta.day_start) items.push({ t: meta.day_start, html: `<b>${M.trips.length} crews</b> roll out` });
    for (const j of doneJobs) if (t >= j.done) items.push({ t: j.done,
      html: `<b style="color:${crewHex(j.noon)}">Crew ${j.noon}</b> fixed ${j.safety ? '<span class="warn">⚠ </span>' : ''}${j.type.toLowerCase()} · ${title(j.community)}` });
  }
  if (items.length !== S.feedN) {
    S.feedN = items.length;
    $('feed-list').innerHTML = items.sort((a, b) => b.t - a.t).slice(0, 4)
      .map(i => `<li><time>${hhmm(i.t)}</time><span>${i.html}</span></li>`).join('') || '<li><time>--:--</time><span style="color:var(--dim)">Waiting for the day to start</span></li>';
  }

  if (S.scene >= SI.day) scrub.value = t;
  btns.forEach((b, i) => b.classList.toggle('on', i === S.scene));
}

function setCaption() {
  const s = SCENES[S.scene], cap = $('caption');
  $('cap-step').textContent = `${S.scene + 1} / ${SCENES.length} · ${s.step}`;
  $('cap-title').textContent = s.title;
  $('cap-sub').textContent = s.sub;
  cap.classList.remove('swap'); void cap.offsetWidth; cap.classList.add('swap');
}

// --- control --------------------------------------------------------------------------------
function go(i, { camera = true } = {}) {
  S.scene = Math.max(0, Math.min(SCENES.length - 1, i));
  S.sceneAt = performance.now();
  const key = sceneKey();
  S.t = key === 'sick' ? T_SICK : key === 'day' ? T_ROLL : key === 'final' ? T_END : T_PLAN;
  if (key === 'final') S.t = T_END;
  S.playing = true; S.finaleShown = false; S.feedN = -1;
  $('finale').classList.add('hidden');
  setCaption(); syncPlay();
  if (camera) cameraFor(key);
  if (key === 'final') showFinale();
}
function next() { if (S.scene < SCENES.length - 1) go(S.scene + 1); }
function startPitch() { S.pitch = true; S.speed = 1; speedBtns.forEach(b => b.classList.toggle('on', b.textContent === '1×')); go(0); }
function togglePlay() { S.playing = !S.playing; if (S.playing && S.scene === SI.final) go(SI.day); syncPlay(); }
function syncPlay() { $('btn-play').textContent = S.playing ? '❚❚' : '▶'; $('btn-pitch').classList.toggle('on', S.pitch); }
function follow(c) {
  if (S.chase === c) { S.chase = null; map.flyTo({ ...overview(), duration: 2000 }); return; }
  S.chase = c;
  const pos = c === outCrew && afterSick() ? crewById[c].base : truckPos(c, S.t);
  map.flyTo({ center: pos, zoom: 15, pitch: 66, duration: 2200, essential: true });
  S.chaseFrom = performance.now() + 2300;
}
function showFinale() {
  if (S.finaleShown) return;
  S.finaleShown = true;
  $('fin-a').textContent = `${metrics.noon.safety}/${SAFETY}`;
  $('fin-al').textContent = `hazards fixed by ${hhmm(lastSafety)}, with only ${crews.length - 1} crews`;
  $('fin-b').textContent = UNSERVED;
  $('fin-bl').textContent = `hazards oldest-first would leave waiting, even with all ${crews.length} crews`;
  $('fin-c').textContent = metrics.noon.safety_dropped;
  $('fin-cl').textContent = `safety jobs lost when Crew ${ev.crew} went down`;
  $('finale').classList.remove('hidden');
}

addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' && e.key !== ' ') return;
  if (e.key === ' ') { e.preventDefault(); togglePlay(); }
  else if (e.key === 'ArrowRight') { S.pitch = false; next(); }
  else if (e.key === 'ArrowLeft') { S.pitch = false; go(S.scene - 1); }
  else if (/^[1-9]$/.test(e.key) && +e.key <= SCENES.length) { S.pitch = false; go(+e.key - 1); }
  else if (e.key === 'p' || e.key === 'P') startPitch();
  else if (e.key === 'c' || e.key === 'C') S.orbit = !S.orbit;
  else if (e.key === 'h' || e.key === 'H') { S.uiHidden = !S.uiHidden; $('ui').classList.toggle('hide', S.uiHidden); $('keys').style.opacity = S.uiHidden ? 0 : 1; }
  else if (e.key === 'Escape' && S.chase) follow(S.chase);
});

// --- main loop -----------------------------------------------------------------------------
const DAY_RATE = (T_END - T_ROLL) / SCENES[SI.day].dur;  // sim seconds per real second at 1x

function tick(now) {
  const dt = Math.min(0.1, (now - S.last) / 1000);
  S.last = now;
  const key = sceneKey(), s = SCENES[S.scene];

  if (key === 'day' && S.playing) {
    S.t = Math.min(T_END, S.t + dt * DAY_RATE * S.speed);
    if (S.t >= T_END) go(SI.final, { camera: S.pitch });
  } else if (S.pitch && s.dur && secs() > s.dur) {
    next();
  }
  // pitch mode: ride along with the crew closest to downtown for part of the day
  if (S.pitch && key === 'day') {
    const frac = (S.t - T_ROLL) / (T_END - T_ROLL);
    if (frac > 0.32 && frac < 0.52 && S.chase == null && !S.rode) { S.rode = true; follow(nearDowntown); }
    if (frac >= 0.52 && S.chase === nearDowntown && S.rode) follow(nearDowntown);
  }
  if (key === 'intro') S.rode = false;

  if (S.chase != null && now > (S.chaseFrom || 0) && !map.isMoving()) {
    const pos = S.chase === outCrew && afterSick() ? crewById[S.chase].base : truckPos(S.chase, S.t);
    map.jumpTo({ center: pos, bearing: map.getBearing() + dt * 6 });
  } else if (S.orbit && S.chase == null && now > S.idleUntil && !map.isMoving() && key !== 'intro') {
    map.setBearing(map.getBearing() + dt * (key === 'final' ? 3 : 1.6));
  }

  overlay.setProps({ layers: buildLayers(now) });
  updateHud(now);
  requestAnimationFrame(tick);
}
const nearDowntown = crews.filter(c => tripByCrew[c.crew])
  .sort((a, b) => Math.hypot(a.base[0] - DOWNTOWN[0], a.base[1] - DOWNTOWN[1]) - Math.hypot(b.base[0] - DOWNTOWN[0], b.base[1] - DOWNTOWN[1]))[0].crew;

window.mission = { go, startPitch, state: S };  // console hooks for rehearsal
setCaption(); syncPlay();
// #4 opens scene 4; #5@0.6 opens the day 60% through (for rehearsal and screenshots)
const hash = location.hash.match(/^#(\d)(?:@([\d.]+))?/);
let started = false;
function start() {  // don't wait for every tile: a slow venue network must not stall the show
  if (started) return;
  started = true;
  if (hash) {
    go(+hash[1] - 1);
    if (hash[2] && sceneKey() === 'day') { S.t = T_ROLL + (T_END - T_ROLL) * +hash[2]; S.playing = false; syncPlay(); }
  } else cameraFor('intro');
  S.sceneAt = performance.now(); requestAnimationFrame(tick);
}
map.once('style.load', start);
map.once('load', start);
setTimeout(start, 4000);
})();
