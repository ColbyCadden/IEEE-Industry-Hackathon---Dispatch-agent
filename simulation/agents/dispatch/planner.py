#!/usr/bin/env python
"""
Route planner agent.

Reads   agents/data/priority_list.csv   (written by the priority agent)
        agents/data/live_updates.jsonl  (written by the calls agent)
        agents/data/control_plan.csv    (roads the traffic agent closed)
Writes  agents/data/routes.json         (read by the 3D viewer)

Routing: Dijkstra over the real SUMO network (sumolib, free/open source) with
free-flow travel time = length / (speed limit * speed_factor). Closed roads are
avoided with a heavy penalty (never impassable, so a stop is always reachable).
Sequencing: Google OR-Tools routing solver. Objective = total drive time plus
priority-weighted arrival time, so urgent stops are reached early. A naive
"priority order, nearest free team" dispatcher is computed as a baseline.

priority_list.csv contract (what the real priority agent must write):
    request_id, category, priority(1-10, 10 = most urgent), location,
    and either x,y (SUMO metres) or lon,lat.     rank / source_agent optional.

    python -m agents.dispatch.planner
"""
import argparse, csv, heapq, json, os, sys, time, warnings
warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "calgary3d", "server"))
from agents.dispatch import updates as U  # noqa: E402

PRIORITY_CSV = os.path.join(DATA, "priority_list.csv")
ROUTES_JSON = os.path.join(DATA, "routes.json")
NET = os.path.join(ROOT, "calgary", "dt.net.xml")

# Minutes of on-site work per category (assumption - tune freely).
SERVICE_MIN = {"signal": 40, "road_maintenance": 45, "pothole": 25, "markings": 30,
               "sign": 20, "debris": 15, "dead_animal": 10}
DEFAULT_SERVICE = 20
CLOSED_PENALTY = 8.0
STOPS_PER_TEAM = 8          # each crew gets exactly this many prioritised issues on ONE route

# DEMO start points for the 5 crews (SUMO metres). Replace with real yard locations.
TEAMS = [
    {"name": "Team 1", "color": "#ff453a", "start": (350, 300)},
    {"name": "Team 2", "color": "#0a84ff", "start": (2050, 320)},
    {"name": "Team 3", "color": "#30d158", "start": (1200, 950)},
    {"name": "Team 4", "color": "#ff9f0a", "start": (400, 1500)},
    {"name": "Team 5", "color": "#bf5af2", "start": (2050, 1500)},
]


# ---------------------------------------------------------------- network ---
class Router:
    def __init__(self, speed_factor=0.7, closed=()):
        import sumolib
        self.net = sumolib.net.readNet(NET)
        self.sf = speed_factor
        self.edges = {e.getID(): e for e in self.net.getEdges()}
        self.closed = set(closed)
        self.core = self._largest_scc()

    def _largest_scc(self):
        """Edges that can all reach each other (Kosaraju, iterative). The network is
        one-way in places, so routing between arbitrary edges can be impossible."""
        order, seen = [], set()
        for s in self.edges:
            if s in seen:
                continue
            seen.add(s)
            st = [(s, iter(self.edges[s].getOutgoing()))]
            while st:
                u, it = st[-1]
                for v in it:
                    if v.getID() not in seen:
                        seen.add(v.getID())
                        st.append((v.getID(), iter(v.getOutgoing())))
                        break
                else:
                    order.append(u)
                    st.pop()
        comp, n = {}, 0
        for s in reversed(order):
            if s in comp:
                continue
            comp[s] = n
            st = [s]
            while st:
                u = st.pop()
                for v in self.edges[u].getIncoming():
                    if v.getID() not in comp:
                        comp[v.getID()] = n
                        st.append(v.getID())
            n += 1
        big = max(set(comp.values()), key=list(comp.values()).count)
        return {e for e, c in comp.items() if c == big}

    def real(self, e):                       # seconds to traverse edge e
        return e.getLength() / max(e.getSpeed() * self.sf, 1.0)

    def snap(self, x, y):
        for r in (60, 200, 600):
            c = [(d, e) for e, d in self.net.getNeighboringEdges(x, y, r)
                 if e.allows("passenger") and e.getID() in self.core]
            if c:
                return min(c, key=lambda t: t[0])[1].getID()
        raise ValueError("no road within 600 m of (%s,%s)" % (x, y))

    def dijkstra(self, src):
        """Single-source over edges. Returns (cost_with_penalty, parent)."""
        dist, par, pq = {src: 0.0}, {}, [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, 1e18):
                continue
            for v in self.edges[u].getOutgoing():
                vid = v.getID()
                w = self.real(v) * (CLOSED_PENALTY if vid in self.closed else 1.0)
                if d + w < dist.get(vid, 1e18):
                    dist[vid], par[vid] = d + w, u
                    heapq.heappush(pq, (d + w, vid))
        return dist, par

    def leg(self, src, dst, dist, par):
        """Real time/distance/polyline for the src->dst path, mid-edge to mid-edge."""
        if src == dst:
            e = self.edges[src]
            return 0.0, 0.0, [[round(p[0], 1), round(p[1], 1)] for p in e.getShape()], 0
        if dst not in dist:
            return None
        ids = [dst]
        while ids[-1] != src:
            ids.append(par[ids[-1]])
        ids.reverse()
        es = [self.edges[i] for i in ids]
        secs = 0.5 * self.real(es[0]) + 0.5 * self.real(es[-1]) + sum(self.real(e) for e in es[1:-1])
        meters = 0.5 * es[0].getLength() + 0.5 * es[-1].getLength() + sum(e.getLength() for e in es[1:-1])
        pts = []
        for e in es:
            for p in e.getShape():
                q = [round(p[0], 1), round(p[1], 1)]
                if not pts or pts[-1] != q:
                    pts.append(q)
        return secs, meters, pts, sum(1 for i in ids if i in self.closed)


# --------------------------------------------------------------- inputs -----
def load_priority(path=PRIORITY_CSV):
    from export_scene import latlon_to_xy
    off = (-704066.21, -5657892.69)
    rows = []
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("x") and r.get("y"):
                x, y = float(r["x"]), float(r["y"])
            else:
                x, y = latlon_to_xy(float(r["lat"]), float(r["lon"]), off)
            rows.append({"id": r["request_id"], "category": r.get("category", ""),
                         "priority": float(r["priority"]), "x": x, "y": y,
                         "location": r.get("location", ""),
                         "source_agent": r.get("source_agent", "")})
    return rows


def load_closed():
    p = os.path.join(DATA, "control_plan.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as fh:
        return [r["edge_id"] for r in csv.DictReader(fh) if r.get("action") == "close"]


def load_all_requests():
    p = os.path.join(DATA, "requests.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as fh:
        return [{"id": r["id"], "category": r["category"], "status": r["status"],
                 "location": r["location"], "x": float(r["x"]), "y": float(r["y"])}
                for r in csv.DictReader(fh)]


def effective_stops(stops, st, extra_pool):
    """Apply live updates: drop resolved, shift priority, add escalated unlisted requests."""
    out, notes, listed = [], [], {s["id"] for s in stops}
    for s in stops:
        if s["id"] in st["resolved"]:
            notes.append("%s (%s) resolved by call - removed" % (s["id"], s["category"]))
            continue
        d = st["delta"].get(s["id"], 0)
        s = dict(s, priority=max(1.0, min(10.0, s["priority"] + d)), delta=d)
        if d:
            notes.append("%s priority %s%d -> %g" % (s["id"], "+" if d > 0 else "", d, s["priority"]))
        out.append(s)
    pool = {r["id"]: r for r in extra_pool}
    for rid, d in st["delta"].items():
        if d > 0 and rid not in listed and rid in pool and rid not in st["resolved"]:
            r = pool[rid]
            out.append({"id": rid, "category": r["category"], "priority": min(10.0, 5 + d),
                        "x": r["x"], "y": r["y"], "location": r["location"],
                        "source_agent": "live_call", "delta": d})
            notes.append("%s (%s) escalated by call - ADDED to list" % (rid, r["category"]))
    return out, notes


def pick_top(stops, n):
    """Highest effective priority first (original list order breaks ties); the rest
    stay on the bench and move up automatically when a listed issue is fixed."""
    order = {s["id"]: i for i, s in enumerate(stops)}
    ranked = sorted(stops, key=lambda s: (-s["priority"], order[s["id"]]))
    return ranked[:n], ranked[n:]


# --------------------------------------------------------------- solving ----
def solve(stops, teams, cost, service, time_limit=2):
    """cost[i][j] seconds between nodes (0..T-1 team starts, T.. stops). Returns
    per-team ordered lists of stop indices."""
    T, S = len(teams), len(stops)
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    n = T + S + 1                                  # +1 dummy end
    end = n - 1
    mgr = pywrapcp.RoutingIndexManager(n, T, list(range(T)), [end] * T)
    rt = pywrapcp.RoutingModel(mgr)

    mat = [[0] * n for _ in range(n)]
    for a in range(n - 1):
        for b in range(n - 1):
            if a != b:
                mat[a][b] = int(cost[a][b]) + (service[a - T] if a >= T else 0)
    cb = rt.RegisterTransitMatrix(mat)
    rt.SetArcCostEvaluatorOfAllVehicles(cb)
    rt.AddDimension(cb, 0, 86400, True, "Time")
    dim = rt.GetDimensionOrDie("Time")
    for k, s in enumerate(stops):
        dim.SetCumulVarSoftUpperBound(mgr.NodeToIndex(T + k), 0, int(round(s["priority"])))
    # every crew visits the same number of stops (balanced; exact when S is a multiple of T)
    unit = [0] * T + [1] * S + [0]
    cnt = rt.RegisterUnaryTransitVector(unit)
    rt.AddDimensionWithVehicleCapacity(cnt, 0, [-(-S // T)] * T, True, "Count")
    cd = rt.GetDimensionOrDie("Count")
    for v in range(T):
        cd.CumulVar(rt.End(v)).SetMin(S // T)
    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.seconds = time_limit
    sol = rt.SolveWithParameters(p)
    if not sol:
        raise RuntimeError("OR-Tools found no solution")
    plans = []
    for v in range(T):
        i, seq = rt.Start(v), []
        while not rt.IsEnd(i):
            nd = mgr.IndexToNode(i)
            if nd >= T:
                seq.append(nd - T)
            i = sol.Value(rt.NextVar(i))
        plans.append(seq)
    return plans


def evaluate(plans, stops, cost, service, T):
    """Weighted objective (sum priority * arrival time) + total drive time, minutes."""
    w = drive = 0.0
    for v, seq in enumerate(plans):
        t, node = 0.0, v
        for k in seq:
            d = cost[node][T + k]
            t += d
            drive += d
            w += stops[k]["priority"] * t
            t += service[k]
            node = T + k
    return {"weighted_arrival_min": round(w / 60, 1), "drive_min": round(drive / 60, 1)}


def baseline(stops, teams, cost, service):
    """Naive dispatcher: stops in priority order, each to whichever team reaches it first."""
    T = len(teams)
    free, node = [0.0] * T, list(range(T))
    plans = [[] for _ in range(T)]
    cap = -(-len(stops) // T)
    for k in sorted(range(len(stops)), key=lambda k: -stops[k]["priority"]):
        v = min((v for v in range(T) if len(plans[v]) < cap),
                key=lambda v: free[v] + cost[node[v]][T + k])
        free[v] += cost[node[v]][T + k] + service[k]
        node[v] = T + k
        plans[v].append(k)
    return plans


# ------------------------------------------------------------------ main ----
def plan(speed_factor=0.7, time_limit=6):
    t0 = time.time()
    st = U.state()
    pri = load_priority()
    stops, notes = effective_stops(pri, st, load_all_requests())
    stops, bench = pick_top(stops, STOPS_PER_TEAM * len(TEAMS))
    closed = load_closed()
    R = Router(speed_factor, closed)
    T = len(TEAMS)
    team_edges = [R.snap(*t["start"]) for t in TEAMS]
    for s in stops:
        s["edge"] = R.snap(s["x"], s["y"])
    nodes = team_edges + [s["edge"] for s in stops]
    sp = {e: R.dijkstra(e) for e in set(nodes)}
    N = len(nodes)
    cost = [[0.0] * N for _ in range(N)]
    legs = {}
    for i in range(N):
        for j in range(N):
            if i == j:
                continue
            lg = R.leg(nodes[i], nodes[j], *sp[nodes[i]])
            if lg is None:
                raise RuntimeError("unreachable: %s -> %s" % (nodes[i], nodes[j]))
            cost[i][j], legs[(i, j)] = lg[0], lg
    service = [SERVICE_MIN.get(s["category"], DEFAULT_SERVICE) * 60 for s in stops]

    plans = solve(stops, TEAMS, cost, service, time_limit) if stops else [[] for _ in TEAMS]
    obj = evaluate(plans, stops, cost, service, T)
    base_obj = evaluate(baseline(stops, TEAMS, cost, service), stops, cost, service, T) if stops else {}

    teams_out = []
    for v, seq in enumerate(plans):
        t, node, path, dist, drive, work, out_stops, cl = 0.0, v, [], 0.0, 0.0, 0.0, [], 0
        for k in seq:
            d_s, d_m, pts, ncl = legs[(node, T + k)]
            path += pts if not path else pts[1:]
            t += d_s
            drive += d_s
            dist += d_m
            cl += ncl
            s = stops[k]
            out_stops.append({"order": len(out_stops) + 1, "request_id": s["id"],
                "category": s["category"], "priority": s["priority"], "location": s["location"],
                "x": round(s["x"], 1), "y": round(s["y"], 1),
                "arrive_min": round(t / 60, 1), "service_min": service[k] // 60,
                "done_min": round((t + service[k]) / 60, 1), "leg_drive_min": round(d_s / 60, 1),
                "source_agent": s["source_agent"]})
            t += service[k]
            work += service[k]
            node = T + k
        teams_out.append({"team": TEAMS[v]["name"], "color": TEAMS[v]["color"],
            "start": {"x": TEAMS[v]["start"][0], "y": TEAMS[v]["start"][1]},
            "stops": out_stops, "path": path, "drive_min": round(drive / 60, 1),
            "work_min": round(work / 60, 1), "total_min": round(t / 60, 1),
            "distance_km": round(dist / 1000, 2), "closed_edges_on_route": cl})

    prev = None
    if os.path.exists(ROUTES_JSON):
        try:
            with open(ROUTES_JSON, encoding="utf-8") as fh:
                prev = json.load(fh)
        except ValueError:
            pass
    changes = list(notes)
    if prev:
        old = {t["team"]: t for t in prev["teams"]}
        for t in teams_out:
            pt = old.get(t["team"])
            if pt is None:
                continue
            a = [s["request_id"] for s in pt["stops"]]
            b = [s["request_id"] for s in t["stops"]]
            if a != b:
                changes.append("%s: route changed (%s -> %s min, %d -> %d stops)"
                               % (t["team"], pt["total_min"], t["total_min"], len(a), len(b)))
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "inputs": {"priority_file": PRIORITY_CSV,
                      "priority_source": sorted({s["source_agent"] for s in pri}),
                      "priority_rows": len(pri), "live_updates": len(U.load()),
                      "closed_edges_avoided": len(closed), "speed_factor": speed_factor},
           "objective": obj, "baseline_objective": base_obj, "changes": changes,
           "teams": teams_out, "stops_total": len(stops), "bench": len(bench),
           "stops_per_team": STOPS_PER_TEAM,
           "makespan_min": max([t["total_min"] for t in teams_out] or [0]),
           "solver_seconds": round(time.time() - t0, 2),
           "assumptions": {"service_min": SERVICE_MIN,
                           "travel": "free-flow = length / (speed limit x speed_factor); "
                                     "crews start at demo depots; plan is for a shift starting now"}}
    tmp = ROUTES_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    os.replace(tmp, ROUTES_JSON)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed-factor", type=float, default=0.7)
    ap.add_argument("--time-limit", type=int, default=6)
    a = ap.parse_args()
    if not os.path.exists(PRIORITY_CSV):
        print("ERROR: %s missing - run the priority agent (or: python -m agents.dispatch.fake_priority)"
              % PRIORITY_CSV, file=sys.stderr)
        return 2
    o = plan(a.speed_factor, a.time_limit)
    print("=== route planner: %d stops (%d on bench), %d teams, %ss ===" % (o["stops_total"], o["bench"], len(o["teams"]), o["solver_seconds"]))
    for t in o["teams"]:
        print("%s: %d stops  drive %sm + work %sm = %sm  %skm" % (t["team"], len(t["stops"]),
              t["drive_min"], t["work_min"], t["total_min"], t["distance_km"]))
        for s in t["stops"]:
            print("   %d. +%5sm  p%g %-16s %s" % (s["order"], s["arrive_min"], s["priority"],
                  s["category"], s["location"][:40]))
    print("objective", o["objective"], "| naive baseline", o["baseline_objective"])
    for c in o["changes"]:
        print(" *", c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
