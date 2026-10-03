# Project State, Decisions and Gotchas

Everything a new person needs that isn't obvious from the code. If you're picking
this project up cold, read this file first.

---

## Current state

| Area | Status |
|---|---|
| SUMO network (downtown Calgary, 171 signals) | Working |
| TraCI + SSE backend | Working, verified from a clean clone |
| 3D three.js viewer, drone controls | Working |
| Traffic control panel (signals, speeds, closures, day/night) | Working |
| Congestion layer + incident markers | **Loaded and rendering the legend, but roads often stay white — open bug, see below** |
| Live incident fetch | Working (City of Calgary feed) |

Verified by cloning the repo fresh and starting the viewer: all four routes
return 200 (`/`, `/congestion.json`, `/sources.json`, `/scene.json`), the
vehicle stream emits real positions and speeds, and `/control` responds.

---

## The open bug: congestion layer shows white roads

**Symptom.** You tick "Real-world Congestion Layer" in the panel (or press **G**).
The legend appears with green/yellow/red swatches, and the module clearly loads —
but the road ribbons stay white instead of recolouring.

**What is confirmed.** `congestion.js` loads and initialises. `/congestion.json`
returns 557 edges with congestion levels, so the backend is producing data. The
UI panel for data sources also works. So the failure is between the fetch and the
mesh recolour, in the frontend only.

**Where to look.** `calgary3d/web/congestion.js`:
- how it fetches `/congestion.json`
- how it matches returned edge IDs to the road geometry in `scene.json` — **this
  is the most likely culprit.** The API returns SUMO edge IDs like
  `-1269868035#0`, and matching those against scene edges is where a silent
  mismatch would show up as "layer enabled, nothing changes"
- the material/colour write path, and whether it's being called at all

**How to debug it.** In the browser console:

```js
fetch('/congestion.json').then(r => r.json()).then(d => console.log(d.edges))
```

If that shows data but the roads stay white, the geometry matching is the bug.
Log the edge ID from a hovered road and compare it to the IDs in that object.

**Known partial-workaround:** vehicles are still coloured by their real measured
speed from the simulation, which is actual SUMO output and is correct. Only the
*road-level* congestion overlay is broken.

---

## Decisions made, and why

**SUMO/TraCI as the engine.** Gives reliable vehicle state, signal control, and
repeatable experiments. Nothing else here would have provided all three.

**Real OSM data rather than hand-drawn geometry.** Roads, street names, building
footprints, parks and water come from OpenStreetMap via Overpass. Hand-drawing
downtown Calgary would have been worse in every way.

**No frontend build step.** Plain HTML/JS with three.js from a CDN. Anyone can
clone, open a terminal, and run it. No `npm install`, no bundler, no lockfile.

**Stdlib Python HTTP server plus SSE.** Not FastAPI, not Flask. It meant zero web
dependencies, which is why `pip install -r requirements.txt` installs three
packages and nothing else.

**The congestion layer is an estimate, labelled as such in the UI.** No free
live per-street speed feed exists for Calgary. Google, TomTom and HERE all
require paid API keys. Rather than pretend, the app ships a clearly-labelled
heuristic and says so in the interface. **Do not remove that labelling.**

**Only the incident feed is live.** Everything else is generated or modelled.
See the Data Provenance section in the README.

---

## Gotchas that will bite you

**`sumocfg` must not reference `maps.view.xml` in headless mode.** It used to,
and SUMO quit on startup with a confusing error. The GUI-only styling reference
was removed. If you re-add it, the TraCI server will fail.

**Coordinate frames.** The 3D scene and the SUMO network are in the same frame,
but not the same origin. `export_scene.py` uses the network's own projection and
offset. An earlier hand-rolled UTM conversion placed all the buildings and
water kilometres away from the roads. **If you regenerate `scene.json`, don't
change how coordinates are derived.**

**Instanced meshes and frustum culling.** The vehicle `InstancedMesh` must have
`frustumCulled = false`. With it on, cars vanish when the camera moves because
the bounding sphere is wrong for instanced geometry.

**Roads are ribbons, not tubes.** A tube along each edge looks like a pipe. The
current ribbon geometry with outlines and lane-centre markings is what produces
the Google-Maps-like look. Don't "simplify" it back.

**Traffic signal state must be merged, not replaced.** Each `/stream` frame sends
only the signals that *changed*. Assigning that partial set to the scene wipes
the others out and they never recover. Merge into existing state.

**`ThreadingHTTPServer.allow_reuse_address = False`.** Deliberate. Two servers
silently sharing port 8765 caused a confusing "stale server answering with
garbage" bug. A second instance should fail loudly.

**Windows timezone.** `zoneinfo` has no `America/Edmonton` key on a stock
Windows Python install. The congestion module falls back to a fixed offset.
Don't try to "fix" this with a `datetime.timezone` subclass — that throws.

**Editor line endings.** Git may warn about LF→CRLF. Harmless here; nothing
parses line endings meaningfully.

---

## Portability: what was fixed and why it matters

This repo was originally built on one machine with absolute paths hardcoded
throughout. It would not have run on anyone else's laptop. All fixed:

- `server.py` finds SUMO via `SUMO_BIN` → `PATH` → `SUMO_HOME` → default, and
  finds the map folder relative to its own file location
- `export_scene.py` same treatment, plus `SCENE_OUT` for the output path
- `start_3d.bat` uses `python` from `PATH` instead of globbing a local install
- `start_3d.sh` added for macOS and Linux
- `calgary/dt.poly.xml` had an absolute `polyconvert.typ.xml` path baked in by
  `polyconvert`; repointed to a relative form
- Personal paths scrubbed from `sim/`, `agent-tests/`, and prose in markdown/logs

**If you add code, keep paths relative to `Path(__file__).parent`.** That is the
single rule that keeps this runnable on other people's machines.

---

## Untested and unverified

Honest list. Don't assume these work.

- **Pointer-lock flight in a real browser.** Controls were driven through
  automated tests; nobody has hand-flown it on a GPU. Check WASD/Q/E and the
  mouse look before trusting them.
- **Frame rate.** An early 10 FPS reading came from a software renderer in a
  headless browser and is meaningless. Real GPU performance is unknown.
- **macOS and Linux.** `start_3d.sh` was written but never executed. SUMO
  resolves differently on macOS and the launcher is untested there.
- **Congestion layer geometry matching.** Known broken, details above.
- **Incident markers.** The live feed has been observed returning incidents far
  outside downtown Calgary, so markers legitimately often do not appear in view.

---

## AI tooling note

Parts of this project were built with AI assistance, and a few AI-authored
status documents were written and then deliberately **deleted**
(`TASK_COMPLETE.md`, `VERIFICATION.md`) because they contained inaccurate
claims and hardcoded local paths. Don't recreate them; the README and this file
are the accurate record.

One concrete lesson: a free-tier model rewrote a README and introduced three
factual errors — a `/stream` example in a format the server never emits, a
reference to a `NOTICE` file that was never created, and a control scheme that
didn't match the implementation. The `/stream` example in the current README was
regenerated from a real captured frame. **Verify AI-written documentation
against the running code.**

---

## Related work

An earlier effort structured this project for the **IEEE YP Industry Hackathon**
(snow plow routing, Stream 02 Case 2), as pre-existing infrastructure for a team
of four. That work lives in a separate repository and is **not** part of this one.

Most of what exists here was built before the hackathon began. If it's submitted
as hackathon work, that needs disclosing.

---

## Where to take it next

Ideas in rough order of value per unit of effort:

1. **Fix the congestion layer** (see the open bug above). Highest visibility —
   it's the most impressive-looking feature and currently the least functional.
2. **Bundle three.js locally** so the viewer works offline. Currently a CDN
   dependency, which will break at a conference with bad wifi.
3. **Replace randomTrips with something defensible.** Demand is the weakest part
   of the simulation. Real hourly counts would make every other result
   meaningful.
4. **Extract real signal timings from OSM** instead of relying on netconvert
   defaults.
5. **Add a routing layer** — this is what the hackathon version wanted, and the
   network is already the hard part.
6. **Compress `dt.osm` / `poly.osm`.** SUMO reads gzipped networks. They're left
   uncompressed here because nothing is over 50 MB, but it would cut 5 MB.