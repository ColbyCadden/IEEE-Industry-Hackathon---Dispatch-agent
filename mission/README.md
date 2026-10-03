# 311 Mission Control

A city-wide 3D replay of the dispatch agent's day, built for the pitch. It plays the engine's
real plans over a dark 3D map of Calgary, with crews driving real road routes.

## Run it

- **Windows:** double-click `mission/start_mission.bat` (opens http://localhost:8800/)
- **Any OS:** `python -m http.server 8800` inside `mission/`, then open http://localhost:8800/
- **No server:** opening `mission/index.html` directly also works

Needs internet for the basemap tiles and fonts. The libraries (`vendor/`) and data (`data/`) are local,
so with no network the crews, routes and HUD still play on a black background.
**Record a backup video before judging.**

## Presenting

Press **P** (or click *Pitch mode*) to auto-play the whole story, about 75 seconds:

1. **City:** flies from the downtown skyline out to every open problem, with hazards as red beacons
2. **Oldest-first:** FIFO routes; 14 of 30 hazards left as pulsing red beacons
3. **Agent plan:** the beacons turn crew colours; 30/30 covered
4. **Sick call (07:40):** Crew 4 goes out; its safety jobs arc to Crew 5; 5 jobs deferred; 0 safety lost
5. **Run the day:** trucks drive real Calgary roads; beacons turn green as hazards are fixed
6. **Results:** scorecard

| Key | Action |
|---|---|
| P | pitch mode (auto-play from the start) |
| ← / → or 1–6 | previous / next / jump to scene |
| Space | play / pause the day |
| Click a crew | camera follows that truck (Esc to stop) |
| C | toggle slow camera orbit |
| H | hide the UI (clean screen recording) |

Use the slider and 0.5×–4× buttons to scrub the day. `index.html#5@0.5` opens the day halfway through.

## Rebuild after the engine changes

```bash
python -m dispatch.run      # writes dispatch/outputs/*.json
python mission/build.py     # writes mission/data/mission.js (stdlib only)
```

Road routes come from the public OSRM demo server and are cached in `data/osrm_cache.json`.
Offline, `build.py` falls back to straight lines and the page footer says so.

## What is real and what is illustrative

- **From the engine:** which crew gets which job, FIFO vs agent coverage, the replan
  (moved, deferred, safety lost), and P totals.
- **Real routing:** the road paths and free-flow drive times (OSRM on OpenStreetMap).
- **Illustrative:** the clock. It assumes 35 min per job, drive time × 1.25, an 08:00 start
  and a 07:40 sick call (before shift start, which is what the replan engine assumes).
  Within each crew, jobs are visited nearest-first.

Map data © OpenStreetMap contributors, tiles by OpenFreeMap / OpenMapTiles, terrain shading from
AWS Terrain Tiles. deck.gl (MIT) and MapLibre GL JS (BSD-3) are vendored in `vendor/`.
