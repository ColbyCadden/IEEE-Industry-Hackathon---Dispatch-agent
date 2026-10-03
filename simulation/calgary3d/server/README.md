# Calgary 3D Traffic Simulator - Server

HTTP server providing live SUMO traffic simulation data, real-time incident information, and congestion estimates for the Calgary downtown 3D visualization.

## Running the Server

```bash
python server.py [--port PORT]
```

- Default port: `8765`
- Custom port: `python server.py --port 8766`

The server starts a SUMO microscopic traffic simulation in a background thread and serves:
- Live vehicle positions via Server-Sent Events (SSE)
- Per-edge congestion levels + incident data
- Complete data sources attribution

**Windows note**: `allow_reuse_address=False` prevents silent dual-binding issues.

---

## API Endpoints

### 1. GET `/stream` - Live Vehicle Position Stream

Server-Sent Events endpoint streaming frame data.

**Response** (text/event-stream):
```
data: {"t": 50.5, "v": [[vid, x, y, angle, speed], ...], "tls": {...}, "stats": {...}}
...
```

**Frame fields**:
- `t`: simulation time (seconds)
- `v`: vehicle array `[int_id, x_m, y_m, heading_deg, speed_m_s]`
- `tls`: traffic light state updates `{tlsId: "rRrGg..."}` (incremental + full every 20 frames)
- `stats`: frame statistics (vehicle count, mean speed, halting count, control state)

**CORS**: `*` (allowed)

---

### 2. POST `/control` - Simulation Control

Adjust simulation parameters.

**Request** (application/json):
```json
{
  "playbackRate": 1.0,
  "speedScale": 1.0,
  "demandScale": 1.0,
  "signalMode": "normal",
  "closeEdges": ["edge_id_1", "edge_id_2"],
  "reset": false
}
```

**Parameters**:
- `playbackRate` (float): 0–4 (0 = pause, 1 = real-time, 2 = 2×)
- `speedScale` (float): 0.2–2.0 (vehicle max speed multiplier)
- `demandScale` (float): 0–3 (vehicle demand scale)
- `signalMode` (string): `"normal"` | `"allRed"` | `"allGreen"` | `"flashing"`
- `closeEdges` (array of strings): edge IDs to close (prevent vehicle routing)
- `reset` (boolean): restart the simulation

**Response** (application/json): current control state

**CORS**: `*` (allowed)

---

### 3. GET `/congestion.json` - Per-Edge Congestion Levels + Live Incidents

Real-time congestion model combining typical-by-hour heuristic + live incident boost.

**Response** (application/json):
```json
{
  "updated": "2026-10-02T15:51:27.804072+00:00",
  "stale": false,
  "hour": 9,
  "isWeekday": true,
  "mode": "typical-by-hour estimate + live incidents",
  "incidents": [
    {
      "id": "...",
      "type": "Closure",
      "desc": "Traffic incident.",
      "x": 1952.2,
      "y": -15308.5,
      "lat": 50.8981,
      "lon": -114.0701,
      "started": "2026-10-02 13:29:33+00:00",
      "modified": "2026-10-02 14:32:34+00:00"
    }
  ],
  "edges": {
    "-1269868035#0": 0.4,
    "-1270794198#0": 0.4,
    ...
  },
  "disclaimer": "Per-street congestion levels are estimates based on a simple..."
}
```

**Fields**:
- `updated`: ISO 8601 timestamp (UTC)
- `stale`: boolean — true if the last City of Calgary incident API fetch failed (using cached data)
- `hour`: local hour (0–23, America/Edmonton timezone)
- `isWeekday`: boolean
- `mode`: description of the congestion model
- `incidents`: live traffic incidents from City of Calgary (list may be empty)
  - Coordinates: `lat`/`lon` are source data; `x`/`y` are snapped to SUMO meter space
  - Note: incidents may be outside the downtown visualization bounds (outside [0, 2452] × [0, 1849])
- `edges`: per-edge congestion level 0–1 (omits edges with level < 0.05)
- `disclaimer`: explanation that this is NOT real-time speed data

**Congestion Model**:
- **Base level**: typical-by-hour heuristic (weekday peaks 07–09 and 16–18; weekends flatter; nights low)
- **Road class**: higher for major roads (speed ≥ 16 m/s or lanes ≥ 2)
- **Incident boost**: +0.5–0.8 for edges within ~250 m of an incident point
  - Higher boost (+0.7–0.8) for closures/accidents
  - Lower boost (+0.5) for construction/info
  - Linear decay: max at 0 m, zero at 250 m
- **Only edges with level ≥ 0.05 are reported**

**Caching**:
- City of Calgary incidents API: 5-minute cache
- On fetch failure: keep last cached data + set `stale: true`
- No server crashes on network failures (all errors caught)

**CORS**: `*` (allowed)

---

### 4. GET `/sources.json` - Data Sources & Attribution

Complete list of all data sources, licences, and status.

**Response** (application/json):
```json
{
  "generated": "2026-10-02T15:51:35.015314+00:00",
  "sources": [
    {
      "name": "City of Calgary Current Traffic Incidents",
      "provides": "Live incident locations, types, descriptions",
      "url": "https://data.calgary.ca/resource/4jah-h97u.json",
      "licence": "Open Government Licence - City of Calgary",
      "attribution": "The City of Calgary",
      "status": "live",
      "updated": "2026-10-02T15:50:59Z",
      "note": "~10 min refresh; last fetch 1 incidents; ID 4jah-h97u"
    },
    ...
  ]
}
```

**Source statuses**:
- `live`: real-time data (refreshed ~10 min)
- `static`: snapshot (versioned, no live updates)
- `modelled`: estimated/heuristic
- `placeholder`: synthetic (SUMO defaults, random)
- `available-not-used`: data exists but not integrated

**Data sources included**:
1. **City of Calgary Current Traffic Incidents** (live, Socrata)
2. City of Calgary Archived Incidents (static)
3. OpenStreetMap road network + street names + building/park/water footprints (ODbL, snapshot 2026-10-01)
4. Building heights (OSM tags where present; pseudo-random fallback)
5. SUMO traffic simulator (EPL-2.0)
6. Typical-by-hour congestion model (modelled assumption)
7. three.js 3D rendering (MIT)
8. Calgary Traffic Cameras dataset (live, not integrated)
9. Alberta 511 (live, requires API key)

**CORS**: `*` (allowed)

---

## Modules

### `server.py`
Main HTTP server. Handles routing, SSE clients, SUMO TraCI interaction.
- Command-line arg: `--port` (default 8765)
- Loads `web/scene.json` for road data on startup
- Imports `congestion` module for `/congestion.json` and `/sources.json`

### `congestion.py`
Congestion model + live incident fetching.

**Key functions**:
- `refresh_cache()`: fetch City of Calgary incidents (5-min TTL, stale fallback)
- `compute_congestion(roads, net_offset)`: per-edge level calculation
- `get_sources(...)`: data attribution metadata

**Dependencies**: stdlib only
- `urllib`: City of Calgary API fetch
- `zoneinfo`: timezone (fallback to UTC-6 fixed if tzdata unavailable)
- `datetime`: timestamp handling

**Net offset**: currently hardcoded to SUMO net offset `(-704066.21, -5657892.69)` (UTM zone 11 WGS84).

### `export_scene.py`
Pre-processing script (not run by server). Exports SUMO network + OSM polygons to `web/scene.json`.
- Reuses `latlon_to_xy()` helper (used by `congestion.py`)

---

## Example Workflows

### Fetch live congestion + incidents
```bash
curl http://localhost:8765/congestion.json | python3 -m json.tool
```

### Check data attribution
```bash
curl http://localhost:8765/sources.json | python3 -m json.tool
```

### Test on alternate port (keep 8765 running)
```bash
python server.py --port 8766
```

### Pause and manipulate simulation
```bash
curl -X POST http://localhost:8765/control \
  -H "Content-Type: application/json" \
  -d '{"playbackRate": 0, "speedScale": 0.5}'
```

---

## Notes

- **Web directory** (`web/`): NOT edited by server (readonly static files)
- **CORS**: all endpoints support `*` origin
- **Errors**: HTTP 500 on exceptions; HTTP 503 if modules unavailable
- **Windows**: `ThreadingHTTPServer.allow_reuse_address = False` prevents port-in-use issues
- **Simulation**:
  - SUMO runs headless in background thread
  - Auto-restarts on vehicle end or fatal TraCI error
  - Reads `CALGARY_DIR/dt.sumocfg`
  - Step length: 0.5 seconds

---

## Testing

Verify both new endpoints work on port 8766 (without killing the 8765 server):

```bash
# Terminal 1: default server
python server.py

# Terminal 2: test server (can run in parallel)
python server.py --port 8766

# Terminal 3: test both endpoints
curl http://localhost:8766/congestion.json
curl http://localhost:8766/sources.json
curl http://localhost:8766/stream | head -c 500
```

Both servers can run simultaneously. Stop with Ctrl+C.
