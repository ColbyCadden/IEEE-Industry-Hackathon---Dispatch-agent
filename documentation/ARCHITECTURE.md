# Architecture (text form)

For the submission form, which wants text or an image. The rendered diagram is
`architecture.html` — open it in any browser.

```
                    ┌──────────────────── LIVE DATA (real) ────────────────────┐
                    │                                                            │
        Calgary 311 ArcGIS service            311 CSV sample          travel times
        11 categories, 5-min refresh          (historic snapshot)     incidents · annual
                    │                                                            │
                    └──────────────────────────────┬─────────────────────────────┘
                                                   │
                    ┌──────────────────────────────▼─────────────────────────────┐
                    │  SCOUT AGENT        fetch, dedupe, filter to sim area      │
                    └──────────────────────────────┬─────────────────────────────┘
                                                   │  requests.csv  (269 open/overdue pins)
                    ┌──────────────────────────────▼─────────────────────────────┐
                    │  PRIORITY AGENT    score urgency 1–10                       │
                    │  ⚠ placeholder — invented scores, documented CSV contract │
                    └──────────────────────────────┬─────────────────────────────┘
                                                   │  priority_list.csv  (top 40 of 48)
                    ┌──────────────────────────────▼─────────────────────────────┐
                    │  ROUTE PLANNER                                                │
                    │    sumolib Dijkstra over the real road network              │
                    │    + Google OR-Tools multi-vehicle routing solver          │
                    │    balance: exactly 8 stops per crew, avoid closed roads    │
                    └───────┬──────────────────────────────────┬──────────────────┘
                            │ routes.json                      │ traffic plan
                            │ (routes, ETAs, polylines)        │ (speed + demand)
        ┌───────────────────▼──────────────────┐   ┌───────────▼──────────────────┐
        │  SUMO simulation                   │   │  3D CITY VIEWER (three.js)   │
        │  557 edges · 171 signals           │   │  live traffic · 311 pins      │
        │  TraCI over SSE                    │   │  5 crew routes · click-to-fix │
        └───────────────────┬──────────────────┘   └───────────┬──────────────────┘
                            │                                  │
                            └────────────────┬─────────────────┘
                                             │  operator sees the plan
                                             │
    ┌────────────────────────────────────────▼─────────────────────────────────────┐
    │  LIVE DECISION LAYER — the autonomous loop                                  │
    │                                                                              │
    │    ElevenLabs Conversational AI dispatch agent                              │
    │      asks which issue → asks what happened → classifies intent              │
    │      tools: find_open_requests()      record_311_update()                   │
    │                                                                              │
    │    "the debris on 8 Street was picked up"                                   │
    │        ↓                                                                     │
    │    live_updates.jsonl  →  pin turns closed  →  priority shifts              │
    │        ↓                                                                     │
    │    ROUTE PLANNER re-solves all five crews  →  routes redraw on screen       │
    └──────────────────────────────────────────────────────────────────────────────┘
```

## Design choices and why

**Files are the contract, not function calls.** Each agent reads and writes CSV/JSON in
`agents/data/`. Any agent can be run and tested alone, and any decision can be reproduced
from the exact inputs that produced it. Swapping the placeholder priority agent for a real
one changes nothing downstream — the planner only needs the columns in `priority_list.csv`.

**Optimise analytically, not by simulation.** The planner chooses routes against a closed-form
cost model (distance / speed limit) rather than booting SUMO to score every candidate plan.
That is why a full 40-stop re-plan takes about 7 seconds and can run mid-conversation.

**A dead source degrades, it does not abort.** Every fetch returns `(rows, meta)` and a failed
source yields zero rows with `meta.ok = False`. Empty categories are logged, not raised.

**Display layers never touch the simulation.** 311 pins, crew routes and the call panel are
read-only overlays. The only things that change traffic are the traffic agent's speed and
demand scaling.

**Corrections on real failures, discovered by running it:**

- The network is one-way in places, so arbitrary edges are not mutually reachable. The
  planner snaps every stop to the largest strongly-connected component (511 of 557 edges).
- English ConvAI agents are rejected by ElevenLabs unless the TTS model is turbo or flash.
  Found by probing the API, not from the docs.