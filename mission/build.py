"""Build mission/data/mission.js for the Mission Control view: `python mission/build.py`.

Reads the dispatch engine's outputs (run `python -m dispatch.run` first), road-routes every crew
with the public OSRM demo server (cached in data/osrm_cache.json; straight lines if offline) and
writes one file the page loads with a <script> tag, so index.html also opens from file://.

Stdlib only. Timeline (illustrative, not measured): the sick call lands at 07:40, before
shift start, which is what the replan engine assumes (no jobs done yet). Crews leave their zone
base at 08:00, visit jobs nearest-first, spend SERVICE_MIN on each, and drive at OSRM free-flow
time x TRAFFIC_FACTOR.
"""
import csv
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dispatch.weights import WEIGHTS  # noqa: E402  (plain dicts, no pandas needed)

OUT = ROOT / "dispatch" / "outputs"
DATA = ROOT / "data" / "311_dispatch_sample.csv"
HERE = Path(__file__).resolve().parent
CACHE = HERE / "data" / "osrm_cache.json"
TARGET = HERE / "data" / "mission.js"

OSRM = "https://router.project-osrm.org/route/v1/driving/{a};{b}?overview=full&geometries=geojson"
SICK_CALL = 7 * 3600 + 40 * 60
DAY_START = 8 * 3600
SERVICE_MIN = 35
TRAFFIC_FACTOR = 1.25
FALLBACK_KMH = 35


# --- routing ----------------------------------------------------------------

def _km(a, b):
    """Ground distance in km between (lon, lat) points (equirectangular; fine at city scale)."""
    dx = (b[0] - a[0]) * 111.32 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (b[1] - a[1]) * 111.32
    return math.hypot(dx, dy)


class Router:
    def __init__(self):
        self.cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        self.online = True
        self.fallbacks = 0

    def leg(self, a, b):
        """Return (coords [[lon, lat]...], seconds) for driving a -> b."""
        key = f"{a[0]:.5f},{a[1]:.5f};{b[0]:.5f},{b[1]:.5f}"
        if key not in self.cache and self.online:
            try:
                with urllib.request.urlopen(OSRM.format(a=key.split(";")[0], b=key.split(";")[1]),
                                            timeout=20) as r:
                    route = json.load(r)["routes"][0]
                self.cache[key] = {"c": [[round(x, 6), round(y, 6)] for x, y in route["geometry"]["coordinates"]],
                                   "s": route["duration"]}
                time.sleep(0.25)  # be polite to the free demo server
            except Exception as e:  # offline or rate-limited: draw straight lines from here on
                print(f"  OSRM unavailable ({e}); using straight lines")
                self.online = False
        if key in self.cache:
            hit = self.cache[key]
            return [list(a)] + hit["c"] + [list(b)], hit["s"]
        self.fallbacks += 1
        return [list(a), list(b)], _km(a, b) / FALLBACK_KMH * 3600

    def save(self):
        CACHE.write_text(json.dumps(self.cache, separators=(",", ":")), encoding="utf-8")


def nearest_first(start, jobs):
    """Order jobs by greedy nearest neighbour from start (a simple stand-in for a route optimiser)."""
    left, here, out = list(jobs), start, []
    while left:
        nxt = min(left, key=lambda j: _km(here, (j["lon"], j["lat"])))
        left.remove(nxt)
        out.append(nxt)
        here = (nxt["lon"], nxt["lat"])
    return out


def route_plan(plan, router):
    """{crew: {"order": [job ids], "path": [[lon, lat]...], "legs": [(coords, secs)...]}} for a plan."""
    out = {}
    for c in plan["crews"]:
        base = (c["centroid"][1], c["centroid"][0])
        order = nearest_first(base, c["jobs"])
        stops = [base] + [(j["lon"], j["lat"]) for j in order] + ([base] if order else [])
        legs = [router.leg(stops[i], stops[i + 1]) for i in range(len(stops) - 1)]
        path = []
        for coords, _ in legs:
            path.extend(coords if not path else coords[1:])
        out[c["crew"]] = {"order": [j["id"] for j in order], "path": path, "legs": legs}
    return out


def timed_trip(legs, order, start):
    """Timestamps along the legs (service stops become a pause), plus per-job arrive/done times."""
    path, ts, events, t = [], [], {}, start
    for i, (coords, secs) in enumerate(legs):
        secs *= TRAFFIC_FACTOR
        seg = [_km(coords[k], coords[k + 1]) for k in range(len(coords) - 1)]
        total = sum(seg) or 1.0
        if not path:
            path.append(coords[0])
            ts.append(t)
        for k, d in enumerate(seg):
            t += secs * d / total
            path.append(coords[k + 1])
            ts.append(round(t, 1))
        if i < len(order):  # arrived at a job: hold position while the crew works
            events[order[i]] = {"arrive": round(t), "done": round(t + SERVICE_MIN * 60)}
            t += SERVICE_MIN * 60
            path.append(coords[-1])
            ts.append(round(t, 1))
    return path, ts, events


# --- data -------------------------------------------------------------------

def load(name):
    path = OUT / name
    if not path.exists():
        sys.exit(f"missing {path} - run `python -m dispatch.run` first")
    return json.loads(path.read_text(encoding="utf-8"))


def backlog():
    """Every open, dispatchable problem (same cleaning as dispatch.data_prep), as [lon, lat, safety]."""
    seen, out = set(), []
    with DATA.open(encoding="utf-8") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: (r["requested_date"], r["service_request_id"]))
    for r in rows:
        if r["status_description"] == "Closed" or not r["latitude"]:
            continue
        key = (r["service_name"], round(float(r["latitude"]), 5), round(float(r["longitude"]), 5))
        if key in seen or WEIGHTS.get(r["service_name"], 0) == 0:
            continue
        seen.add(key)
        out.append([round(float(r["longitude"]), 6), round(float(r["latitude"]), 6),
                    WEIGHTS[r["service_name"]] == 3])
    return out


def crew_of(plan):
    return {j["id"]: c["crew"] for c in plan["crews"] for j in c["jobs"]}


def main():
    am, noon, fifo = load("plan_8am.json"), load("plan_noon.json"), load("plan_fifo.json")
    metrics, event = load("metrics.json"), load("event.json")

    router = Router()
    print("routing oldest-first plan...")
    r_fifo = route_plan(fifo, router)
    print("routing 8 a.m. plan...")
    r_am = route_plan(am, router)
    print("routing replanned day...")
    r_noon = route_plan(noon, router)
    router.save()

    trips, done = [], {}
    for crew, r in r_noon.items():
        if not r["order"]:
            continue
        path, ts, events = timed_trip(r["legs"], r["order"], DAY_START)
        trips.append({"crew": crew, "path": path, "ts": ts})
        done.update(events)

    c_am, c_noon, c_fifo = crew_of(am), crew_of(noon), crew_of(fifo)
    jobs = {}
    for plan in (am, noon, fifo):
        for c in plan["crews"]:
            for j in c["jobs"]:
                jobs.setdefault(j["id"], {k: j[k] for k in ("id", "type", "community", "P", "safety", "lat", "lon")})
    for jid, j in jobs.items():
        j.update(am=c_am.get(jid), noon=c_noon.get(jid), fifo=c_fifo.get(jid), **done.get(jid, {}))
        j["status"] = ("moved" if j["am"] and j["noon"] and j["am"] != j["noon"]
                       else "dropped" if j["am"] and not j["noon"] else "kept" if j["am"] else "fifo_only")

    open_items = backlog()
    safety_open = sum(1 for b in open_items if b[2])
    end = max(t["ts"][-1] for t in trips)
    mission = {
        "meta": {"sick_call": SICK_CALL, "day_start": DAY_START, "day_end": end + 1800,
                 "service_min": SERVICE_MIN, "traffic_factor": TRAFFIC_FACTOR,
                 "routing": "OSRM road network" if router.fallbacks == 0 else
                            f"straight lines for {router.fallbacks} legs (offline)",
                 "safety_open": safety_open, "open": len(open_items)},
        "crews": [{"crew": c["crew"], "zone": c["zone"], "base": [c["centroid"][1], c["centroid"][0]]}
                  for c in am["crews"]],
        "jobs": list(jobs.values()),
        "routes": {name: {str(k): v["path"] for k, v in r.items() if v["order"]}
                   for name, r in (("fifo", r_fifo), ("am", r_am), ("noon", r_noon))},
        "trips": trips,
        "backlog": open_items,
        "metrics": metrics,
        "event": event,
    }
    TARGET.write_text("window.MISSION = " + json.dumps(mission, separators=(",", ":")) + ";\n", encoding="utf-8")

    m = metrics
    print(f"\nwrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size // 1024} KB)")
    print(f"  open problems {len(open_items)} (safety {safety_open})  |  FIFO safety {m['fifo']['safety']}  "
          f"agent safety {m['8am']['safety']}  |  replan moved {m['noon']['moved']} dropped {m['noon']['dropped']} "
          f"safety_dropped {m['noon']['safety_dropped']}")
    print(f"  day ends {int(end // 3600):02d}:{int(end % 3600 // 60):02d}  routing: {mission['meta']['routing']}")


if __name__ == "__main__":
    main()
