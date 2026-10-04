# Results and evaluation

## How to reproduce

```bash
setup.bat
.venv\Scripts\python.exe -m agents.run_pipeline          # fetch, plan, no apply
.venv\Scripts\python.exe -m agents.dispatch.planner --json
```

The planner writes `agents/data/routes.json` with the plan, the objective value, and the
baseline it was compared against.

---

## Headline numbers

A clean run with no live call history: 40 prioritised stops, 5 crews, 8 stops each.

| Metric | Optimised | Naive baseline | Change |
|---|---|---|---|
| Total drive time | **38.6 min** | 73.8 min | **1.9× less** |
| Priority-weighted arrival (min·priority) | **18,411** | 22,178 | **17% lower** |
| Solve time | **7.3 s** | — | fast enough to re-plan mid-shift |
| Stops per crew | **8 / 8 / 8 / 8 / 8** | uneven | balanced |

**The baseline is the "lazy" answer:** walk the priority list, send whichever crew is
closest. It is what a dispatcher does by hand, and it is also what `agent_starter.py` in the
parent repo does with oldest-first FIFO.

### Per crew

| Crew | Stops | Drive (min) | On-site work (min) | Total (min) | Distance (km) |
|---|---|---|---|---|---|
| Team 1 | 8 | 9.8 | 200 | 209.8 | 5.73 |
| Team 2 | 8 | 5.8 | 185 | 190.8 | 3.39 |
| Team 3 | 8 | 7.3 | 185 | 192.3 | 4.25 |
| Team 4 | 8 | 5.3 | 170 | 175.3 | 3.08 |
| Team 5 | 8 | 10.3 | 170 | 180.3 | 5.75 |

Longest crew day: 209.8 min.

---

## The improvement round

The rubric asks for one visible improvement step against a baseline. There are two.

**1. Optimiser vs dispatcher.** Same 40 tickets, same crews. The routing solver cuts total
drive time from 73.8 to 38.6 min (1.9×). This is the `objective` vs `baseline_objective`
fields in `routes.json`.

**2. The plan improves as calls come in.** Every recorded caller update re-solves the
assignment. Observed live:

| Event | Effect |
|---|---|
| Caller reports debris picked up | Stop removed from a crew's route, next-ranked reserve stop promoted, that crew's total drops |
| Caller reports a sign still blocking traffic | Priority +3, stop enters the top 40, a lower-priority stop is pushed to the bench, affected crews re-route |
| 19 tickets resolved across a session | Plan re-optimised on all 19; no manual re-planning |

The reserve bench is what keeps crews at 8: `priority_list.csv` holds 48 rows and the planner
uses the top 40, so a fix promotes the next candidate automatically.

---

## Scoring experiment (from the parent repo)

The dispatcher's own scoring work in `dispatch/` compared priority-weighted assignment
against oldest-first FIFO:

- Scored agent ahead on safety weighting in **30 of 40** test cases
- Held its ranking in **16 of 16** sensitivity variations

---

## Live data volumes

One scout cycle, filtered to the simulated downtown area (2.4 × 1.8 km):

| Category | Fetched city-wide | In sim area |
|---|---|---|
| Signs | 1,063 | 119 |
| Road maintenance | 1,220 | 15 |
| Traffic signals | 1,021 | 42 |
| Dead animals | 821 | 21 |
| Debris | 745 | 49 |
| Potholes | 445 | 15 |
| Pavement markings | 75 | 3 |
| Snow & ice | 0 | 0 |
| Street cleaning | 0 | 0 |
| **Total in view** | **~5,400** | **265** |

269 open/overdue pins after deduplication, including the CSV snapshot.

Three categories return zero because their live layers are empty and nothing in the CSV
falls downtown — not a bug.

---

## Files in this folder

| File | What it is |
|---|---|
| `routes_latest.json` | The most recent plan: per-crew routes, polylines, ETAs, objective vs baseline, list of what changed |
| `requests_in_sim_area.csv` | Every 311 request currently pinned in the simulation |
| `live_updates.csv` | Every caller update recorded through the voice agent |