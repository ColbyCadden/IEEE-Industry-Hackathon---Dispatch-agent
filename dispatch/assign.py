"""Assign scored tickets to C crews x K jobs, by priority or oldest-first (FIFO)."""
import math

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

CREWS = 8
JOBS_PER_CREW = 5
CITY_CENTRE = (51.045, -114.06)  # (lat, lon) reference point for zone labels
COMPASS = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]

JOB_FIELDS = ["id", "type", "service_name", "community", "P", "safety", "lat", "lon", "reports"]


def zone_label(lat: float, lon: float) -> str:
    """8-point compass direction of (lat, lon) as seen from CITY_CENTRE."""
    dy = lat - CITY_CENTRE[0]
    dx = (lon - CITY_CENTRE[1]) * math.cos(math.radians(CITY_CENTRE[0]))
    angle = math.degrees(math.atan2(dy, dx)) % 360
    return COMPASS[int((angle + 22.5) // 45) % 8]


def make_zones(df: pd.DataFrame, crews: int = CREWS, seed: int = 0) -> np.ndarray:
    """KMeans centroids (crews x [lat, lon]) over all scored tickets."""
    km = KMeans(n_clusters=crews, n_init=10, random_state=seed)
    km.fit(df[["lat", "lon"]].to_numpy())
    return km.cluster_centers_


def order_tickets(df: pd.DataFrame, order: str = "priority") -> pd.DataFrame:
    if order == "priority":
        return df.sort_values(["P", "requested_date", "id"], ascending=[False, True, True])
    if order == "fifo":
        return df.sort_values(["requested_date", "id"])
    raise ValueError(f"unknown order: {order!r}")


def fill(ordered: pd.DataFrame, centroids: np.ndarray, jobs: int = JOBS_PER_CREW) -> list[list[dict]]:
    """Walk tickets in order; each goes to the nearest crew with space. Stops when all are full."""
    crews = len(centroids)
    loads: list[list[dict]] = [[] for _ in range(crews)]
    total = 0
    for row in ordered.itertuples(index=False):
        if total >= crews * jobs:
            break
        dist = np.hypot(centroids[:, 0] - row.lat, centroids[:, 1] - row.lon)
        for c in np.argsort(dist, kind="stable"):
            if len(loads[c]) < jobs:
                loads[c].append(_job(row))
                total += 1
                break
    return loads


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
              jobs: int = JOBS_PER_CREW, seed: int = 0) -> dict:
    """Build a day plan: {"crews": [{"crew", "zone", "centroid", "jobs": [...]}]}."""
    centroids = make_zones(df, crews, seed)
    loads = fill(order_tickets(df, order), centroids, jobs)
    return {"crews": [
        {
            "crew": i + 1,
            "zone": zone_label(*centroids[i]),
            "centroid": [round(float(centroids[i][0]), 6), round(float(centroids[i][1]), 6)],
            "jobs": loads[i],
        }
        for i in range(crews)
    ]}


if __name__ == "__main__":
    from dispatch.data_prep import load_clean_tickets
    from dispatch.scoring import score

    try:
        scored = score(load_clean_tickets())
    except ImportError:
        # TEMPORARY test table: weights.py is still a placeholder. Not the real table.
        print("(weights.py not ready - using temporary test weights)")
        tmp_weights = {
            "Roads - Pothole Maintenance": 3,
            "Roads - Signs - Missing - Damaged": 3,
            "Roads - Signs - Traffic and Roadmarking": 3,
            "Roads - Debris on Street/Sidewalk/Boulevard": 2,
            "Roads - Signs - Parking": 1,
            "WRS - Waste - Residential": 1,
            "WRS - Commercial Collection Services": 1,
            "WRS - New Service - Carts": 1,
        }
        tmp_safety = set(list(tmp_weights)[:4])
        tmp_short = {name: name.split(" - ", 1)[-1] for name in tmp_weights}
        scored = score(load_clean_tickets(), weights=tmp_weights,
                       safety_types=tmp_safety, short_names=tmp_short)

    for order in ("priority", "fifo"):
        plan = make_plan(scored, order=order)
        ids = [j["id"] for c in plan["crews"] for j in c["jobs"]]
        sizes = [len(c["jobs"]) for c in plan["crews"]]
        print(f"\n{order}: jobs={len(ids)} unique_ids={len(set(ids))} "
              f"max_per_crew={max(sizes)} sizes={sizes}")
        for c in plan["crews"]:
            print(f"  crew {c['crew']} {c['zone']:>2} {c['centroid']}  "
                  f"P={sum(j['P'] for j in c['jobs']):.2f}  "
                  f"safety={sum(j['safety'] for j in c['jobs'])}")
        assert len(ids) == 40 and len(set(ids)) == 40 and max(sizes) <= 5
    print("\nCHECK OK: both plans have 40 jobs, no duplicate ids, no crew over 5")
