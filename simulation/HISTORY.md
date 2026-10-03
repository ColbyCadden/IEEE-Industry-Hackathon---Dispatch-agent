# Project History

A record of how this project was built, in case the reasoning behind any piece of
it ever comes up.

## How it came together

The project started as an experiment: take real downtown Calgary OSM road data,
convert it to a SUMO network, and drive it with TraCI. That part worked early —
`netconvert` produced a network with 171 signalised junctions from a single
Overpass query, which was more than expected.

The 3D viewer came later, once the traffic simulation was stable. The goal was a
Google-Maps-like look with a drone camera: white roads, grey extruded buildings,
green parks, blue river, vehicles coloured by speed like the Google Maps traffic
layer.

That took the longest to get right, mostly because of coordinate systems and
rendering bugs that looked like "the city is empty" rather than like errors.

## Environment it was developed on

- Windows 11, build 26200
- SUMO 1.27.1 (with GUI, GDAL, FFmpeg, OSG, GL2PS)
- Python 3.14.7
- `traci` / `sumolib` / `eclipse-sumo` all 1.27.1
- Node v26.7.0 present but unused — the project has no npm dependencies
- Verified in Edge and Chrome. Never opened in Firefox or Safari.

## Data pipeline

1. **Road network.** Overpass query, bbox `51.0405,-114.0850,51.0510,-114.0560`
   → `dt.osm`. Converted with `netconvert --guess-ramps --join-junctions
   --guess-signals --tl-logic.join --tls.default-type actuated` → `dt.net.xml`,
   171 `tlLogic` entries.

2. **Demand.** `randomTrips.py` — 3,000 passenger trips over 1,800 seconds,
   `--fringe-factor 10`, minimum distance 600 m, seed 1 → `dt.rou.xml`.
   A headless run completed 2,649 of 3,000 trips.

3. **Buildings, parks, water.** Second Overpass query → `poly.osm`, 918
   polygons, converted with `polyconvert` using
   `data/typemap/osmPolyconvert.typ.xml` → `dt.poly.xml`.

4. **3D scene.** `export_scene.py` reads the network and polygons, using the
   network's own projection and net offset, and writes `web/scene.json`:
   ~740 buildings, roads, street names, parks, water, 171 signals.

## Bugs that cost real time

These are the ones worth remembering, because they all presented as "the app is
broken" with no error message.

**Buildings and cars invisible.** Instanced meshes were being frustum-culled
against a wrong bounding sphere. Fixed with `frustumCulled = false` plus
camera-height-based vehicle scaling. Building rings also had an incorrect
transformation applied, and buildings were positioned at the wrong height.

**The whole city appeared in the wrong place.** The exporter used a hand-rolled
UTM conversion while SUMO was using its own projection and network offset, so
buildings, parks and water sat kilometres from the roads. Fixed by deriving
coordinates from the network's actual projection.

**Roads looked like pipes.** First implementation extruded a tube along each
edge. Replaced with flat ribbon geometry plus outlines and lane-centre
markings — that's what produces the map-like look.

**Cars rendered black.** The instance colour buffer wasn't being written, and
the material needed `vertexColors` on the instanced attributes.

**Traffic signals kept vanishing.** Each `/stream` frame only sends signals that
*changed*, so assigning that set wholesale erased every unchanged signal. Fixed
by merging into existing state.

**A blank page with no console error.** The road double-click handler was defined
but never actually installed during initialisation. It had to be extracted into
an `installRoadClick()` function and called explicitly.

**Two servers, one port.** A stale server was still bound to 8765 while a new one
started. `netstat` found two PIDs; one was answering `/stream` with garbage like
`: k`. Fixed by killing stale processes and setting
`allow_reuse_address = False` so a second instance fails loudly instead of
silently sharing the port.

**SUMO quit on startup in headless mode.** `dt.sumocfg` referenced
`maps.view.xml`, a SUMO-GUI-only style file. In TraCI mode that reference is
fatal. Removed.

**The server was unusable off this machine.** Absolute paths everywhere, including
the Python install location and the map directory. This only surfaced when the
package was tested in a clean folder — and the *first* test passed for the wrong
reason, because the stale server was quietly loading the original map instead of
the copy. Retested properly and confirmed from the log which `.sumocfg` it read.

## What was verified and how

- **Clean-clone test.** Clone the repo to a fresh directory, start the server,
  check all four routes return 200 and the vehicle stream emits real data. Done
  after every significant change. This is the test that catches portability bugs.
- **Control API tests.** `playbackRate`, `speedScale`, `demandScale`,
  `signalMode` (normal / all-red / all-green), `closeEdges`, reset, day/night.
  All-red stopped 127 cars within five seconds, which is the expected behaviour.
- **Screenshot review.** Headless Edge via CDP, inspected visually. This is how
  the coordinate, geometry and material bugs were found — they were invisible in
  logs.
- **Secret scan.** Grepped for API keys, tokens, private key headers and
  personal paths before every publish. Clean. The only historical matches were
  the words `api_key_required` and `api_key_cost` in `sources_verified.json`,
  which document Alberta 511's registration requirement.

## Data honesty

This was a deliberate priority throughout. The app labels in its UI what is live
(modelled or placeholder) because a traffic visualisation that implies real
measurements it doesn't have is actively misleading.

The rule applied consistently: if something is generated, modelled, or a
placeholder, the README says so plainly and so does the interface. The congestion
layer says it is an estimate. The data sources panel says which inputs are live,
static, modelled, or placeholder.

**Keep that discipline in any new work.**