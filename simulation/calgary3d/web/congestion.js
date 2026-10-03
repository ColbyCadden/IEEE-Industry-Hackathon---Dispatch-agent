/**
 * Congestion Layer & Data Sources Module
 * Manages real-world traffic congestion visualization and data source attribution
 */

import * as THREE from 'three';

// Color definitions matching Google Maps traffic
const COLORS = {
  green: new THREE.Color(0x34a853),   // Free-flowing
  yellow: new THREE.Color(0xfbbc04),  // Moderate congestion
  red: new THREE.Color(0xea4335),     // Heavy congestion
  white: new THREE.Color(0xffffff)    // Unaffected/default
};

// Threshold levels for congestion coloring
const THRESHOLDS = {
  green: 0.33,
  yellow: 0.66,
  red: 1.0
};

class CongestionLayer {
  constructor() {
    this.enabled = false;
    this.congestionData = null;
    this.materialsCloned = new Set();
    this.incidentMeshes = new Map();
    this.pollInterval = null;
    this.pollFrequency = 60000; // 60 seconds
    this.sourcesPanelOpen = false;
    this.sourcesData = null;

    // Wait for app to be ready
    this.waitForApp();
  }

  async waitForApp() {
    // Poll for window.__dbg and roadMeshMap
    const maxWaitTime = 30000;
    const startTime = Date.now();

    while (!window.__dbg || !window.__dbg.roadMeshMap || window.__dbg.roadMeshMap.size === 0) {
      if (Date.now() - startTime > maxWaitTime) {
        console.error('[Congestion] Failed to initialize: app not ready after 30s');
        return;
      }
      await new Promise(r => setTimeout(r, 500));
    }

    console.log('[Congestion] App ready, initializing layer');
    this.init();
  }

  init() {
    // Add CSS
    this.injectCSS();

    // Add controls to panel
    this.addControlPanel();

    // Load data (mock or real)
    this.loadCongestionData();
    this.loadSourcesData();

    // Set up keyboard shortcuts
    this.setupKeyboardShortcuts();

    // Create incident pin group
    if (!window.__dbg.incidentGroup) {
      window.__dbg.incidentGroup = new THREE.Group();
      window.__dbg.scene.add(window.__dbg.incidentGroup);
    }
  }

  injectCSS() {
    if (!document.querySelector('link[href="congestion.css"]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = 'congestion.css';
      document.head.appendChild(link);
    }
  }

  addControlPanel() {
    const panelBody = document.querySelector('#panel-body');
    if (!panelBody) return;

    // Add sources button near top of panel
    const sourcesBtn = document.createElement('button');
    sourcesBtn.id = 'btn-sources';
    sourcesBtn.textContent = '📊 Data Sources (I)';
    sourcesBtn.onclick = () => this.toggleSourcesPanel();
    panelBody.insertBefore(sourcesBtn, panelBody.firstChild);

    // Add congestion checkbox section
    const congestionSection = document.createElement('div');
    congestionSection.id = 'congestion-section';
    congestionSection.innerHTML = `
      <div class="ctrl-group checkbox-row">
        <label><input type="checkbox" id="chk-congestion"> Real-world Congestion Layer (G)</label>
      </div>
      <div id="congestion-legend">
        <div class="legend-item">
          <div class="legend-color" style="background: #34a853;"></div>
          <span class="legend-label">Free-flowing (&lt;33%)</span>
        </div>
        <div class="legend-item">
          <div class="legend-color" style="background: #fbbc04;"></div>
          <span class="legend-label">Moderate (33–66%)</span>
        </div>
        <div class="legend-item">
          <div class="legend-color" style="background: #ea4335;"></div>
          <span class="legend-label">Heavy (&gt;66%)</span>
        </div>
        <div class="legend-item">
          <div class="legend-color" style="background: #ffffff;"></div>
          <span class="legend-label">No data</span>
        </div>
        <div id="congestion-diag"></div>
      </div>
    `;
    
    const closedRoadsSection = document.querySelector('#closed-roads-section');
    if (closedRoadsSection) {
      closedRoadsSection.parentNode.insertBefore(congestionSection, closedRoadsSection);
    } else {
      panelBody.appendChild(congestionSection);
    }

    // Add incidents section after congestion
    const incidentsSection = document.createElement('div');
    incidentsSection.id = 'incidents-section';
    incidentsSection.innerHTML = `
      <strong>Live Incidents:</strong>
      <ul id="incidents-list"></ul>
    `;
    incidentsSection.style.display = 'none';
    congestionSection.parentNode.insertBefore(incidentsSection, congestionSection.nextSibling);

    // Add HUD line for congestion stats
    const statsHud = document.querySelector('#stats-hud');
    if (statsHud) {
      const hudLine = document.createElement('div');
      hudLine.id = 'congestion-hud-line';
      statsHud.appendChild(hudLine);
    }

    // Add disclaimer section
    const disclaimerSection = document.createElement('div');
    disclaimerSection.id = 'disclaimer-section';
    disclaimerSection.style.display = 'none';
    disclaimerSection.innerHTML = `<div id="disclaimer-text"></div>`;
    panelBody.appendChild(disclaimerSection);

    // Wire checkbox
    const checkbox = document.querySelector('#chk-congestion');
    checkbox.addEventListener('change', (e) => this.toggleCongestionLayer(e.target.checked));

    // Create sources panel
    this.createSourcesPanel();
  }

  createSourcesPanel() {
    const panel = document.createElement('div');
    panel.id = 'sources-panel';
    panel.innerHTML = `
      <div id="sources-header">
        <h2>📊 Data Sources</h2>
        <button id="sources-close-btn">✕</button>
      </div>
      <div id="sources-content"></div>
      <div id="sources-footer">
        © OpenStreetMap contributors · The City of Calgary (Open Government Licence) · Eclipse SUMO
      </div>
    `;
    document.body.appendChild(panel);

    document.querySelector('#sources-close-btn').onclick = () => this.toggleSourcesPanel();

    // Click outside to close
    panel.addEventListener('click', (e) => {
      if (e.target === panel) this.toggleSourcesPanel();
    });
  }

  toggleSourcesPanel() {
    const panel = document.querySelector('#sources-panel');
    this.sourcesPanelOpen = !this.sourcesPanelOpen;
    panel.classList.toggle('open', this.sourcesPanelOpen);
    
    if (this.sourcesPanelOpen) {
      this.renderSourcesPanel();
    }
  }

  renderSourcesPanel() {
    const content = document.querySelector('#sources-content');
    if (!this.sourcesData || !this.sourcesData.sources) {
      content.innerHTML = '<p style="opacity: 0.5;">No data sources available</p>';
      return;
    }

    const grouped = {};
    this.sourcesData.sources.forEach(source => {
      if (!grouped[source.status]) grouped[source.status] = [];
      grouped[source.status].push(source);
    });

    const statusOrder = ['live', 'static', 'modelled', 'placeholder'];
    const statusLabels = {
      'live': '🟢 Live',
      'static': '🔵 Static',
      'modelled': '🟡 Modelled',
      'placeholder': '⚪ Placeholder'
    };

    content.innerHTML = '';

    statusOrder.forEach(status => {
      if (!grouped[status]) return;

      const groupDiv = document.createElement('div');
      groupDiv.className = 'sources-group';

      const title = document.createElement('div');
      title.className = 'sources-group-title';
      const badge = document.createElement('span');
      badge.className = `sources-badge ${status}`;
      badge.textContent = status[0].toUpperCase();
      title.appendChild(badge);
      title.appendChild(document.createTextNode(statusLabels[status]));
      groupDiv.appendChild(title);

      grouped[status].forEach(source => {
        const item = document.createElement('div');
        item.className = 'source-item';
        
        const name = document.createElement('div');
        name.className = 'source-name';
        name.textContent = source.name;
        item.appendChild(name);

        if (source.provides) {
          const provides = document.createElement('div');
          provides.className = 'source-info';
          provides.innerHTML = `<strong>Provides:</strong> ${this.escapeHTML(source.provides)}`;
          item.appendChild(provides);
        }

        if (source.url) {
          const url = document.createElement('div');
          url.className = 'source-info';
          url.innerHTML = `<a href="${source.url}" target="_blank" class="source-link">${this.escapeHTML(source.url)}</a>`;
          item.appendChild(url);
        }

        if (source.licence) {
          const licence = document.createElement('div');
          licence.className = 'source-info';
          licence.innerHTML = `<strong>Licence:</strong> ${this.escapeHTML(source.licence)}`;
          item.appendChild(licence);
        }

        if (source.attribution) {
          const attr = document.createElement('div');
          attr.className = 'source-info';
          attr.innerHTML = `<strong>Attribution:</strong> ${this.escapeHTML(source.attribution)}`;
          item.appendChild(attr);
        }

        if (source.updated) {
          const updated = document.createElement('div');
          updated.className = 'source-info';
          const time = new Date(source.updated).toLocaleString();
          updated.innerHTML = `<strong>Updated:</strong> ${time}`;
          item.appendChild(updated);
        }

        if (source.note) {
          const note = document.createElement('div');
          note.className = 'source-note';
          note.textContent = source.note;
          item.appendChild(note);
        }

        groupDiv.appendChild(item);
      });

      content.appendChild(groupDiv);
    });
  }

  escapeHTML(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  async loadCongestionData() {
    const useMock = new URLSearchParams(window.location.search).has('mockcong');

    try {
      const url = useMock ? '/mock/congestion.json' : '/congestion.json';
      const response = await fetch(url);

      if (!response.ok) {
        if (!useMock) {
          console.log('[Congestion] Real endpoint not available, falling back to mock');
          return this.loadCongestionData(); // Retry with mock
        }
        throw new Error(`Failed to load: ${response.status}`);
      }

      this.congestionData = await response.json();
      this.updateBadge();
      console.log('[Congestion] Data loaded:', this.congestionData.mode);
    } catch (err) {
      console.error('[Congestion] Load failed:', err);
      this.updateBadge('error');
    }
  }

  async loadSourcesData() {
    try {
      const useMock = new URLSearchParams(window.location.search).has('mockcong');
      const url = useMock ? '/mock/sources.json' : '/sources.json';
      const response = await fetch(url);

      if (!response.ok) {
        if (!useMock) {
          return this.loadSourcesData(); // Retry with mock
        }
        throw new Error(`Failed to load sources: ${response.status}`);
      }

      this.sourcesData = await response.json();
      console.log('[Congestion] Sources loaded');
    } catch (err) {
      console.error('[Congestion] Sources load failed:', err);
    }
  }

  updateBadge(state = 'mock') {
      const badge = document.querySelector('#connection-badge');
      if (!badge) return;

      if (state === 'error') {
        badge.textContent = '● Error';
        badge.className = 'badge-error';
        return;
      }

      // BUG FIX: this hard-coded "● MOCK" even when real measured data was
      // driving the layer, which misrepresented live data as a placeholder.
      // Report what is actually feeding the model.
      const m = (this.congestionData && this.congestionData.measured) || null;
      if (m && m.available) {
        const live = (m.edges_with_travel_times || 0) > 0;
        badge.textContent = live ? '● LIVE' : '● VOLUMES';
        badge.className = live ? 'badge-live' : 'badge-mock';
        badge.title = live
          ? `Measured: ${m.edges_with_travel_times} roads from live travel times, ${m.edges_with_volumes} from annual volumes`
          : `Historic annual volumes only (${m.edges_with_volumes} roads). Run the scraper agent for live travel times.`;
      } else {
        badge.textContent = '● ESTIMATE';
        badge.className = 'badge-mock';
        badge.title = 'No measured data found. Run: python -m agents.run_pipeline';
      }
    }

  async toggleCongestionLayer(enabled) {
    this.enabled = enabled;
    const checkbox = document.querySelector('#chk-congestion');
    const legend = document.querySelector('#congestion-legend');
    const incidentsSection = document.querySelector('#incidents-section');
    const disclaimerSection = document.querySelector('#disclaimer-section');
    const hudLine = document.querySelector('#congestion-hud-line');

    if (enabled) {
      if (!this.congestionData) {
        await this.loadCongestionData();
      }

      legend.classList.add('active');
      incidentsSection.style.display = 'block';
      disclaimerSection.style.display = 'block';
      hudLine.classList.add('active');

      this.applyColors();
      this.renderIncidents();
      this.updateHUDLine();

      // Start polling
      if (this.pollInterval) clearInterval(this.pollInterval);
      this.pollInterval = setInterval(() => {
        this.loadCongestionData();
        if (this.enabled) {
          this.applyColors();
          this.renderIncidents();
          this.updateHUDLine();
        }
      }, this.pollFrequency);
    } else {
      legend.classList.remove('active');
      incidentsSection.style.display = 'none';
      disclaimerSection.style.display = 'none';
      hudLine.classList.remove('active');

      this.restoreColors();
      this.clearIncidents();

      if (this.pollInterval) clearInterval(this.pollInterval);
    }
  }

  applyColors() {
      if (!this.congestionData || !this.congestionData.edges) return;

      const roadMeshMap = window.__dbg.roadMeshMap;
      const edges = this.congestionData.edges;

      // Track what we actually recoloured so the layer can report itself
      // honestly instead of silently failing (the original open bug).
      let coloured = 0, missing = 0;

      roadMeshMap.forEach((mesh, edgeId) => {
        const level = edges[edgeId];

        if (level === undefined) {
          // No data for this road. Leave it as-is rather than resetting it to
          // a brand-new material - the old code built a fresh white material
          // here, which threw away the shared material reference and made the
          // whole layer look broken even where data WAS present.
          missing++;
          return;
        }

        // Determine color based on congestion level
        let color;
        if (level < THRESHOLDS.green) {
          color = COLORS.green;
        } else if (level < THRESHOLDS.yellow) {
          color = COLORS.yellow;
        } else {
          color = COLORS.red;
        }

        // Clone material if not already cloned (keyed by edgeId, matching
        // restoreColors()).
        if (!this.materialsCloned.has(edgeId)) {
          mesh.material = mesh.material.clone();
          this.materialsCloned.add(edgeId);
        }

        mesh.material.color.copy(color);
        coloured++;
      });

      this.lastApplyStats = { coloured, missing, total: roadMeshMap.size };
      this.renderDiagnostics();
    }

    renderDiagnostics() {
      const el = document.querySelector('#congestion-diag');
      if (!el) return;
      const s = this.lastApplyStats;
      const m = (this.congestionData && this.congestionData.measured) || null;
      if (!s) { el.textContent = ''; return; }
      let txt = `layer: ${s.coloured}/${s.total} roads coloured`;
      if (m && m.available) {
        txt += ` · ${m.edges_with_travel_times} from travel times · ${m.edges_with_volumes} from volumes`;
      } else {
        txt += ' · NO measured data (run the scraper agent)';
      }
      el.textContent = txt;
      el.style.display = 'block';
    }

  restoreColors() {
    if (!window.__dbg.roadMeshMap) return;

    const roadMeshMap = window.__dbg.roadMeshMap;

    // BUG FIX: this method previously compared materialsCloned against
    // mesh.uuid while applyColors() stores edgeId. Those are different key
    // spaces, so the "has this material been cloned?" check never matched.
    //
    // The original white road material must come from __dbg (captured at
    // build time). Grabbing it from an arbitrary mesh here would pick up a
    // material applyColors() had already recoloured, so toggling the layer
    // off left every road stuck on its last congestion colour.
    const originalMaterial = window.__dbg.originalRoadMaterial
      || new THREE.MeshStandardMaterial({ color: 0xffffff });

    roadMeshMap.forEach((mesh, edgeId) => {
      mesh.material = originalMaterial;
      this.materialsCloned.delete(edgeId);
    });
  }

  renderIncidents() {
    this.clearIncidents();

    if (!this.congestionData || !this.congestionData.incidents) return;

    const incidentsGroup = window.__dbg.incidentGroup;
    const incidents = this.congestionData.incidents;
    const CEN = window.__dbg.CEN;
    const scene = window.__dbg.scene;

    incidents.forEach(incident => {
      // Create pin geometry (cone + sphere)
      const pinGroup = new THREE.Group();

      // Sphere at top (head)
      const sphereGeom = new THREE.SphereGeometry(8, 16, 16);
      const color = incident.type === 'incident' ? COLORS.red : COLORS.yellow;
      const sphereMat = new THREE.MeshStandardMaterial({ color });
      const sphere = new THREE.Mesh(sphereGeom, sphereMat);
      sphere.position.z = 5;
      pinGroup.add(sphere);

      // Cone (body)
      const coneGeom = new THREE.ConeGeometry(6, 30, 16);
      const coneMat = new THREE.MeshStandardMaterial({ color });
      const cone = new THREE.Mesh(coneGeom, coneMat);
      cone.position.z = 0;
      pinGroup.add(cone);

      // Position pin at incident location
      const pinX = incident.x - CEN.x;
      const pinY = -(incident.y - CEN.y);
      const pinZ = 40; // Height above ground
      pinGroup.position.set(pinX, pinZ, pinY);

      incidentsGroup.add(pinGroup);
      this.incidentMeshes.set(incident.id, pinGroup);
    });

    this.renderIncidentsList();
  }

  renderIncidentsList() {
    const list = document.querySelector('#incidents-list');
    if (!list || !this.congestionData) return;

    list.innerHTML = '';

    this.congestionData.incidents.forEach(incident => {
      const item = document.createElement('li');
      item.className = `incident-item type-${incident.type}`;
      
      const typeEl = document.createElement('div');
      typeEl.className = 'incident-type';
      typeEl.textContent = incident.type.toUpperCase();
      item.appendChild(typeEl);

      const descEl = document.createElement('div');
      descEl.className = 'incident-desc';
      descEl.textContent = incident.desc;
      item.appendChild(descEl);

      const timeEl = document.createElement('div');
      timeEl.className = 'incident-time';
      const startTime = new Date(incident.started).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      timeEl.textContent = `Started: ${startTime}`;
      item.appendChild(timeEl);

      const flyBtn = document.createElement('button');
      flyBtn.className = 'incident-button';
      flyBtn.textContent = '🎯 Fly To';
      flyBtn.onclick = () => this.flyToIncident(incident);
      item.appendChild(flyBtn);

      list.appendChild(item);
    });
  }

  flyToIncident(incident) {
    if (!window.__dbg.camera) return;

    const CEN = window.__dbg.CEN;
    const camera = window.__dbg.camera;

    const targetX = incident.x - CEN.x;
    const targetY = -(incident.y - CEN.y);
    const targetZ = 200; // Above the incident

    // Smoothly animate camera
    const startPos = camera.position.clone();
    const targetPos = new THREE.Vector3(targetX, targetZ, targetY);
    const duration = 1.5;
    const startTime = Date.now();

    const animate = () => {
      const elapsed = (Date.now() - startTime) / 1000;
      const progress = Math.min(elapsed / duration, 1);

      camera.position.lerpVectors(startPos, targetPos, progress);
      camera.lookAt(targetX, 0, targetY);

      if (progress < 1) {
        requestAnimationFrame(animate);
      }
    };

    animate();
  }

  clearIncidents() {
    const incidentsGroup = window.__dbg.incidentGroup;
    while (incidentsGroup.children.length > 0) {
      incidentsGroup.removeChild(incidentsGroup.children[0]);
    }
    this.incidentMeshes.clear();
  }

  updateHUDLine() {
    const hudLine = document.querySelector('#congestion-hud-line');
    if (!hudLine || !this.congestionData) return;

    const data = this.congestionData;
    const time = new Date(data.updated).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const mode = data.mode || 'unknown';
    const incidentCount = data.incidents ? data.incidents.length : 0;

    let html = `Congestion: <strong>${mode}</strong> · ${incidentCount} incidents · updated ${time}`;

    if (data.stale) {
      html += ' <span class="hud-stale-warning">⚠ STALE</span>';
    }

    hudLine.innerHTML = html;

    // Update disclaimer
    const disclaimerText = document.querySelector('#disclaimer-text');
    if (disclaimerText && data.disclaimer) {
      disclaimerText.textContent = data.disclaimer;
    }
  }

  setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      if (e.key === 'g' || e.key === 'G') {
        const checkbox = document.querySelector('#chk-congestion');
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event('change'));
      }
      if (e.key === 'i' || e.key === 'I') {
        this.toggleSourcesPanel();
      }
    });
  }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  new CongestionLayer();
});

// Also try to initialize immediately if already loaded
if (document.readyState !== 'loading') {
  new CongestionLayer();
}
