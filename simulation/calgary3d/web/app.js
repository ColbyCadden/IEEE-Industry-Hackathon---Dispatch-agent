import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

// ────────────────────────────────────────────────────────────
// CONFIG
// ────────────────────────────────────────────────────────────
const BACKEND = 'http://localhost:8765';
const USE_MOCK = new URLSearchParams(location.search).has('mock');

// ────────────────────────────────────────────────────────────
// DOM REFS
// ────────────────────────────────────────────────────────────
const container = document.getElementById('container');
const badge = document.getElementById('connection-badge');
const helpText = document.getElementById('help-text');
const panel = document.getElementById('control-panel');
const panelBody = document.getElementById('panel-body');
const panelToggle = document.getElementById('panel-toggle');

// ────────────────────────────────────────────────────────────
// STATE
// ────────────────────────────────────────────────────────────
let CEN = {x:0,y:0};
let sceneSpan = 1500;
let sceneData = null;          // loaded scene.json
let vehicles = new Map();      // id -> {x,y,angle,speed}
let prevVehicles = new Map();  // previous frame for interpolation
let tlsStates = {};
let closedRoads = new Set();
let simStats = { n: 0, meanSpeed: 0, halting: 0 };
let controlState = { playbackRate: 1, speedScale: 1, demandScale: 1, signalMode: 'normal', closeEdges: [] };
let simTime = 0;
let isNight = true;            // Mission Control look by default; toggle = blue hour
let isPaused = false;
let isMockMode = USE_MOCK;
let streamConnected = false;
let streamReconnectTimer = null;
let lastStreamFrame = null;
let streamInterp = 0;

// Three.js objects
let scene, camera, renderer, clock;
let flyControls, orbitControls;
let cameraMode = 'intro'; // 'intro' | 'fly' | 'orbit' | 'cinematic'
let composer, bloomPass, gridHelper;
let introT = 0;
const introFrom = new THREE.Vector3(), introTo = new THREE.Vector3();
let orbitIdleAt = 0;            // resume auto-rotate after the user stops dragging
const winUniform = { value: 1 }; // lit-window intensity (night 1, blue hour lower)
const speedHistory = [];
let cinOrbitAngle = 0;
let groundMesh, roadGroup, buildingGroup, parkGroup, waterGroup, signalGroup;
let labelGroup;
let carInstancedMesh;
let carData = [];          // per-instance: {id, x, y, angle, speed, active}
const MAX_CARS = 1000;
let carMatrix = new THREE.Matrix4();
let carColor = new THREE.Color();
let tmpQuat = new THREE.Quaternion();
let tmpPos = new THREE.Vector3();
let raycaster = new THREE.Raycaster();
let mouse = new THREE.Vector2();
const carDummy = new THREE.Object3D();

// Speed colors
const COLOR_STOPPED = new THREE.Color('#ff3b4e');  // red
const COLOR_SLOW = new THREE.Color('#facc15');     // yellow
const COLOR_FLOW = new THREE.Color('#3ee08f');     // green
const COLOR_PLAIN = new THREE.Color('#22d3ee');    // cyan for plain mode

// Road edge ID -> road object mapping for raycasting
let roadMeshMap = new Map();

// ────────────────────────────────────────────────────────────
// MOCK DATA & STREAM
// ────────────────────────────────────────────────────────────
let mockVehicles = [];
let mockTlsPhases = {};
let mockTime = 0;

function initMockVehicles() {
  if (!sceneData) return;
  mockVehicles = [];
  const roadShapes = sceneData.roads.map(r => r.shape);
  // Spawn vehicles on roads
  let id = 0;
  for (let ri = 0; ri < roadShapes.length; ri++) {
    const shape = roadShapes[ri];
    const perRoad = Math.max(1, Math.min(8, Math.round(700 / roadShapes.length)));  // ~700 cars on any network
    const numCars = Math.max(1, Math.round(perRoad * (0.5 + Math.random())));
    for (let c = 0; c < numCars; c++) {
      const seg = Math.floor(Math.random() * (shape.length - 1));
      const t = Math.random();
      const x = shape[seg][0] + (shape[seg+1][0] - shape[seg][0]) * t;
      const y = shape[seg][1] + (shape[seg+1][1] - shape[seg][1]) * t;
      const dx = shape[seg+1][0] - shape[seg][0];
      const dy = shape[seg+1][1] - shape[seg][1];
      const angle = Math.atan2(dx, dy); // clockwise from north: atan2(dx, dy)
      const speed = 5 + Math.random() * 10; // m/s
      mockVehicles.push({ id: id++, x, y, angle: angle * 180 / Math.PI, speed, roadIdx: ri, seg, t });
    }
  }
  // Init TLS phases
  for (const s of sceneData.signals) {
    mockTlsPhases[s.id] = { phase: 0, phases: ['GGrrrr', 'rrrrGG', 'yyrrrr', 'rrrryy'] };
  }
}

function updateMockStream(dt) {
  mockTime += dt * (isPaused ? 0 : controlState.playbackRate);
  for (const v of mockVehicles) {
    const shape = sceneData.roads[v.roadIdx].shape;
    v.t += v.speed * dt * controlState.speedScale / (Math.hypot(
      shape[v.seg+1][0] - shape[v.seg][0],
      shape[v.seg+1][1] - shape[v.seg][1]
    ) || 1);  // zero-length segments in real networks
    if (v.t > 1) {
      v.t -= 1;
      v.seg++;
      if (v.seg >= shape.length - 1) v.seg = 0;
    }
    const dx = shape[v.seg+1][0] - shape[v.seg][0];
    const dy = shape[v.seg+1][1] - shape[v.seg][1];
    v.x = shape[v.seg][0] + dx * v.t;
    v.y = shape[v.seg][1] + dy * v.t;
    v.angle = Math.atan2(dx, dy) * 180 / Math.PI;
    // Vary speed slightly
    v.speed = Math.max(0.5, v.speed + (Math.random() - 0.5) * 0.3);
  }
  // Update TLS phases
  for (const [id, tls] of Object.entries(mockTlsPhases)) {
    tls.phase += dt * (isPaused ? 0 : controlState.playbackRate);
    if (tls.phase > 30) tls.phase = 0;
    if (controlState.signalMode === 'allRed') {
      tlsStates[id] = 'rrrrrr';
    } else if (controlState.signalMode === 'allGreen') {
      tlsStates[id] = 'GGGGGG';
    } else if (controlState.signalMode === 'flashing') {
      tlsStates[id] = Math.floor(tls.phase * 2) % 2 === 0 ? 'GGGGGG' : 'rrrrrr';
    } else {
      const idx = Math.floor(tls.phase / 30 * tls.phases.length) % tls.phases.length;
      tlsStates[id] = tls.phases[idx];
    }
  }
  simTime = mockTime;
  simStats = { n: mockVehicles.length, meanSpeed: mockVehicles.reduce((s,v) => s+v.speed, 0) / Math.max(1, mockVehicles.length), halting: mockVehicles.filter(v => v.speed < 1).length };
  controlState.signalMode = controlState.signalMode; // preserve
}

function getMockStreamData() {
  return {
    t: mockTime,
    // mock cars live on the recentred roads; add CEN back because processStreamData subtracts it
    v: mockVehicles.map(v => [v.id, v.x + CEN.x, v.y + CEN.y, v.angle, v.speed]),
    tls: tlsStates,
    stats: {
      n: mockVehicles.length,
      meanSpeed: mockVehicles.reduce((s,v) => s+v.speed,0) / Math.max(1, mockVehicles.length),
      halting: mockVehicles.filter(v => v.speed < 1).length,
      control: controlState
    }
  };
}

// ────────────────────────────────────────────────────────────
// FETCH / SSE
// ────────────────────────────────────────────────────────────
async function loadSceneData() {
  if (isMockMode) return loadOfflineScene();
  try {
    const resp = await fetch(BACKEND + '/scene.json');
    if (!resp.ok) throw new Error('Backend not available');
    return resp.json();
  } catch (e) {
    console.warn('Backend unavailable, switching to mock');
    isMockMode = true;
    setBadge('mock');
    return loadOfflineScene();
  }
}

// Offline: the real downtown network exported beside the page (scene.json), else the toy grid
async function loadOfflineScene() {
  try {
    const resp = await fetch('scene.json');
    if (resp.ok) return await resp.json();
  } catch (e) { /* fall through */ }
  const resp = await fetch('mock/scene.json');
  return resp.json();
}

function connectStream() {
  if (isMockMode) return;
  if (streamReconnectTimer) clearTimeout(streamReconnectTimer);

  try {
    const es = new EventSource(BACKEND + '/stream');
    es.onopen = () => {
      streamConnected = true;
      setBadge('connected');
    };
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        processStreamData(data);
      } catch (err) { console.warn('Stream parse error:', err); }
    };
    es.onerror = () => {
      streamConnected = false;
      es.close();
      setBadge('error');
      streamReconnectTimer = setTimeout(connectStream, 3000);
    };
    // Store reference for cleanup
    window._eventSource = es;
  } catch (e) {
    console.warn('SSE connection failed, using mock');
    isMockMode = true;
    setBadge('mock');
  }
}

function processStreamData(data) {
  lastStreamFrame = data;
  streamInterp = 0;
  if (data.stats) {
    simStats = data.stats;
    if (data.stats.control) controlState = data.stats.control;
  }
  simTime = data.t;
  if (data.tls) tlsStates = Object.assign(tlsStates || {}, data.tls);

  // Track previous positions for interpolation
  prevVehicles = new Map(vehicles);
  vehicles.clear();
  if (data.v) {
    for (const v of data.v) {
      vehicles.set(v[0], { id: v[0], x: v[1] - CEN.x, y: v[2] - CEN.y, angle: v[3], speed: v[4] });
    }
  }
}

async function postControl(changes) {
  Object.assign(controlState, changes);
  if (isMockMode) return;
  try {
    const resp = await fetch(BACKEND + '/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes)
    });
    if (resp.ok) {
      const state = await resp.json();
      Object.assign(controlState, state);
    }
  } catch (e) { console.warn('POST /control failed:', e); }
}

function setBadge(type) {
  badge.className = '';
  switch (type) {
    case 'connecting': badge.className = 'badge-connecting'; badge.textContent = '● Connecting'; break;
    case 'connected': badge.className = 'badge-connected'; badge.textContent = '● Connected'; break;
    case 'mock': badge.className = 'badge-mock'; badge.textContent = '◆ Mock Mode'; break;
    case 'error': badge.className = 'badge-error'; badge.textContent = '● Disconnected'; break;
  }
}

// ────────────────────────────────────────────────────────────
// THREE.JS SCENE
// ────────────────────────────────────────────────────────────
function initThree() {
  // Renderer
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false,
    preserveDrawingBuffer: new URLSearchParams(location.search).has('shot') });  // ?shot: headless screenshots
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  container.appendChild(renderer.domElement);

  // Scene: night sky gradient + navy haze (the Mission Control look)
  scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x0b1426, 600, 2500);
  scene.background = skyTexture(true);

  // Camera
  camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 5, 5000);
  camera.position.set(400, 350, 400);
  camera.lookAt(0, 0, 0);

  window.__dbg = { scene, camera, get bg(){return buildingGroup;}, get roadMeshMap(){return roadMeshMap;}, get roadGroup(){return roadGroup;}, get CEN(){return CEN;}, get sceneData(){return sceneData;}, get originalRoadMaterial(){ return window.__roadMat || null; } };
  // Lighting: cool moonlight; the city's own glow (windows, cars, signals) does the rest
  const ambient = new THREE.AmbientLight(0x3a4a6e, 0.9);
  scene.add(ambient);

  const sun = new THREE.DirectionalLight(0x9fb8ff, 1.4);
  sun.position.set(300, 500, 200);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.near = 10;
  sun.shadow.camera.far = 2000;
  sun.shadow.camera.left = -600;
  sun.shadow.camera.right = 600;
  sun.shadow.camera.top = 600;
  sun.shadow.camera.bottom = -600;
  sun.shadow.bias = -0.0001;
  scene.add(sun);
  scene.userData.sun = sun;
  scene.userData.ambient = ambient;

  const hemi = new THREE.HemisphereLight(0x2a4170, 0x05070d, 0.6);
  scene.add(hemi);
  scene.userData.hemi = hemi;

  // Ground
  const groundGeo = new THREE.PlaneGeometry(3000, 3000);
  const groundMat = new THREE.MeshStandardMaterial({ color: 0x070b13, roughness: 1 });
  groundMesh = new THREE.Mesh(groundGeo, groundMat);
  groundMesh.rotation.x = -Math.PI / 2;
  groundMesh.position.y = -0.2;
  groundMesh.receiveShadow = true;
  scene.add(groundMesh);

  // Groups
  roadGroup = new THREE.Group();
  buildingGroup = new THREE.Group();
  parkGroup = new THREE.Group();
  waterGroup = new THREE.Group();
  signalGroup = new THREE.Group();
  labelGroup = new THREE.Group();
  scene.add(roadGroup);
  scene.add(buildingGroup);
  scene.add(parkGroup);
  scene.add(waterGroup);
  scene.add(signalGroup);
  scene.add(labelGroup);

  // Fly controls (custom)
  initFlyControls();

  // Orbit controls
  orbitControls = new OrbitControls(camera, renderer.domElement);
  orbitControls.target.set(0, 0, 0);
  orbitControls.enableDamping = true;
  orbitControls.dampingFactor = 0.1;
  orbitControls.minDistance = 20;
  orbitControls.maxDistance = 1500;
  orbitControls.maxPolarAngle = Math.PI / 2 - 0.05;
  orbitControls.enabled = false;
  orbitControls.autoRotateSpeed = 0.35;
  orbitControls.addEventListener('start', () => { orbitControls.autoRotate = false; orbitIdleAt = performance.now() + 12000; });

  // Bloom: anything bright (windows, cars, signals) glows
  composer = new EffectComposer(renderer);
  composer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  composer.addPass(new RenderPass(scene, camera));
  bloomPass = new UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 0.85, 0.55, 0.72);
  composer.addPass(bloomPass);
  composer.addPass(new OutputPass());

  // Car InstancedMesh
  const carGeo = createCarGeometry();
  const carMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.35, metalness: 0.2 });
  carMat.onBeforeCompile = (sh) => {
    sh.fragmentShader = sh.fragmentShader.replace('#include <emissivemap_fragment>',
      '#include <emissivemap_fragment>\n#ifdef USE_COLOR\n  totalEmissiveRadiance += vColor.rgb * 1.15;\n#endif');
  };
  carInstancedMesh = new THREE.InstancedMesh(carGeo, carMat, MAX_CARS);
  carInstancedMesh.frustumCulled = false;
  carInstancedMesh.castShadow = true;
  carInstancedMesh.receiveShadow = true;
  carInstancedMesh.count = 0;
  carInstancedMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  scene.add(carInstancedMesh);

  // Resize handler
  window.addEventListener('resize', onResize);

  // Pointer lock
  renderer.domElement.addEventListener('click', onCanvasClick);
  document.addEventListener('pointerlockchange', onPointerLockChange);

  // Keyboard
  document.addEventListener('keydown', onKeyDown);
  document.addEventListener('keyup', onKeyUp);
  document.addEventListener('wheel', onWheel);
  document.addEventListener('mousemove', onMouseMove);
}

function ribbonGeo(pts, half, y) {
  const pos = [], idx = [];
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const p = pts[i];
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(n - 1, i + 1)];
    let dx = b.x - a.x, dz = b.z - a.z;
    const l = Math.hypot(dx, dz) || 1; dx /= l; dz /= l;
    const nx = -dz, nz = dx;
    pos.push(p.x + nx * half, y, p.z + nz * half, p.x - nx * half, y, p.z - nz * half);
    if (i < n - 1) { const k = i * 2; idx.push(k, k + 2, k + 1, k + 1, k + 2, k + 3); }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  // ensure upward normals
  const nn = g.attributes.normal;
  for (let i = 0; i < nn.count; i++) if (nn.getY(i) < 0) nn.setXYZ(i, -nn.getX(i), -nn.getY(i), -nn.getZ(i));
  return g;
}

function createCarGeometry() {
  // Simple box car with cab
  const group = new THREE.BoxGeometry(1.8, 0.6, 4.2);
  group.translate(0, 0.6, 0);
  // Cab
  const cab = new THREE.BoxGeometry(1.7, 0.5, 1.8);
  cab.translate(0, 1.15, -0.3);
  // Merge
  const merged = mergeGeometries([group, cab]);
  return merged;
}

function mergeGeometries(geos) {
  const merged = new THREE.BufferGeometry();
  let positions = [];
  let normals = [];
  let uvs = [];
  let indices = [];
  let idxOffset = 0;

  for (const g of geos) {
    const pos = g.getAttribute('position').array;
    const norm = g.getAttribute('normal').array;
    const uv = g.getAttribute('uv') ? g.getAttribute('uv').array : null;
    const idx = g.getIndex() ? g.getIndex().array : null;

    const vertCount = pos.length / 3;
    for (let i = 0; i < pos.length; i++) positions.push(pos[i]);
    for (let i = 0; i < norm.length; i++) normals.push(norm[i]);
    if (uv) for (let i = 0; i < uv.length; i++) uvs.push(uv[i]);
    else for (let i = 0; i < vertCount * 2; i++) uvs.push(0);

    if (idx) {
      for (let i = 0; i < idx.length; i++) indices.push(idx[i] + idxOffset);
    } else {
      for (let i = 0; i < vertCount; i++) indices.push(i + idxOffset);
    }
    idxOffset += vertCount;
    g.dispose();
  }

  merged.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  merged.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  merged.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  merged.setIndex(indices);
  merged.computeVertexNormals();
  return merged;
}

// ────────────────────────────────────────────────────────────
// FLY CONTROLS
// ────────────────────────────────────────────────────────────
const keys = {};
let flySpeed = 80;
let pitch = 0, yaw = Math.PI / 4;
const euler = new THREE.Euler(0, 0, 0, 'YXZ');

function initFlyControls() {
  // Initial look direction
  const dir = new THREE.Vector3();
  camera.getWorldDirection(dir);
  pitch = Math.asin(-dir.y);
  yaw = Math.atan2(-dir.x, -dir.z);
}

function updateFlyControls(dt) {
  if (cameraMode !== 'fly' || !document.pointerLockElement) return;

  const speed = flySpeed * (keys['Shift'] ? 3 : 1);
  const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
  forward.y = 0;
  forward.normalize();
  const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
  right.y = 0;
  right.normalize();

  if (keys['KeyW']) camera.position.addScaledVector(forward, speed * dt);
  if (keys['KeyS']) camera.position.addScaledVector(forward, -speed * dt);
  if (keys['KeyA']) camera.position.addScaledVector(right, -speed * dt);
  if (keys['KeyD']) camera.position.addScaledVector(right, speed * dt);
  if (keys['KeyQ'] || keys['Space']) camera.position.y += speed * dt;
  if (keys['KeyE'] || keys['ControlLeft'] || keys['ControlRight']) camera.position.y -= speed * dt;

  // Min altitude
  if (camera.position.y < 3) camera.position.y = 3;

  // Mouse look handled by pointerlock event
}

function onMouseMove(e) {
  if (!document.pointerLockElement || cameraMode !== 'fly') return;
  const sensitivity = 0.002;
  yaw -= e.movementX * sensitivity;
  pitch -= e.movementY * sensitivity;
  pitch = Math.max(-Math.PI / 2 + 0.01, Math.min(Math.PI / 2 - 0.01, pitch));

  euler.set(pitch, yaw, 0);
  camera.quaternion.setFromEuler(euler);
}

function onWheel(e) {
  flySpeed = Math.max(10, Math.min(500, flySpeed + e.deltaY * -0.1));
}

// ────────────────────────────────────────────────────────────
// BUILD SCENE FROM DATA
// ────────────────────────────────────────────────────────────
function buildScene() {
  if (!sceneData) return;

  // Clear
  while (roadGroup.children.length) roadGroup.remove(roadGroup.children[0]);
  while (buildingGroup.children.length) buildingGroup.remove(buildingGroup.children[0]);
  while (parkGroup.children.length) parkGroup.remove(parkGroup.children[0]);
  while (waterGroup.children.length) waterGroup.remove(waterGroup.children[0]);
  while (signalGroup.children.length) signalGroup.remove(signalGroup.children[0]);
  roadMeshMap.clear();

  // Roads
  const roadMat = new THREE.MeshStandardMaterial({ color: 0x1a2436, roughness: 0.85, side: THREE.DoubleSide });
  // Publish the pristine white material so the congestion layer can restore
  // it exactly (reading it back off a mesh would return an already-recoloured
  // clone). Declared here because __dbg is built earlier in init().
  window.__roadMat = roadMat;
  const roadOutlineMat = new THREE.MeshStandardMaterial({ color: 0x2a5d9a, emissive: 0x1d4f8f, emissiveIntensity: 0.9,
    roughness: 0.8, side: THREE.DoubleSide });

  for (const road of sceneData.roads) {
    const pts = road.shape.map(([x, y]) => new THREE.Vector3(x, 0, -y));
    const w = Math.max(road.width || 3, (road.lanes || 1) * 3.4, 6.5) * 1.25;
    const mesh = new THREE.Mesh(ribbonGeo(pts, w / 2, 0.15), roadMat);
    mesh.receiveShadow = true;
    mesh.userData = { edgeId: road.id, roadName: road.name, isRoad: true, shape: road.shape, width: w };
    roadGroup.add(mesh);
    roadMeshMap.set(road.id, mesh);

    const outline = new THREE.Mesh(ribbonGeo(pts, w / 2 + 1.0, 0.05), roadOutlineMat);
    roadGroup.add(outline);

    if (road.lanes >= 2) {
      const dashMat = new THREE.MeshBasicMaterial({ color: 0x1b7f99 });
      roadGroup.add(new THREE.Mesh(ribbonGeo(pts, 0.18, 0.2), dashMat));
    }
  }

  // Buildings (merged for performance)
  mergeBuildings();

  // Parks
  const parkMat = new THREE.MeshStandardMaterial({ color: 0x0e2a20, roughness: 0.95 });
  for (const ring of sceneData.parks) {
    const shape = new THREE.Shape();
    shape.moveTo(ring[0][0], ring[0][1]);
    for (let i = 1; i < ring.length; i++) shape.lineTo(ring[i][0], ring[i][1]);
    const geo = new THREE.ShapeGeometry(shape);
    const mesh = new THREE.Mesh(geo, parkMat);
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.y = 0.05;
    mesh.receiveShadow = true;
    parkGroup.add(mesh);

    // Add some trees
    addParkTrees(ring);
  }

  // Water
  const waterMat = new THREE.MeshStandardMaterial({ color: 0x0a2240, emissive: 0x061a33, roughness: 0.15, metalness: 0.6 });
  if (sceneData.water && sceneData.water.length > 0) {
    for (const ring of sceneData.water) {
      const shape = new THREE.Shape();
      shape.moveTo(ring[0][0], ring[0][1]);
      for (let i = 1; i < ring.length; i++) shape.lineTo(ring[i][0], ring[i][1]);
      const geo = new THREE.ShapeGeometry(shape);
      const mesh = new THREE.Mesh(geo, waterMat);
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.y = -0.1;
      mesh.receiveShadow = true;
      waterGroup.add(mesh);
    }
  }

  // Signals
  buildSignals();

  // ===== STREET LABELS =====
  buildStreetLabels();

  // Init mock vehicles after scene data is available
  if (isMockMode) initMockVehicles();
}

function mergeBuildings() {
  const geosToMerge = [];
  const bldgMat = towerMaterial();

  for (const b of sceneData.buildings) {
    const shape = new THREE.Shape();
    shape.moveTo(b.ring[0][0], b.ring[0][1]);
    for (let i = 1; i < b.ring.length; i++) shape.lineTo(b.ring[i][0], b.ring[i][1]);

    const extrudeSettings = { steps: 1, depth: b.h, bevelEnabled: false };
    const geo = new THREE.ExtrudeGeometry(shape, extrudeSettings);
    geo.rotateX(-Math.PI / 2);
    geo.computeVertexNormals();
    geosToMerge.push(geo);
  }

  if (geosToMerge.length > 0) {
    const merged = mergeGeometries(geosToMerge);
    const n = merged.getAttribute('normal');
    const wall = new Float32Array(n.count);
    for (let i = 0; i < n.count; i++) wall[i] = Math.abs(n.getY(i)) < 0.5 ? 1 : 0;
    merged.setAttribute('aWall', new THREE.BufferAttribute(wall, 1));
    const mesh = new THREE.Mesh(merged, bldgMat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    buildingGroup.add(mesh);
    // glowing wireframe edges: the "digital twin" outline that keeps towers readable at night
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(merged, 30),
      new THREE.LineBasicMaterial({ color: 0x4f8fe0, transparent: true, opacity: 0.42 }));
    buildingGroup.add(edges);
    buildingGroup.userData.edges = edges;
    buildingGroup.userData.mesh = mesh;
  }
}

// Navy towers that lighten with height, with a procedural grid of lit windows on the walls
// (warm offices, a few cool screens). Window brightness follows winUniform (night/blue hour).
function towerMaterial() {
  const mat = new THREE.MeshStandardMaterial({ color: 0x2b3d62, roughness: 0.6, metalness: 0.2 });
  mat.onBeforeCompile = (sh) => {
    sh.uniforms.uWin = winUniform;
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nattribute float aWall;\nvarying float vWall;\nvarying vec3 vWPos;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvWall = aWall;\nvWPos = (modelMatrix * vec4(position, 1.0)).xyz;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', `#include <common>
uniform float uWin;
varying float vWall;
varying vec3 vWPos;
float hash21(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }`)
      .replace('#include <color_fragment>', `#include <color_fragment>
diffuseColor.rgb *= mix(0.7, 1.7, clamp(vWPos.y / 140.0, 0.0, 1.0));
diffuseColor.rgb *= mix(0.6, 1.0, vWall);`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
{
  vec2 g = vec2((vWPos.x + vWPos.z) / 3.2, vWPos.y / 3.6);
  vec2 f = fract(g);
  float win = step(0.22, f.x) * step(f.x, 0.78) * step(0.3, f.y) * step(f.y, 0.78);
  win *= clamp(1.4 - max(fwidth(g.x), fwidth(g.y)) * 3.0, 0.0, 1.0);  // fade sub-pixel windows (no speckle)
  float r = hash21(floor(g) + floor(vWPos.xz / 37.0));
  float lit = step(0.58, r);
  vec3 wc = mix(vec3(1.0, 0.74, 0.42), vec3(0.5, 0.82, 1.0), step(0.9, r));
  totalEmissiveRadiance += vWall * win * lit * step(3.0, vWPos.y) * wc * uWin * (0.45 + 0.55 * fract(r * 7.0));
}`);
  };
  return mat;
}

// Vertical sky gradient with a faint horizon glow, as a screen-space background
function skyTexture(night) {
  const c = document.createElement('canvas');
  c.width = 16; c.height = 512;
  const g = c.getContext('2d');
  const grad = g.createLinearGradient(0, 0, 0, 512);
  if (night) {
    grad.addColorStop(0, '#02040a'); grad.addColorStop(0.55, '#07101f');
    grad.addColorStop(0.8, '#0f1d36'); grad.addColorStop(1, '#16284a');
  } else {
    grad.addColorStop(0, '#0b1a36'); grad.addColorStop(0.5, '#1f3c6e');
    grad.addColorStop(0.8, '#4a6fa5'); grad.addColorStop(1, '#e8a87c');
  }
  g.fillStyle = grad; g.fillRect(0, 0, 16, 512);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function addParkTrees(ring) {
  const trunkMat = new THREE.MeshStandardMaterial({ color: 0x1a1410, roughness: 0.8 });
  const leafMat = new THREE.MeshStandardMaterial({ color: 0x14503a, emissive: 0x06261b, roughness: 0.7 });

  // Calculate centroid
  let cx = 0, cy = 0;
  for (const [x, y] of ring) { cx += x; cy += y; }
  cx /= ring.length; cy /= ring.length;

  // Bounds
  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
  for (const [x, y] of ring) { minx = Math.min(minx, x); maxx = Math.max(maxx, x); miny = Math.min(miny, y); maxy = Math.max(maxy, y); }

  const numTrees = Math.floor((maxx - minx) * (maxy - miny) / 400);
  for (let i = 0; i < Math.min(numTrees, 30); i++) {
    const tx = minx + Math.random() * (maxx - minx);
    const ty = miny + Math.random() * (maxy - miny);
    const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.4, 3), trunkMat);
    trunk.position.set(tx, 1.5, -ty);
    trunk.castShadow = true;
    parkGroup.add(trunk);

    const leaves = new THREE.Mesh(new THREE.SphereGeometry(1.8, 6, 4), leafMat);
    leaves.position.set(tx, 3.8, -ty);
    leaves.castShadow = true;
    parkGroup.add(leaves);
  }
}

function buildSignals() {
  const postMat = new THREE.MeshStandardMaterial({ color: 0x1b2230, roughness: 0.6 });
  const headMat = new THREE.MeshStandardMaterial({ color: 0x0b0f17, roughness: 0.5 });

  for (const s of sceneData.signals) {
    const group = new THREE.Group();

    // Post
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.2, 4), postMat);
    post.position.y = 2;
    post.castShadow = true;
    group.add(post);

    // Signal head housing
    const head = new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.8, 0.3), headMat);
    head.position.y = 4.5;
    group.add(head);

    // 3 lights
    const lightGeo = new THREE.SphereGeometry(0.18, 8, 8);
    const redLight = new THREE.Mesh(lightGeo, new THREE.MeshStandardMaterial({ color: 0x330000, emissive: 0x330000, emissiveIntensity: 3 }));
    redLight.position.set(0, 4.9, 0.2);
    redLight.userData.color = 'red';
    group.add(redLight);

    const yellowLight = new THREE.Mesh(lightGeo, new THREE.MeshStandardMaterial({ color: 0x332200, emissive: 0x332200, emissiveIntensity: 3 }));
    yellowLight.position.set(0, 4.5, 0.2);
    yellowLight.userData.color = 'yellow';
    group.add(yellowLight);

    const greenLight = new THREE.Mesh(lightGeo, new THREE.MeshStandardMaterial({ color: 0x003300, emissive: 0x003300, emissiveIntensity: 3 }));
    greenLight.position.set(0, 4.1, 0.2);
    greenLight.userData.color = 'green';
    group.add(greenLight);

    group.userData = { signalId: s.id, lights: [redLight, yellowLight, greenLight] };
    group.position.set(s.x, 0, -s.y);
    signalGroup.add(group);
  }
}

// ===== STREET LABELS =====
function buildStreetLabels() {
  // Clear existing labels
  while (labelGroup.children.length) labelGroup.remove(labelGroup.children[0]);

  // Dedupe by name: keep longest segment (most shape points) per unique name
  const nameBest = new Map(); // name -> { shape, width }
  for (const road of sceneData.roads) {
    if (!road.name || road.shape.length < 2) continue;
    const existing = nameBest.get(road.name);
    if (!existing || road.shape.length > existing.shape.length) {
      nameBest.set(road.name, road);
    }
  }

  const entries = Array.from(nameBest.values());
  // Limit to 250 labels for performance
  const toLabel = entries.slice(0, 250);

  const LABEL_HEIGHT = 14; // world-units text height
  const canvasSize = 1024;

  for (const road of toLabel) {
    const shape = road.shape;
    // Pick midpoint segment
    const midIdx = Math.floor(shape.length / 2);
    const p0 = shape[Math.max(0, midIdx - 1)];
    const p1 = shape[Math.min(shape.length - 1, midIdx + 1)];
    const mx = (p0[0] + p1[0]) / 2;
    const my = (p0[1] + p1[1]) / 2;
    const dx = p1[0] - p0[0];
    const dy = p1[1] - p0[1];
    const angle = Math.atan2(dx, dy); // angle in XY plane

    // Create canvas texture
    const canvas = document.createElement('canvas');
    canvas.width = canvasSize;
    canvas.height = canvasSize / 4;
    const ctx = canvas.getContext('2d');

    const name = road.name;
    const fontSize = canvas.height * 0.55;
    ctx.font = `bold ${fontSize}px "Segoe UI", "Helvetica Neue", Arial, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    // White halo (stroke)
    ctx.strokeStyle = 'rgba(5,8,16,0.9)';
    ctx.lineWidth = fontSize * 0.25;
    ctx.lineJoin = 'round';
    ctx.strokeText(name, canvas.width / 2, canvas.height / 2);

    // Grey text fill
    ctx.fillStyle = '#cfe3ff';
    ctx.fillText(name, canvas.width / 2, canvas.height / 2);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.magFilter = THREE.LinearFilter;
    texture.generateMipmaps = true;

    const spriteMat = new THREE.SpriteMaterial({
      map: texture,
      transparent: true,
      depthTest: false,
      depthWrite: false,
    });

    const sprite = new THREE.Sprite(spriteMat);
    const aspect = canvas.width / canvas.height;
    sprite.scale.set(LABEL_HEIGHT * aspect, LABEL_HEIGHT, 1);

    // Place at road midpoint in THREE coords (x, 0, -y_sumo)
    sprite.position.set(mx, 0.5, -my);

    // Rotate sprite to align with road direction
    // Sprites always face camera; rotate around Z to match road orientation
    // Flip text if road goes backwards (dx < 0) for readability
    if (dx < 0) {
      sprite.rotation.z = angle + Math.PI;
    } else {
      sprite.rotation.z = angle;
    }

    sprite.userData = { roadName: road.name };
    labelGroup.add(sprite);
  }
}

function updateLabelOpacity() {
  if (!labelGroup) return;
  const checked = document.getElementById('chk-labels')?.checked ?? false;
  if (!checked) {
    labelGroup.visible = false;
    return;
  }

  const alt = camera.position.y;
  const threshold = sceneSpan * 0.5;

  if (alt > threshold) {
    const fade = Math.max(0, 1 - (alt - threshold) / (threshold * 0.5));
    labelGroup.visible = fade > 0.01;
    labelGroup.children.forEach(s => {
      if (s.material) s.material.opacity = fade;
    });
  } else {
    labelGroup.visible = true;
    labelGroup.children.forEach(s => {
      if (s.material) s.material.opacity = 1;
    });
  }
}

// ────────────────────────────────────────────────────────────
// UPDATE SIGNALS
// ────────────────────────────────────────────────────────────
function updateSignalLights() {
  for (const child of signalGroup.children) {
    const signalId = child.userData.signalId;
    const state = tlsStates[signalId] || 'rrrrrr';
    const firstChar = state[0];

    let activeColor = 'red';
    if (firstChar === 'G' || firstChar === 'g') activeColor = 'green';
    else if (firstChar === 'y' || firstChar === 'Y') activeColor = 'yellow';

    for (const light of child.userData.lights) {
      const isActive = light.userData.color === activeColor;
      const colorName = light.userData.color;
      if (colorName === 'red') {
        light.material.color.set(isActive ? 0xff0000 : 0x330000);
        light.material.emissive.set(isActive ? 0xff0000 : 0x110000);
      } else if (colorName === 'yellow') {
        light.material.color.set(isActive ? 0xffaa00 : 0x332200);
        light.material.emissive.set(isActive ? 0xff6600 : 0x110000);
      } else {
        light.material.color.set(isActive ? 0x00ff00 : 0x003300);
        light.material.emissive.set(isActive ? 0x00ff00 : 0x001100);
      }
    }
  }
}

// ────────────────────────────────────────────────────────────
// UPDATE CARS
// ────────────────────────────────────────────────────────────
function updateCars(dt) {
  streamInterp = Math.min(1, streamInterp + dt * 10);

  // Determine which vehicles to show
  const allVeh = new Map();
  const alpha = streamInterp;

  for (const [id, v] of vehicles) {
    const prev = prevVehicles.get(id);
    if (prev) {
      allVeh.set(id, {
        x: prev.x + (v.x - prev.x) * alpha,
        y: prev.y + (v.y - prev.y) * alpha,
        angle: v.angle,
        speed: v.speed
      });
    } else {
      allVeh.set(id, { x: v.x, y: v.y, angle: v.angle, speed: v.speed });
    }
  }

  // Also include disappearing vehicles (fade out)
  for (const [id, v] of prevVehicles) {
    if (!allVeh.has(id)) {
      allVeh.set(id, { x: v.x, y: v.y, angle: v.angle, speed: v.speed, fading: true });
    }
  }

  let count = 0;
  const useSpeedColor = document.getElementById('chk-speedcolor').checked;

  for (const [id, v] of allVeh) {
    if (count >= MAX_CARS) break;

    carDummy.position.set(v.x, 0, -v.y);
    // angle is clockwise from north; three.js: rotation.y = -(angle - 90) degrees = (90 - angle) degrees as radians
    // angle in degrees, clockwise from north. north = +y in SUMO = -z in three.js
    // In three.js, heading 0 faces -z. A car heading east (90° clockwise from north) should face +x.
    // rotation.y = -angle * PI/180
    carDummy.rotation.set(0, -v.angle * Math.PI / 180, 0);
    carDummy.scale.setScalar(Math.min(3.2, Math.max(1, camera.position.y / 160)));
    carDummy.updateMatrix();
    carInstancedMesh.setMatrixAt(count, carDummy.matrix);

    // Color
    if (useSpeedColor) {
      const speed = v.speed;
      if (speed < 1) carColor.copy(COLOR_STOPPED);
      else if (speed < 5) carColor.copy(COLOR_SLOW);
      else carColor.copy(COLOR_FLOW);
    } else {
      carColor.copy(COLOR_PLAIN);
    }
    if (v.fading) carColor.multiplyScalar(0.3);
    carInstancedMesh.setColorAt(count, carColor);

    count++;
  }

  carInstancedMesh.count = count;
  carInstancedMesh.instanceMatrix.needsUpdate = true;
  if (carInstancedMesh.instanceColor) carInstancedMesh.instanceColor.needsUpdate = true;
}

// ────────────────────────────────────────────────────────────
// RAYCASTING FOR ROADS
// ────────────────────────────────────────────────────────────
function onCanvasClick(e) {
  // Only fly mode captures the mouse; orbit and cinematic use normal dragging
  if (cameraMode !== 'fly') return;
  if (!document.pointerLockElement) {
    // Check if clicking on control panel
    if (e.target.closest('#control-panel')) return;
    // Lock pointer
    renderer.domElement.requestPointerLock();
    return;
  }
}

function onPointerLockChange() {
  if (!document.pointerLockElement) {
    helpText.style.opacity = '1';
  } else {
    helpText.style.opacity = '0.5';
  }
}

function onKeyDown(e) {
  keys[e.code] = true;

  if (e.code === 'Escape') {
    if (document.pointerLockElement) document.exitPointerLock();
  }
  if (e.target.closest && e.target.closest('input, select, textarea')) return;
  if (e.code === 'KeyC') setCameraMode('orbit');
  if (e.code === 'KeyF') setCameraMode('fly');
  if (e.code === 'KeyO') setCameraMode('cinematic');
  if (e.code === 'KeyH') document.body.classList.toggle('ui-hidden');
  if (e.code === 'KeyN') toggleDayNight();
  if (e.code === 'Tab') {
    e.preventDefault();
    if (document.pointerLockElement) document.exitPointerLock();
    panel.classList.toggle('panel-collapsed');
    panelToggle.textContent = panel.classList.contains('panel-collapsed') ? '+' : '−';
  }
}

function onKeyUp(e) {
  keys[e.code] = false;
}

function setCameraMode(mode) {
  if (document.pointerLockElement && mode !== 'fly') document.exitPointerLock();
  cameraMode = mode;
  orbitControls.enabled = mode !== 'fly';
  orbitControls.autoRotate = mode === 'orbit';
  document.body.style.cursor = mode === 'orbit' ? 'grab' : '';
  if (mode === 'orbit') orbitControls.target.set(0, 0, 0);
  if (mode === 'cinematic') cinOrbitAngle = Math.atan2(camera.position.z, camera.position.x);
  if (mode === 'fly') {
    initFlyControls();  // keep the current view instead of snapping
    if (!document.pointerLockElement) renderer.domElement.requestPointerLock();
  }
  document.querySelectorAll('[data-cam]').forEach(b => b.classList.toggle('on', b.dataset.cam === mode));
}
window.setCameraMode = setCameraMode;

function updateCinematicOrbit(dt) {
  if (cameraMode !== 'cinematic') return;
  cinOrbitAngle += dt * 0.3;
  const radius = 500;
  const height = 200 + Math.sin(cinOrbitAngle * 0.5) * 100;
  const cx = Math.cos(cinOrbitAngle) * radius;
  const cz = Math.sin(cinOrbitAngle) * radius;
  camera.position.lerp(new THREE.Vector3(cx, height, cz), 0.02);
  orbitControls.target.set(0, 0, 0);
  orbitControls.update();
}

// ────────────────────────────────────────────────────────────
// RAYCAST ROAD CLICK (when pointer not locked)
// ────────────────────────────────────────────────────────────
function installRoadClick() {
renderer.domElement.addEventListener('dblclick', (e) => {
  if (document.pointerLockElement) return;
  if (e.target.closest('#control-panel')) return;

  mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
  mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;

  raycaster.setFromCamera(mouse, camera);
  const intersects = raycaster.intersectObjects(roadGroup.children, true);

  if (intersects.length > 0) {
    let obj = intersects[0].object;
    while (obj && !obj.userData.isRoad) obj = obj.parent;
    if (obj && obj.userData.edgeId) {
      toggleRoad(obj.userData.edgeId);
    }
  }
});
}

async function toggleRoad(edgeId) {
  if (closedRoads.has(edgeId)) {
    closedRoads.delete(edgeId);
  } else {
    closedRoads.add(edgeId);
  }
  controlState.closeEdges = Array.from(closedRoads);
  await postControl({ closeEdges: controlState.closeEdges });
  updateClosedRoadsUI();
  highlightClosedRoads();
}

function updateClosedRoadsUI() {
  const list = document.getElementById('closed-roads-list');
  list.innerHTML = '';
  for (const id of closedRoads) {
    const li = document.createElement('li');
    const road = roadMeshMap.get(id);
    li.textContent = road ? road.userData.roadName : id;
    li.addEventListener('click', () => toggleRoad(id));
    list.appendChild(li);
  }
}

function highlightClosedRoads() {
  for (const [id, mesh] of roadMeshMap) {
    if (closedRoads.has(id)) {
      mesh.material = mesh.material.clone();
      mesh.material.color.set(0xff4444);
      mesh.material.emissive = new THREE.Color(0x330000);
    } else {
      mesh.material.color.set(0xe8e8e8);
      mesh.material.emissive = new THREE.Color(0x000000);
    }
  }
}

// ────────────────────────────────────────────────────────────
// DAY/NIGHT
// ────────────────────────────────────────────────────────────
function toggleDayNight() {
  isNight = !isNight;
  applyTheme();
}

function applyTheme() {
  const u = scene.userData;
  scene.background = skyTexture(isNight);
  scene.fog.color.set(isNight ? 0x0b1426 : 0x3a5a8a);
  u.sun.color.set(isNight ? 0x9fb8ff : 0xffc9a0);
  u.sun.intensity = isNight ? 1.4 : 2.4;
  u.ambient.color.set(isNight ? 0x3a4a6e : 0x6f86b8);
  u.ambient.intensity = isNight ? 0.9 : 1.3;
  u.hemi.intensity = isNight ? 0.6 : 1.0;
  winUniform.value = isNight ? 1 : 0.35;
  bloomPass.strength = isNight ? 0.85 : 0.45;
  renderer.toneMappingExposure = isNight ? 1.05 : 1.15;
  document.body.classList.toggle('night-mode', isNight);
  document.getElementById('btn-daynight').textContent = isNight ? '☀ Blue hour' : '🌙 Night';
}

// ────────────────────────────────────────────────────────────
// CONTROL PANEL EVENTS
// ────────────────────────────────────────────────────────────
function setupControlPanel() {
  // Collapse toggle
  document.getElementById('panel-header').addEventListener('click', (e) => {
    if (e.target === panelToggle) return;
    panel.classList.toggle('panel-collapsed');
    panelToggle.textContent = panel.classList.contains('panel-collapsed') ? '+' : '−';
  });
  panelToggle.addEventListener('click', (e) => {
    e.stopPropagation();
    panel.classList.toggle('panel-collapsed');
    panelToggle.textContent = panel.classList.contains('panel-collapsed') ? '+' : '−';
  });

  // Sliders
  const playbackSlider = document.getElementById('ctrl-playback');
  const speedScaleSlider = document.getElementById('ctrl-speedscale');
  const demandSlider = document.getElementById('ctrl-demand');
  const signalModeSelect = document.getElementById('ctrl-signalmode');

  playbackSlider.addEventListener('input', () => {
    const val = parseFloat(playbackSlider.value);
    document.getElementById('val-playback').textContent = val.toFixed(1) + 'x';
    postControl({ playbackRate: val });
  });

  document.getElementById('btn-pause').addEventListener('click', () => {
    isPaused = !isPaused;
    document.getElementById('btn-pause').textContent = isPaused ? '▶' : '⏯';
    postControl({ playbackRate: isPaused ? 0 : parseFloat(playbackSlider.value) });
  });

  speedScaleSlider.addEventListener('input', () => {
    const val = parseFloat(speedScaleSlider.value);
    document.getElementById('val-speedscale').textContent = val.toFixed(1);
    postControl({ speedScale: val });
  });

  demandSlider.addEventListener('input', () => {
    const val = parseFloat(demandSlider.value);
    document.getElementById('val-demand').textContent = val.toFixed(1);
    postControl({ demandScale: val });
  });

  signalModeSelect.addEventListener('change', () => {
    postControl({ signalMode: signalModeSelect.value });
    document.getElementById('stat-mode').textContent = signalModeSelect.value;
  });

  document.getElementById('btn-reset').addEventListener('click', () => {
    postControl({ reset: true });
    if (isMockMode) { mockTime = 0; initMockVehicles(); }
  });

  document.getElementById('btn-daynight').addEventListener('click', toggleDayNight);

  document.getElementById('chk-buildings').addEventListener('change', (e) => {
    buildingGroup.visible = e.target.checked;
  });

  // ===== STREET LABELS =====
  document.getElementById('chk-labels').addEventListener('change', (e) => {
    labelGroup.visible = e.target.checked;
    // Update label opacity immediately
    updateLabelOpacity();
  });

  // Prevent panel interactions from propagating to canvas
  panel.addEventListener('mousedown', (e) => e.stopPropagation());
  panel.addEventListener('pointerdown', (e) => e.stopPropagation());
}

// ────────────────────────────────────────────────────────────
// STATS HUD
// ────────────────────────────────────────────────────────────
function updateStatsHUD() {
  document.getElementById('stat-cars').textContent = simStats.n || vehicles.size || 0;
  document.getElementById('stat-speed').textContent = ((simStats.meanSpeed || 0) * 3.6).toFixed(0);
  document.getElementById('stat-stopped').textContent = simStats.halting || 0;
  const t = Math.max(0, simTime || 0);
  document.getElementById('stat-time').textContent =
    [t / 3600, t % 3600 / 60, t % 60].map(v => String(Math.floor(v)).padStart(2, '0')).join(':');
  document.getElementById('stat-mode').textContent = controlState.signalMode || 'normal';
  const n = simStats.n || vehicles.size || 0;
  const moving = n ? Math.round(100 * (1 - (simStats.halting || 0) / n)) : 0;
  const mv = document.getElementById('stat-moving');
  if (mv) mv.textContent = moving;
  drawSparkline((simStats.meanSpeed || 0) * 3.6);
}

let sparkAt = 0;
function drawSparkline(kmh) {
  const now = performance.now();
  if (now - sparkAt < 500) return;  // two samples a second, ~1 minute of history
  sparkAt = now;
  speedHistory.push(kmh);
  if (speedHistory.length > 120) speedHistory.shift();
  const cv = document.getElementById('spark-speed');
  if (!cv) return;
  const g = cv.getContext('2d'), w = cv.width, h = cv.height;
  const max = Math.max(30, ...speedHistory);
  g.clearRect(0, 0, w, h);
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, 'rgba(34,211,238,0.35)'); grad.addColorStop(1, 'rgba(34,211,238,0)');
  g.beginPath();
  speedHistory.forEach((v, i) => {
    const x = i / 119 * w, y = h - 2 - v / max * (h - 4);
    i ? g.lineTo(x, y) : g.moveTo(x, y);
  });
  g.strokeStyle = '#22d3ee'; g.lineWidth = 1.5; g.stroke();
  g.lineTo((speedHistory.length - 1) / 119 * w, h); g.lineTo(0, h); g.closePath();
  g.fillStyle = grad; g.fill();
}

let fpsFrames = 0;
let fpsTime = 0;
function updateFPS(dt) {
  fpsFrames++;
  fpsTime += dt;
  if (fpsTime >= 1) {
    document.getElementById('stat-fps').textContent = Math.round(fpsFrames / fpsTime);
    fpsFrames = 0;
    fpsTime = 0;
  }
}

// ────────────────────────────────────────────────────────────
// RESIZE
// ────────────────────────────────────────────────────────────
function onResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  composer.setSize(window.innerWidth, window.innerHeight);
}

// ────────────────────────────────────────────────────────────
// MAIN LOOP
// ────────────────────────────────────────────────────────────
function animate(timestamp) {
  requestAnimationFrame(animate);

  const dt = Math.min(0.1, clock.getDelta());

  // Update mock stream
  if (isMockMode && sceneData) {
    updateMockStream(dt);
    const data = getMockStreamData();
    processStreamData(data);
  }

  // Update stream interpolation
  if (!isMockMode && lastStreamFrame) {
    streamInterp = Math.min(1, streamInterp + dt * 10);
  }

  // Controls
  if (cameraMode === 'intro') updateIntro(dt);
  if (cameraMode === 'fly') updateFlyControls(dt);
  if (cameraMode === 'orbit') {
    if (!orbitControls.autoRotate && orbitIdleAt && performance.now() > orbitIdleAt) orbitControls.autoRotate = true;
    orbitControls.update();
  }
  if (cameraMode === 'cinematic') updateCinematicOrbit(dt);

  // Update scene
  updateCars(dt);
  updateSignalLights();
  updateLabelOpacity();
  updateStatsHUD();
  updateFPS(dt);

  // Render (bloom)
  composer.render();
}

// Opening shot: swoop down from high above the city into a slow orbit
function updateIntro(dt) {
  introT = Math.min(1, introT + dt / 5);
  const e = introT < 0.5 ? 4 * introT ** 3 : 1 - (-2 * introT + 2) ** 3 / 2;
  camera.position.lerpVectors(introFrom, introTo, e);
  camera.lookAt(0, 0, 0);
  if (introT >= 1) setCameraMode('orbit');
}

// ────────────────────────────────────────────────────────────
// INIT
// ────────────────────────────────────────────────────────────
function recenterScene(d) {
  if (!d.bounds || d.__centered) return;
  const [x0, y0, x1, y1] = d.bounds;
  CEN = { x: (x0 + x1) / 2, y: (y0 + y1) / 2 };
  sceneSpan = Math.max(x1 - x0, y1 - y0);
  const sh = p => [p[0] - CEN.x, p[1] - CEN.y];
  d.roads.forEach(r => r.shape = r.shape.map(sh));
  d.buildings.forEach(b => b.ring = b.ring.map(sh));
  d.parks = d.parks.map(r => r.map(sh));
  d.water = d.water.map(r => r.map(sh));
  d.signals.forEach(g => { g.x -= CEN.x; g.y -= CEN.y; });
  d.__centered = true;
  camera.position.set(0, sceneSpan * 0.35, sceneSpan * 0.55);
  camera.lookAt(0, 0, 0);
  introFrom.set(-sceneSpan * 0.15, sceneSpan * 1.25, sceneSpan * 1.1);
  introTo.set(sceneSpan * 0.32, sceneSpan * 0.2, sceneSpan * 0.36);
  camera.position.copy(introFrom); camera.lookAt(0, 0, 0);
  // faint "digital twin" grid on the ground
  const size = sceneSpan * 3;
  gridHelper = new THREE.GridHelper(size, Math.round(size / 50), 0x1d4f8f, 0x13294a);
  gridHelper.material.transparent = true; gridHelper.material.opacity = 0.35; gridHelper.position.y = -0.1;
  scene.add(gridHelper);
  camera.far = sceneSpan * 4; camera.updateProjectionMatrix();
  scene.fog.near = sceneSpan * 0.6; scene.fog.far = sceneSpan * 3;
  groundMesh.scale.set(sceneSpan * 3 / 3000, sceneSpan * 3 / 3000, 1);
}

async function main() {
  clock = new THREE.Clock();

  initThree();
  installRoadClick();
  setupControlPanel();
  applyTheme();
  document.querySelectorAll('[data-cam]').forEach(b => b.addEventListener('click', () => setCameraMode(b.dataset.cam)));
  document.getElementById('btn-hide-ui')?.addEventListener('click', () => document.body.classList.toggle('ui-hidden'));

  // Initial badge
  setBadge(isMockMode ? 'mock' : 'connecting');

  // Load scene
  sceneData = await loadSceneData();
  recenterScene(sceneData);
  buildScene();
  const sub = document.getElementById('scene-sub');
  if (sub) sub.textContent = `${sceneData.signals.length} signals · ${sceneData.roads.length} road segments · ${sceneData.buildings.length} buildings`;

  // Connect stream
  if (!isMockMode) {
    connectStream();
  }

  // Start
  requestAnimationFrame(animate);
}

main().catch(err => {
  console.error('Init error:', err);
  setBadge('error');
  badge.textContent = '⚠ Error: ' + err.message;
});