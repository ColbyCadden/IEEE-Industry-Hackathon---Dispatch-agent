# Traffic Agents

Two cooperating agents that turn **live Calgary/Alberta traffic data** into
**simulator control commands**.

```
 ┌──────────────┐   CSV    ┌────────────────┐  plan   ┌──────────────┐
 │  SCRAPER     │ ───────> │   SIMULATOR    │ ──────> │ SUMO / 3D    │
 │ agent        │          │ agent          │  POST   │ calgary3d    │
 └──────────────┘          └────────────────┘         └──────────────┘
  511 alerts                 fuse → model            demand scale,
  travel times               → optimize              speed scale,
  traffic counts             → rank edges            signal priority,
  live incidents                                    closed edges
```

## Quick start

```bash
# one shot: scrape, build a plan, push it to the running sim
.venv/Scripts/python.exe -m agents.run_pipeline --apply

# keep it running (re-scrapes every 5 minutes)
.venv/Scripts/python.exe -m agents.run_pipeline --apply --watch 300
```

The simulator server must be up first — double-click `start_here.bat`, or
`python -u calgary3d/server/server.py`, then open http://localhost:8765/.

## Agent 1 — scraper (`agents/scraper/`)

Pulls four sources, all free, no API keys:

| Output CSV | Source | Live? |
|---|---|---|
| `travel_times.csv` | calgary.ca travel-times page | **Yes** — measured, ~3 min |
| `alerts_511.csv` | `511.alberta.ca/Alert/GetUpdatedAlerts` | **Yes** — statewide, often empty |
| `incidents.csv` | `data.calgary.ca/resource/35ra-9556.json` | **Yes** — rolling window |
| `traffic_counts.csv` | trafficcounts.calgary.ca (9,230 segs) | No — historic annual volumes |

```bash
python -m agents.scraper.run                    # everything
python -m agents.scraper.run --only travel_times
python -m agents.scraper.run --counts-geometry  # add lon/lat
```

### The trafficcounts proxy

`trafficcounts.calgary.ca` hides its ArcGIS services behind server-resolved
hostnames (`GISServerExtName`), so direct access 404s. The site exposes its own
forwarder — but the **entire target URL including its query string** must be
percent-encoded as a single parameter:

```python
url = ("https://trafficcounts.calgary.ca/proxy/proxy.ashx?"
       + urllib.parse.quote(target_url_with_query, safe=""))
```

Passing it as `?url=...` fails with *"Proxy has not been set up for this URL."*

## Agent 2 — simulator (`agents/simulator/`)

1. **Loads** the CSVs and the 557 SUMO edges from `calgary3d/web/scene.json`.
2. **Fuses** them into a per-edge `pressure` (0–1):
   - live travel-time delay + duration → strongest signal
   - historic annual volume (log-scaled) → baseline
   - incidents, weighted by **recency**, applied per quadrant
   - statewide weather alerts → small global floor
3. **Optimizes** by grid-searching `demand_scale`, `speed_scale` and
   `signal_green_bonus` against an analytic cost model (delay² + spillback −
   relief). No SUMO boot needed to choose a plan, so it runs in ~1 s.
4. **Writes** `control_plan.csv` — one row per edge with its action.
5. **Applies** the plan via `POST /control`.

```bash
python -m agents.simulator.run           # plan only
python -m agents.simulator.run --json    # machine-readable
python -m agents.simulator.run --apply   # push to the live server
```

### Matching live corridors to SUMO edges

The CSVs name roads in city language; SUMO uses edge IDs like
`-1269868035#0`. Matching is **deliberately conservative** — Calgary reuses
names across the grid (`16 AVE NW` ≠ `16 AVE SE`), so a token must be rare in
the network to count as a match. Over-matching paints the whole city red;
the current run matches 8 of 68 corridors to 241 distinct edges, which is the
honest number.

### `control_plan.csv`

| Column | Meaning |
|---|---|
| `edge_id` | SUMO edge ID |
| `road_name` | Street name from the network |
| `pressure` | 0–1 congestion estimate |
| `delay_min` / `volume` | the live / historic numbers behind it |
| `sources` | which CSVs contributed (`travel_times`, `traffic_counts`, `incidents`, `alerts_511`) |
| `action` | `close` / `signal_priority` / `speed_relief` / `normal` |

## Honest limitations

- **The vehicles in the sim are still simulated.** These agents make the
  *control settings* respond to real conditions; they do not turn SUMO's
  synthetic traffic into real traffic.
- **Traffic counts are annual volumes**, not live speeds — study dates run
  2002–2018. They set a realistic baseline, nothing more.
- **Incidents are a rolling window**, not a live snapshot, so they're
  recency-weighted rather than treated as "happening now".
- **The corridor→edge match is fuzzy.** Only corridors naming roads that exist
  in the downtown SUMO network can be matched; peripheral routes (Stoney Trail,
  Deerfoot) fall outside it.
- **`signal_green_bonus` is planned but not yet wired** into per-signal timing;
  the server currently exposes only normal / all-red / all-green / flashing.

## Files

```
agents/
├── run_pipeline.py       orchestrator (--watch loops)
├── scraper/
│   ├── sources.py        one fetch_* per source
│   └── run.py            CLI → agents/data/*.csv
├── simulator/
│   ├── optimizer.py      fuse → model → optimize → plan
│   └── run.py            CLI → control_plan.csv (+ --apply)
└── data/                 the CSV contract between the two agents
```