# Calgary SUMO 3D Drone Traffic Simulation

A real-time 3D traffic simulation of Calgary's road network using SUMO (Simulation of Urban MObility) and three.js, with live incident data from the City of Calgary's open data portal.

## Features

| **Sim** | Runs downtown Calgary traffic in SUMO and lets you watch and control it |
| **3D city** | Fly around downtown Calgary in three.js, like a drone |
| **Live incidents** | Real road closures and crashes from the City of Calgary, on the map |
| **Control panel** | Change traffic speed, volume, and every traffic light, close roads, switch day/night |
| **Street names** | Real Calgary street names on the roads |

Everything runs from public data. **No API keys, no accounts, no paid services.**

**If you just want to look around, [start here](STATE.md) — including a known bug
in the congestion layer you should know about.**

## Setup — start to finish on a fresh laptop

Takes about 10 minutes. Nothing needs to be installed by hand except Python.

### What you need

| | Version | Why |
|---|---|---|
| **Python** | **3.14** | runs the backend |
| **SUMO** | **1.27.1** | the traffic simulator — installed automatically by pip in step 3 |
| **Git** | any recent | to clone the repo |
| **Browser** | Chrome, Firefox, Edge, Safari | any with WebGL (all of them) |

**No API keys. No accounts. No paid services.**

Node.js is **not** required — the viewer is plain HTML/JS with no build step.

### Step 1 — Install Python 3.14

Download from <https://www.python.org/downloads/> and run the installer.

**On Windows, tick "Add python.exe to PATH"** on the first screen. This is the
step people most often miss, and it causes a "python not found" error later.

Check it worked — you should see `Python 3.14.x`:

```bash
python --version
```

### Step 2 — Clone the repo

```bash
git clone https://github.com/semirhaji07/sumo-drone-traffic-sim.git
cd sumo-drone-traffic-sim
```

No write access needed to clone. To contribute you'll need to be added as a
collaborator — see *Working on this together* below.

### Step 3 — Install the Python packages

```bash
pip install -r calgary3d/requirements.txt
```

That's one command. It installs **eclipse-sumo 1.27.1** (the simulator itself)
plus the `traci` and `sumolib` Python modules, all pinned to matching versions.

### Step 4 — Start it

**Windows:** double-click `calgary3d\start_3d.bat`

**macOS / Linux:**
```bash
cd calgary3d
./start_3d.sh
```

**Or do it manually, either OS:**
```bash
cd calgary3d
python -u server/server.py
```

Then open **<http://localhost:8765/>**

The first start takes ~10 seconds while SUMO loads the network. You'll see the
city, then cars appear as the simulation warms up.

### Step 5 — Verify it worked

You should see:
- The 3D city with roads, buildings, parks and the Bow River
- Cars coloured by speed (green = moving, red = stopped)
- A control panel — press **Tab** to toggle it
- ~170–400 vehicles in the stats readout

### Troubleshooting

| Symptom | Fix |
|---|---|
| `python: command not found` | Reinstall Python and tick "Add to PATH", then open a **new** terminal |
| `sumo.exe not found` | `pip install -r calgary3d/requirements.txt` did not complete. Re-run it, then reopen the terminal |
| Port 8765 already in use | Another program is on that port. Use `python -u server/server.py --port 9000` and open `http://localhost:9000/` |
| Blank screen | Your browser has WebGL disabled, or you're offline (three.js loads from a CDN). Try a different browser |
| No cars appear | Wait ~15 s; SUMO injects traffic gradually. Check the server terminal for errors |
| `SUMO_HOME is not set properly` | Usually harmless. See the optional section below to silence it |

### Optional — setting SUMO_HOME

Only needed if SUMO complains about missing data files. On Windows, where SUMO is
installed under `Program Files` and doesn't always self-locate:

```powershell
# for this terminal session only
$env:SUMO_HOME = "C:\Program Files\Eclipse SUMO\Sumo-1.27.1"

# permanently, for your user account
[Environment]::SetEnvironmentVariable("SUMO_HOME", "C:\Program Files\Eclipse SUMO\Sumo-1.27.1", "User")
```

You can also copy `calgary3d/.env.example` to `calgary3d/.env` and set `SUMO_BIN`
to the full path of the executable. **Never commit a `.env`** — it's in
`.gitignore`.

The backend looks for SUMO in this order: the `SUMO_BIN` environment variable →
`sumo` on your `PATH` → `SUMO_HOME` → the installer's default location. It finds
the map folder relative to its own location, so the repo can live anywhere on
your disk.

## Controls (three.js viewer)

| Action | Control |
|--------|---------|
| Fly camera | Click the scene to capture the mouse, then look around |
| Move forward/back/left/right | W / S / A / D |
| Move down / up | Q / E |
| Boost speed | Hold **Shift** |
| Adjust fly speed | Scroll wheel |
| Orbit mode (toggle) | **C** — drag to orbit, scroll to zoom |
| Cinematic auto-orbit | **O** |
| Open/close control panel | **Tab** |
| Release the mouse | **Esc** |
| Close / reopen a road | Double-click a road segment |

## Control Panel Toggles

- **playbackRate** — simulation speed multiplier (0.1× – 10×)
- **speedScale** — vehicle speed visual multiplier
- **demandScale** — vehicle demand multiplier (affects randomTrips generation)
- **signalMode** — `normal` / `allRed` / `allGreen` / `flashing`
- **closeEdges** — click a road in the panel to close it; click again to reopen
- **reset** — restart simulation from time 0
- **day/night** — toggle time-of-day lighting

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/stream` | GET (SSE) | Per-frame vehicle positions, signal states, summary stats |
| `/control` | POST | Send control commands (playbackRate, demandScale, signalMode, closeEdges, reset) |
| `/congestion.json` | GET | Current per-edge congestion levels (0–1) and incident boosts |
| `/sources.json` | GET | Data provenance manifest (see below) |

### `/stream` Event Format
Each Server-Sent Event is one `data:` line of compact JSON. Coordinates are SUMO
metres (x east, y north) in the network's own frame, `angle` is degrees clockwise
from north, and speeds are m/s.

```json
{"t":1286.5,
 "v":[[2938,818.1,997.5,90.5,0.0],[2945,1030.1,965.1,182.4,0.0]],
 "tls":{"11013329563":"GGrr"},
 "stats":{"n":412,"meanSpeed":8.2,"halting":31,
          "control":{"playbackRate":1.0,"speedScale":1.0,"demandScale":1.0,
                     "signalMode":"normal","closeEdges":[]}}}
```

`v` rows are `[vehicleId, x, y, angleDeg, speedMs]`. `tls` carries only the
signals whose state changed since the previous frame, plus a full dump every 20
frames.

### `/control` Payload Example
```json
{"playbackRate": 2.0, "demandScale": 1.5, "signalMode": "allGreen"}
```

## Data Provenance — Read This First

**The ONLY live data in this simulation is the City of Calgary Current Traffic Incidents dataset.**
- Source: `data.calgary.ca` dataset **4jah-h97u**
- Licence: **Open Government Licence – City of Calgary**
- Refresh: approximately every 10 minutes
- Used: incident locations, types, and descriptions → visual markers + local congestion boosts

**Everything else is simulated / modelled, not measured:**

| Component | Source | Nature |
|-----------|--------|--------|
| Vehicle demand | SUMO `randomTrips` | **Random placeholder** — NOT real Calgary traffic counts |
| Signal timings | `netconvert` actuated defaults | **NOT** Calgary's real signal plans |
| Building heights | OSM `height` / `building:levels` tags where present; otherwise **pseudo-random 10–45 m** |
| Per-street congestion colouring | Typical-by-hour heuristic model + incident proximity boosts | **ESTIMATE** — NOT measured speed data |
| Road network, street names, building footprints, parks, water | OpenStreetMap (Overpass snapshot **2026-10-01**) | Licensed **ODbL** — © OpenStreetMap contributors |

**No free live per-street speed feed exists for Calgary.** Google, TomTom, and HERE all require paid API keys. The congestion colours you see are a heuristic model, not ground truth.

## Attribution & Licences

This repository **redistributes ODbL-licensed data** from OpenStreetMap.

| Component | Licence | Credit |
|-----------|---------|--------|
| Road network, street names, building footprints, parks, water | **ODbL 1.0** | **© OpenStreetMap contributors** — snapshot via Overpass 2026-10-01 |
| City of Calgary Current Traffic Incidents | **Open Government Licence – City of Calgary** | The City of Calgary |
| SUMO (simulation engine) | **EPL-2.0** | Eclipse SUMO project |
| three.js (3D renderer) | **MIT** | three.js authors |
| Project code (this repo) | **MIT** | Copyright (c) 2026 Semir Haji |

See `LICENSE` for the project's MIT licence text. Upstream credits are in the
table above and in the Data Provenance section.

## Project Structure

```
calgary3d/
  server/
    server.py        # TraCI + SSE backend (port 8765)
    export_scene.py  # Exports SUMO network to three.js scene.json
    congestion.py    # Congestion heuristic + incident boost
  web/
    index.html       # three.js viewer entry point
    app.js           # Viewer logic, controls, SSE client
    congestion.js    # Congestion layer + Data Sources panel
    style.css        # UI styling
    scene.json       # Exported 3D scene (roads, buildings, water, parks)
  data/
    sources_verified.json  # Data provenance manifest (served at /sources.json)
  requirements.txt   # Pinned Python packages (SUMO, traci, sumolib)
  .env.example       # Optional local overrides — copy to .env, never commit it
  start_3d.bat       # Windows one-click launcher
  start_3d.sh        # macOS / Linux launcher
calgary/
  dt.osm, dt.net.xml, dt.rou.xml, dt.poly.xml, dt.sumocfg  # SUMO model from OSM
sim/                   # Older synthetic SUMO test outputs
agent-tests/           # Logs from four original agent tests
STATE.md               # Project state, open bugs, decisions, gotchas  ← READ THIS
HISTORY.md             # How it was built, and what broke along the way
```

## Working on this together

### If you just want to look

Cloning is enough — no GitHub login needed. Follow the setup steps above.

### If you want to change things

You need write access. Ask Semir to add you as a collaborator:
**Repo → Settings → Collaborators → Add people**. You only need the
**Write** role; **Triage** and **Read** are not enough to push.

### The basic loop

```bash
git clone https://github.com/semirhaji07/sumo-drone-traffic-sim.git
cd sumo-drone-traffic-sim

# make your changes, then:
git status                  # check what you actually changed
git add <specific files>    # never `git add .` on a first pass — review first
git commit -m "Short description of the change"
git push
```

Pull before you start work, so you don't build on a stale copy:

```bash
git pull
```

### Using branches — don't push straight to `master`

The default branch is `master`. Direct pushes work, but they make it easy to
break everyone else's setup. **Branch instead:**

```bash
git checkout -b fix-congestion-colours   # creates and switches to a new branch
# ... make your changes ...
git add -p                               # add changes hunk by hunk, review as you go
git commit -m "Fix congestion colour ramp at low values"
git push -u origin fix-congestion-colours
```

`-u` links the branch to the remote so future pushes are just `git push`.
Then open a **Pull Request** on GitHub: go to the repo's *Pull requests* tab,
click *New pull request*, check the branch, write what changed and why, and
submit. Someone merges it into `master`.

`git add -p` is worth learning. It walks you through each change and asks
whether to stage it, so you never accidentally commit a debug file or an
editor backup.

### When two people edit the same file

This is normal and not a crisis. If you push and get rejected:

```bash
git pull --rebase
# resolve conflicts in the files git flags, then:
git add <resolved files>
git rebase --continue
git push
```

If a conflict looks unfixable, `git rebase --abort` returns you to where you
started. Nothing is lost.

### Commit messages that help

Write the **why**, not the which. The diff already shows which files changed.

- Good: `Fix congestion colour ramp so low levels don't render as free-flow`
- Bad: `updated stuff`, `fixes`

One logical change per commit. If you'd describe it with "and", split it.

### Never commit

- **`.env`** — any file holding API keys or tokens. Already in `.gitignore`.
- **`.pyc`, `__pycache__`** — build artefacts. Already ignored.
- **Large generated files** — re-run the export script instead.
- **Absolute local paths** like `C:/Users/yourname/...`. Use paths relative to
  the script file so the code works on every machine.

If you accidentally commit a secret, rotate the key **first**, then rewrite the
history. Deleting the file in a later commit does not remove it from history.

### Using Claude Code on this repo

Point it at the project and it can work on the code directly:

```bash
cd sumo-drone-traffic-sim
claude
```

Useful prompts that work well here:
- *"Why does the congestion layer sometimes show white roads when it's enabled?"*
- *"Add a traffic signal phase display to the control panel."*
- *"Explain what `export_scene.py` does, then add XYZ."*

Two habits that avoid a lot of pain: commit before you start a large change so
you can go back, and ask it to explain anything it edits before you accept it.

## Known Limitations

- Traffic demand is randomly generated, so congestion patterns are plausible but
  not a reproduction of a real Calgary commute.
- Signal timings are simulator defaults, so junction behaviour will not match
  what Calgary's signals actually do.
- The congestion colouring is an estimate (see Data Provenance), not measured
  speeds.
- The congestion layer and the Data Sources panel load as separate frontend
  modules; if `/congestion.json` is unavailable the layer falls back to bundled
  mock data and labels itself MOCK in the interface.
- three.js loads from a CDN, so the viewer needs an internet connection.
- Developed and tested on Windows. `start_3d.bat` is Windows-only, but
  `start_3d.sh` covers macOS and Linux, and the Python commands work anywhere
  SUMO 1.27.1 is installed.

## Author

**Semir Haji** — [@semirhaji07](https://github.com/semirhaji07) — semirhaji07@gmail.com

Built with AI coding assistance in places. See `CREDITS.md` for upstream
attribution and `HISTORY.md` for how it was built.