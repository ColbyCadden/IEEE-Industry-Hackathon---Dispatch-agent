"""Assign scored tickets to C crews x K jobs, by priority or oldest-first (FIFO).

Two steps:
  1. Choose the day's work: the top C x K tickets in priority (or FIFO) order.
  2. Split that work into C compact groups of K jobs, minimising how far each crew drives:
     capacitated k-means, where every round assigns jobs to crew slots optimally (Hungarian
     algorithm) and then recentres each crew on its jobs. Best of many seeded starts.
Each crew's zone is the centre of its own jobs, and its jobs are listed in driving order.

Flexible crews (the agent plan; oldest-first stays at C x K):
  3. Split the fixed workforce (C x CREW_SIZE people) across crews by workload: the priority of the
     day's work around each crew. Every crew keeps enough people for its safety jobs.
  4. Each crew's job limit comes from its workers and how tight its jobs are:
     CREW_SIZE workers = K jobs; every 2 workers more or fewer = +/-1 job; every 2 jobs within
     HOP_KM of another job on the same crew (short hops) = +1 job, at most one per 2 workers;
     never more than MAX_JOBS.
  5. Crews over their limit release their lowest-priority jobs; then the next tickets in priority
     order go to one of their 2 nearest crews (within ADD_KM of its centre) that has room.
"""
import math
from itertools import permutations

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

CREWS = 8
JOBS_PER_CREW = 5
CITY_CENTRE = (51.045, -114.06)  # (lat, lon) reference point for zone labels
COMPASS = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
STARTS = 40                      # seeded restarts of the grouping; the most compact result wins
CREW_SIZE = 4                    # people in a standard crew (the parser assumes the same)
MIN_WORKERS, MAX_WORKERS = 3, 8  # flexible crews: smallest and largest crew
MAX_JOBS = 7                     # most jobs any crew takes in a day
HOP_KM = 1.0                     # jobs this close to another job on the same crew are short hops
ADD_KM = 6.0                     # an extra job may join a crew whose centre is at most this far away

KM_LAT = 111.32                                          # km per degree of latitude
KM_LON = 111.32 * math.cos(math.radians(CITY_CENTRE[0]))  # km per degree of longitude in Calgary

JOB_FIELDS = ["id", "type", "service_name", "community", "P", "safety", "lat", "lon", "reports"]


def zone_label(lat: float, lon: float) -> str:
    """8-point compass direction of (lat, lon) as seen from CITY_CENTRE."""
    dy = lat - CITY_CENTRE[0]
    dx = (lon - CITY_CENTRE[1]) * math.cos(math.radians(CITY_CENTRE[0]))
    angle = math.degrees(math.atan2(dy, dx)) % 360
    return COMPASS[int((angle + 22.5) // 45) % 8]


def km(a, b) -> float:
    """Straight-line distance in km between two (lat, lon) points (fine at city scale)."""
    return math.hypot((a[0] - b[0]) * KM_LAT, (a[1] - b[1]) * KM_LON)


def order_tickets(df: pd.DataFrame, order: str = "priority") -> pd.DataFrame:
    if order == "priority":
        return df.sort_values(["P", "requested_date", "id"], ascending=[False, True, True])
    if order == "fifo":
        return df.sort_values(["requested_date", "id"])
    raise ValueError(f"unknown order: {order!r}")


def compact_groups(points: np.ndarray, crews: int, jobs: int, starts: int = STARTS,
                   seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Split points (n x 2, in km) into `crews` groups of at most `jobs`, as tight as possible.

    Returns (label per point, group centres). Deterministic for a given seed.
    """
    n = len(points)
    k = min(crews, n)
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(starts):
        centres = points[rng.choice(n, k, replace=False)]
        for _ in range(100):
            slots = np.repeat(centres, jobs, axis=0)  # each crew offers `jobs` slots
            cost = ((points[:, None, :] - slots[None, :, :]) ** 2).sum(axis=2)
            rows, cols = linear_sum_assignment(cost)  # optimal job -> slot assignment
            labels = np.empty(n, dtype=int)
            labels[rows] = cols // jobs
            new = np.array([points[labels == g].mean(axis=0) if (labels == g).any() else centres[g]
                            for g in range(k)])
            if np.allclose(new, centres):
                break
            centres = new
        spread = sum(((points[labels == g] - centres[g]) ** 2).sum() for g in range(k))
        if best is None or spread < best[0] - 1e-9:
            best = (spread, labels.copy(), centres.copy())
    return best[1], best[2]


def driving_order(start, jobs: list[dict]) -> list[dict]:
    """Jobs in the order that minimises the drive from `start` through all of them."""
    if len(jobs) <= 1:
        return list(jobs)
    pts = [(j["lat"], j["lon"]) for j in jobs]
    if len(jobs) <= 7:  # exact: at most 5040 orders
        best = min(permutations(range(len(jobs))),
                   key=lambda o: km(start, pts[o[0]]) + sum(km(pts[o[i]], pts[o[i + 1]]) for i in range(len(o) - 1)))
        return [jobs[i] for i in best]
    left, here, out = list(range(len(jobs))), start, []  # larger crews: nearest next stop
    while left:
        nxt = min(left, key=lambda i: km(here, pts[i]))
        out.append(jobs[nxt])
        left.remove(nxt)
        here = pts[nxt]
    return out


def route_km(start, jobs: list[dict]) -> float:
    """Drive from `start` through the jobs in the order given."""
    stops = [start] + [(j["lat"], j["lon"]) for j in jobs]
    return sum(km(stops[i], stops[i + 1]) for i in range(len(stops) - 1))


def short_hops(jobs: list[dict]) -> int:
    """Jobs that sit within HOP_KM of another job on the same crew."""
    pts = [(j["lat"], j["lon"]) for j in jobs]
    return sum(1 for i, a in enumerate(pts) if any(i != k and km(a, b) <= HOP_KM for k, b in enumerate(pts)))


def job_limit(workers: int, jobs: list[dict], base: int = JOBS_PER_CREW) -> int:
    """Jobs a crew can do today: set by its workers, plus one per two short hops (one per two workers at most)."""
    hop_bonus = min(short_hops(jobs) // 2, workers // 2)  # bigger crews can take more of the short hops
    return min(MAX_JOBS, base + int((workers - CREW_SIZE) / 2) + hop_bonus)  # 3 or 5 people: no change


def split_workers(load: list[float], total: int, mins: list[int], most: int = MAX_WORKERS) -> list[int]:
    """Share `total` workers in proportion to load, each crew between its minimum and `most`."""
    out = list(mins)
    for _ in range(total - sum(out)):  # hand out one worker at a time to the crew furthest below its share
        room = [i for i in range(len(out)) if out[i] < most]
        if not room:
            break
        target = {i: load[i] / max(sum(load[k] for k in room), 1e-9) for i in room}
        out[max(room, key=lambda i: (target[i] * total - out[i], -i))] += 1
    return out


def flex_crews(df: pd.DataFrame, plan: dict, jobs: int = JOBS_PER_CREW) -> dict:
    """Give the plan flexible crews: workers by workload, job limits by workers and short hops."""
    crews = plan["crews"]
    n = len(crews)
    centres = [c["centroid"] for c in crews]
    ordered = list(order_tickets(df, "priority").itertuples(index=False))
    load = [0.0] * n  # workload: priority of every open ticket in each crew's area (its nearest crew)
    for r in ordered:
        load[min(range(n), key=lambda i: km((r.lat, r.lon), centres[i]))] += r.P
    mins = []
    for c in crews:  # never fewer people than the crew's safety jobs need
        keep = [j for j in c["jobs"] if j["safety"]]
        mins.append(next((w for w in range(MIN_WORKERS, MAX_WORKERS + 1)
                          if job_limit(w, keep, jobs) >= len(keep)), MAX_WORKERS))
    workers = split_workers(load, n * CREW_SIZE, mins)

    groups = [sorted(c["jobs"], key=lambda j: -j["P"]) for c in crews]
    for g, w in zip(groups, workers):
        while len(g) > job_limit(w, g, jobs):
            g.pop()  # lowest priority goes back to the pool
    taken = {j["id"] for g in groups for j in g}
    for r in ordered:  # next tickets in priority order fill the room that's left
        if r.id in taken:
            continue
        job = _job(r)
        for k in sorted(range(n), key=lambda i: km((r.lat, r.lon), centres[i]))[:2]:
            if km((r.lat, r.lon), centres[k]) <= ADD_KM and len(groups[k]) + 1 <= job_limit(workers[k], groups[k] + [job], jobs):
                groups[k].append(job)
                taken.add(job["id"])
                break
    return {"crews": [{**c, "workers": w, "limit": job_limit(w, g, jobs), "jobs": driving_order(c["centroid"], g)}
                      for c, w, g in zip(crews, workers, groups)]}


def _job(row) -> dict:
    return {
        "id": str(row.id),
        "type": str(row.type),
        "service_name": str(row.service_name),
        "community": str(row.community),
        "P": round(float(row.P), 4),
        "safety": bool(row.safety),
        "lat": float(row.lat),
        "lon": float(row.lon),
        "reports": int(row.reports),
    }


def make_plan(df: pd.DataFrame, order: str = "priority", crews: int = CREWS,
              jobs: int = JOBS_PER_CREW, seed: int = 0, flexible: bool | None = None) -> dict:
    """Build a day plan: {"crews": [{"crew", "zone", "centroid", "workers", "limit", "jobs": [...]}]}.

    Crews are numbered clockwise from north by where their jobs are, so numbering is stable.
    flexible (default: on for the priority plan, off for oldest-first) sizes crews by workload.
    """
    plan = _fixed_plan(df, order, crews, jobs, seed)
    if flexible if flexible is not None else order == "priority":
        return flex_crews(df, plan, jobs)
    return plan


def _fixed_plan(df: pd.DataFrame, order: str, crews: int, jobs: int, seed: int) -> dict:
    """Top crews x jobs tickets, split into compact groups of `jobs` (standard crews)."""
    chosen = list(order_tickets(df, order).head(crews * jobs).itertuples(index=False))
    if not chosen:
        return {"crews": [{"crew": i + 1, "zone": "-", "centroid": list(CITY_CENTRE),
                           "workers": CREW_SIZE, "limit": jobs, "jobs": []}
                          for i in range(crews)]}
    points = np.array([[r.lat * KM_LAT, r.lon * KM_LON] for r in chosen])
    labels, centres = compact_groups(points, crews, jobs, seed=seed)
    groups = []
    for g, (y, x) in enumerate(centres):
        lat, lon = y / KM_LAT, x / KM_LON
        members = [_job(r) for r, lab in zip(chosen, labels) if lab == g]
        groups.append(((lat, lon), driving_order((lat, lon), members)))
    while len(groups) < crews:  # fewer tickets than crews: the rest stay at the city centre, idle
        groups.append((CITY_CENTRE, []))

    def bearing(item):  # clockwise from north, as seen from the city centre
        (lat, lon), _ = item
        return math.degrees(math.atan2((lon - CITY_CENTRE[1]) * KM_LON, (lat - CITY_CENTRE[0]) * KM_LAT)) % 360

    groups.sort(key=bearing)
    return {"crews": [
        {
            "crew": i + 1,
            "zone": zone_label(lat, lon),
            "centroid": [round(float(lat), 6), round(float(lon), 6)],
            "workers": CREW_SIZE,
            "limit": jobs,
            "jobs": members,
        }
        for i, ((lat, lon), members) in enumerate(groups)
    ]}


if __name__ == "__main__":
    from dispatch.data_prep import load_clean_tickets
    from dispatch.scoring import score

    scored = score(load_clean_tickets())

    for order in ("priority", "fifo"):
        plan = make_plan(scored, order=order)
        ids = [j["id"] for c in plan["crews"] for j in c["jobs"]]
        sizes = [len(c["jobs"]) for c in plan["crews"]]
        drive = sum(route_km(c["centroid"], c["jobs"]) for c in plan["crews"])
        print(f"\n{order}: jobs={len(ids)} unique_ids={len(set(ids))} "
              f"workers={sum(c['workers'] for c in plan['crews'])} max_per_crew={max(sizes)} sizes={sizes} "
              f"driving={drive:.1f} km")
        for c in plan["crews"]:
            pts = [(j["lat"], j["lon"]) for j in c["jobs"]]
            spread = max((km(a, b) for a in pts for b in pts), default=0)
            print(f"  crew {c['crew']} {c['zone']:>2} {c['workers']} workers, limit {c['limit']}  "
                  f"P={sum(j['P'] for j in c['jobs']):.2f}  "
                  f"safety={sum(j['safety'] for j in c['jobs'])}  spread={spread:.1f} km  "
                  f"route={route_km(c['centroid'], c['jobs']):.1f} km")
        assert len(ids) == len(set(ids)), "a ticket is assigned twice"
        assert all(len(c["jobs"]) <= c["limit"] <= MAX_JOBS for c in plan["crews"]), "a crew is over its limit"
        assert sum(c["workers"] for c in plan["crews"]) == CREWS * CREW_SIZE, "workforce changed"
    print("\nCHECK OK: no duplicate ids, no crew over its limit, workforce fixed at 32")
