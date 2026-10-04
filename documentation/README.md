# Calgary 311 Dispatch Agent

**Software and Computational Math stream — IEEE Young Professionals Industry Hackathon**

An autonomous agent team that decides which road and waste tickets Calgary 311 should send
crews to, in what order, and re-plans live when the public calls in with an update.

Runs on one laptop. All open-source software. Live municipal data.

---

## The problem

Calgary 311 receives thousands of road and waste tickets a day: potholes, debris, damaged
signs, dead animals, failed traffic signals.

If every crew takes the oldest ticket, a genuine safety hazard sits behind a backlog of small
complaints. Then a blizzard hits, or a crew calls in sick, and the 7 a.m. plan is already
wrong before the first truck leaves the yard.

**The user who feels this:** the 311 dispatcher and the crew lead, at 7 a.m., deciding who
goes where.

---

## What the software does

| Agent | Job | Real or assumed |
|---|---|---|
| **Scout** | Pulls live Calgary 311 data across 11 categories from the city's own ArcGIS service (the backend behind the city's public Live Maps), filtered to the simulated downtown area | **Real, live** |
| **Priority** | Scores each open ticket for urgency | **Placeholder** — made-up scores, documented contract for the real agent |
| **Route planner** | Sequences the work into 5 crew routes of exactly 8 stops each, using Dijkstra shortest paths over the real road network (sumolib) plus the Google OR-Tools vehicle routing solver | **Real** |
| **Dispatch (voice)** | An ElevenLabs Conversational AI agent that answers incoming 311 calls: asks which issue, asks what happened, records fixed / still there / urgent | **Real** |
| **Traffic** | Fuses live travel times, incidents and annual volume counts into a SUMO traffic plan | **Mixed** — see Provenance |
| **Visualisation** | 3D fly-over city: live traffic, 311 pins, 5 glowing crew routes with numbered stops and ETAs, call panel | **Real** |

---

## The autonomous reasoning loop

This is the core of the project:

```
city 311 data  ──►  scout  ──►  priority  ──►  route planner  ──►  SUMO + 3D viewer
                        ▲                                                │
                        │                                                │
              ElevenLabs voice agent  ◄── caller reports an update ───────┘
              (classify intent, match request, re-optimise all 5 crews)
```

**Data in, decision out.** A caller says *"the debris on 8 Street was picked up."* The agent
classifies the intent, matches it to the right request, marks it resolved, and the optimiser
re-solves the assignment for all five crews. The routes visibly redraw on screen.

---

## Measured results

From a clean run of 40 prioritised stops across 5 crews (8 stops per crew):

| | Optimised | Naive baseline (priority order, nearest free crew) |
|---|---|---|
| **Total drive time** | **38.6 min** | 73.8 min |
| Priority-weighted arrival | 18,410 | 22,178 |

**~1.9× less drive time for the same work.** The baseline is the obvious thing a dispatcher
does by hand: go down the priority list and send whoever is closest.

Per crew:

| Crew | Stops | Drive | On-site work | Total | Distance |
|---|---|---|---|---|---|
| Team 1 | 8 | 9.8 min | 200 min | 209.8 min | 5.73 km |
| Team 2 | 8 | 5.8 min | 185 min | 190.8 min | 3.39 km |
| Team 3 | 8 | 7.3 min | 185 min | 192.3 min | 4.25 km |
| Team 4 | 8 | 5.3 min | 170 min | 175.3 min | 3.08 km |
| Team 5 | 8 | 10.3 min | 170 min | 180.3 min | 5.75 km |

In a live demo session, 19 tickets were resolved through the voice agent and the plan
re-optimised on every one of them.

---

## Data provenance

Being precise about what is real:

| Layer | Status |
|---|---|
| 311 requests (live feed) | **Live** — city ArcGIS service, refreshed every 5 min |
| Your `311_dispatch_sample.csv` | **Historic snapshot** (late August), analysed for potholes/debris/signs/waste |
| Travel times | **Live** |
| Incidents | **Live**, rolling window |
| Traffic counts | **Historic annual volumes**, 2002–2018 — baseline only |
| Crew depots, on-site work times, urgency scores | **Our invented assumptions** for the demo |

The vehicles in the simulation are simulated. Real data drives the *settings*, not the cars.

---

## Install and run

Requires Python 3.10+ and internet access (pip, and the three.js CDN the viewer loads).

```bat
setup.bat        REM once: creates .venv, installs SUMO + OR-Tools
start_all.bat    REM server + browser + agents every 5 min
```

Open <http://localhost:8765/>.

Optional: put an ElevenLabs key in `.env` as `ELEVENLABS_API_KEY=...` to enable the voice
agent, and `ELEVENLABS_AGENT_ID=...` for a pre-built agent.

### Running the agents by hand

```bash
.venv/Scripts/python.exe -m agents.requests311.run     # fetch live 311 data
.venv/Scripts/python.exe -m agents.dispatch.fake_priority   # DEMO priority list
.venv/Scripts/python.exe -m agents.dispatch.planner     # plan 5 crew routes
.venv/Scripts/python.exe -m agents.simulator.run       # traffic plan
```

---

## Swapping in the real priority agent

`agents/dispatch/fake_priority.py` is a documented placeholder. The real agent only has to
write `agents/data/priority_list.csv` with these columns; the planner needs no changes:

```csv
request_id,category,priority,location,x,y,rank,source_agent
26-00733545,debris,9,"1819 8 ST SW, CALGARY, AB",1203.4,455.1,1,my_priority_agent
```

- `priority` — 1 to 10, 10 is most urgent
- `x`, `y` — SUMO metres (the planner also accepts `lon`, `lat` instead)

---

## Code map

```
simulation/
├── agents/
│   ├── requests311/run.py       scout + analyst: live 311 data -> requests.csv
│   ├── dispatch/
│   │   ├── fake_priority.py     DEMO priority list (replace me)
│   │   ├── planner.py           Dijkstra + OR-Tools -> routes.json
│   │   ├── calls.py             call handling, intent -> map/route update
│   │   ├── updates.py           live-update log, intent + address matching
│   │   └── elevenlabs_setup.py  configures the voice agent + its tools
│   ├── scraper/                 travel times, incidents, traffic counts
│   └── simulator/               traffic plan
├── calgary3d/
│   ├── server/server.py         TraCI/SUMO server, SSE, control + call APIs
│   ├── server/congestion.py     congestion model
│   └── web/                     three.js viewer, 311 pins, dispatch panel, call UI
└── calgary/                     SUMO network (downtown Calgary), routes, config
```

---

## Tech stack

All free and open source, no paid services except the optional ElevenLabs voice agent:

- **SUMO** (Eclipse) + **TraCI** — traffic simulation
- **three.js** — 3D viewer
- **Google OR-Tools** — vehicle routing solver
- **sumolib** — shortest paths over the real road network
- **ElevenLabs Conversational AI** — the voice agent (optional)
- Python standard library for everything else

---

## Scaling

- **Pilot:** one Roads operations team, one district. Connects to the 311 system the city
  already publishes publicly, plus crew GPS — a GPS unit and an API call, not new procurement.
- **Scale:** the optimiser is formulated as a multi-vehicle routing problem, so adding crews
  or districts is a parameter change, not a rewrite.
- **Beyond Calgary:** any city with a 311-style open-ticket system. The same priority +
  routing logic applies to utilities, waste collection and facility maintenance.
- **Cost:** the whole stack runs on free software on one laptop.

---

## Known limitations

- The priority agent is a placeholder with invented urgency scores.
- Crew depots and on-site work times are assumed, not real city data.
- Travel times are free-flow estimates from speed limits, not live congestion.
- The voice agent's tools need a public URL (tunnel) so ElevenLabs can reach the server;
  without one it can talk but cannot update the map.
- Address matching from speech is token-based. It rejects category mismatches and offers
  candidates when ambiguous, but is not geocoded.
- The simulated area is downtown Calgary only (about 2.4 × 1.8 km).

---

## Team

Built by four presenters for the IEEE YP Industry Hackathon, October 2–4 2026, Collision
Space, Hunter Hub, University of Calgary.

See the repository README for team members and handles.