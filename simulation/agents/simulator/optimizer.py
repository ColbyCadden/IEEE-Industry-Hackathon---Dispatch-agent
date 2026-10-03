"""
Simulator agent - reads the scraper's CSVs, builds a demand + congestion
model, and emits SUMO-ready control commands.

Pipeline:
    CSVs -> load -> fuse into a per-edge signal -> score corridor demand
         -> compute recommended simulator control (speed scale, demand scale,
            signal timing, closed edges) -> write control_plan.csv

Design notes
------------
* The SUMO network in calgary/ has ~557 named edges in scene.json. The live
  CSVs are city-wide and use Calgary street names, not SUMO edge IDs, so we
  map by road-name token overlap and fall back to a global demand scale when
  no name matches.
* Optimization objective is a simple, explainable cost:
      cost = w_delay * mean_delay + w_queue * mean_queue - w_throughput * throughput
  We sweep candidate control settings, score each with a small analytic
  demand/capacity model, and pick the best. This keeps the agent deterministic
  and fast - it does not need to boot SUMO to choose a plan.
* Apply with --apply to POST the plan to a running simulator server.
"""
import csv
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

# agents/simulator/optimizer.py  ->  agents/simulator  ->  agents  ->  repo root
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(HERE, "data")
ROOT = os.path.dirname(HERE)
SCENE = os.path.join(ROOT, "calgary3d", "web", "scene.json")


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_travel_times(path=None):
    """Live measured drive times. Returns list of corridor dicts."""
    path = path or os.path.join(DATA_DIR, "travel_times.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_incidents(path=None):
    path = path or os.path.join(DATA_DIR, "incidents.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_alerts(path=None):
    path = path or os.path.join(DATA_DIR, "alerts_511.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_counts(path=None):
    """Historic annual volumes keyed by location description."""
    path = path or os.path.join(DATA_DIR, "traffic_counts.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_scene_edges(path=None):
    """SUMO road edges from scene.json: {edge_id: {name, speed, lanes}}."""
    path = path or SCENE
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        scene = json.load(fh)
    out = {}
    for r in scene.get("roads", []):
        out[r.get("id")] = {
            "name": (r.get("name") or "").upper(),
            "speed": _num(r.get("speed"), 13.9),
            "lanes": _num(r.get("lanes"), 1),
        }
    return out


# --------------------------------------------------------------------------
# Fusing CSV sources into a demand signal
# --------------------------------------------------------------------------
def _tokens(name):
    """Road-name tokens used for fuzzy matching against SUMO edge names.

    Calgary's downtown grid is almost entirely NUMERIC ("2 STREET SW",
    "16 AVENUE NW"), so digits are first-class tokens here - dropping them
    would leave almost nothing to match on.
    """
    s = (name or "").upper()
    s = re.sub(r"\b(AT|NB|SB|EB|WB)\b", " ", s)
    s = re.sub(r"\b(NORTHBOUND|SOUTHBOUND|EASTBOUND|WESTBOUND)\b", " ", s)
    s = re.sub(r"\b(VI[A-Z]*|TO|FROM|AND|NEAR|OF|THE|WITH)\b", " ", s)

    # Normalise street-type abbreviations so "ST" == "STREET", "AVE" == "AVENUE".
    raw = [p for p in re.split(r"[^A-Z0-9]+", s) if p]
    out = []
    for p in raw:
        if p.isdigit():
            # Keep digits as bare tokens; street types are separate words.
            out.append(p)
            continue
        if p in _TYPE_CANON:
            out.append(_TYPE_CANON[p])
            continue
        if len(p) > 2 and p.isalpha():
            out.append(p)

    stop = {"STREET", "AVENUE", "ROAD", "TRAIL", "BOULEVARD", "BLVD", "DRIVE",
            "WAY", "CRESCENT", "COURT", "PLACE", "TERRACE", "HIGHWAY", "PARKWAY",
            "LANE", "CRES", "ROW", "WALK", "CLOSE", "GARDENS", "PARK", "RIDGE",
            "HILL", "HILLS", "VALLEY", "MEWS", "LINK", "SQUARE", "POINT", "LOOP",
            "ALLEY", "MIN", "DELAY", "VIA", "INTERSECTION", "NORTH", "SOUTH",
            "EAST", "WEST", "BOULEVARD"}
    return {p for p in out if p not in stop}


_TYPE_CANON = {
    "ST": "STREET", "STR": "STREET", "AVE": "AVENUE", "AV": "AVENUE",
    "RD": "ROAD", "DR": "DRIVE", "BLVD": "BOULEVARD", "BV": "BOULEVARD",
    "TR": "TRAIL", "TL": "TRAIL", "CR": "CRESCENT", "CT": "COURT",
    "PL": "PLACE", "TER": "TERRACE", "HWY": "HIGHWAY", "PKWY": "PARKWAY",
    "LN": "LANE", "CRES": "CRESCENT", "ROW": "ROW", "WALK": "WALK",
    "CL": "CLOSE", "GDNS": "GARDENS", "PK": "PARK", "RDGE": "RIDGE",
    "HILL": "HILL", "VAL": "VALLEY", "MEWS": "MEWS", "SQ": "SQUARE",
    "PT": "POINT", "ALY": "ALLEY", "WY": "WAY", "TRL": "TRAIL",
    "COUNTRY": "COUNTRY", "HILLS": "HILLS", "GLENMORE": "GLENMORE",
}


def _edge_signature(name):
    """Canonical 'NAME + number' key, e.g. 'STONEY' + '1' -> 'STONEY 1'."""
    s = (name or "").upper()
    nums = re.findall(r"\b(\d+)\b", s)
    words = [w for w in re.findall(r"[A-Z]{3,}", s) if w not in
             {"STREET", "AVENUE", "BOULEVARD", "TRAIL", "ROAD", "DRIVE",
              "HIGHWAY", "PARKWAY", "CRESCENT", "COURT", "PLACE", "TERRACE",
              "COUNTRY", "HILLS", "GLENMORE", "VALLEY", "RIDGE", "PARK"}]
    return (" ".join(sorted(set(words))), tuple(sorted(set(nums))))


def build_demand_model(travel, incidents, counts, edges):
    """Fuse the three CSVs into a per-edge congestion + demand estimate.

    Returns dict edge_id -> {
        'name', 'pressure' 0..1, 'delay_min', 'volume', 'sources' [..]
    }
    """
    demand = defaultdict(lambda: {"name": "", "pressure": 0.0, "delay_min": 0.0,
                                  "volume": 0.0, "sources": []})

    # --- 1. live travel-time corridors -> pressure on matching SUMO edges ---
    # Index SUMO edges by token, but require a DISCRIMINATING token match:
    # a bare street number ("2") is far too common to match on alone.
    token_index = defaultdict(list)
    for eid, meta in edges.items():
        toks = _tokens(meta["name"])
        for t in toks:
            token_index[t].append(eid)

    def find_edges(label):
        """SUMO edges plausibly named by `label`, or empty set.

        Matching is intentionally conservative. Calgary names are reused
        across the grid ("16 AVE NW" and "16 AVE SE" are different roads), so
        a bare number match is rejected unless the number is rare in the
        network. Over-matching here would paint the whole city red.
        """
        toks = _tokens(label)
        if not toks:
            return set()
        nums = {t for t in toks if t.isdigit()}
        words = toks - nums

        # A rare road word is a strong signal.
        strong = {t for t in words if len(t) >= 4 and len(token_index.get(t, [])) <= 60}
        if strong:
            hits = set()
            for t in strong:
                hits.update(token_index.get(t, []))
            if hits:
                return hits

        # Fall back to a street number, but only if it is not ubiquitous.
        rare_nums = {n for n in nums
                     if 0 < len(token_index.get(n, [])) <= 40}
        if rare_nums:
            hits = set()
            for n in rare_nums:
                hits.update(token_index.get(n, []))
            if hits:
                return hits
        return set()

    matched_corridors = 0
    for row in travel:
        delay = _num(row.get("delay_min"))
        minutes = _num(row.get("minutes"))
        # Match on every named road in the corridor (origin/destination/via).
        label = " ".join(str(row.get(k) or "") for k in
                         ("origin", "destination", "via", "corridor"))
        hits = find_edges(label)
        if not hits:
            continue
        matched_corridors += 1
        # Longer corridor + more delay -> more pressure on each hit edge.
        weight = min(1.0, delay / 5.0) * 0.6 + min(1.0, minutes / 45.0) * 0.4
        for eid in hits:
            d = demand[eid]
            d["name"] = d["name"] or edges[eid]["name"]
            d["pressure"] = max(d["pressure"], weight)
            d["delay_min"] = max(d["delay_min"], delay)
            if "travel_times" not in d["sources"]:
                d["sources"].append("travel_times")

    # --- 2. historic volumes -> baseline demand on named roads -------------
    for row in counts:
        vol = _num(row.get("volume"))
        if vol <= 0:
            continue
        hits = find_edges(row.get("description"))
        if not hits:
            continue
        # Log-scaled: 10k+ vehicles/day is a major artery.
        base = min(1.0, math.log10(max(vol, 1)) / 4.6)
        for eid in hits:
            d = demand[eid]
            d["name"] = d["name"] or edges[eid]["name"]
            d["volume"] = max(d["volume"], vol)
            # Volume lifts pressure but never overrides live measurement.
            d["pressure"] = max(d["pressure"], base * 0.55)
            if "traffic_counts" not in d["sources"]:
                d["sources"].append("traffic_counts")

    # --- 3. live incidents -> hard pressure near incident quadrants --------
    # NOTE: the Socrata feed is a rolling window of recent incidents, not a
    # snapshot of "right now". Weight it by recency and cap the total lift so
    # a busy day cannot saturate the entire network.
    quadrant_pressure = defaultdict(float)
    for row in incidents:
        q = (row.get("quadrant") or "").strip().upper()
        desc = (row.get("description") or "").upper()
        modified = str(row.get("modified_utc") or "")

        recency = 0.5  # default for unknown age
        if len(modified) >= 16:
            try:
                when = datetime.strptime(modified[:16], "%Y-%m-%d %H:%M")
                hours = (datetime.now(timezone.utc).replace(tzinfo=None) - when).total_seconds() / 3600.0
                if hours <= 2:
                    recency = 1.0
                elif hours <= 12:
                    recency = 0.75
                elif hours <= 48:
                    recency = 0.45
                else:
                    recency = 0.2
            except ValueError:
                pass

        severity = 1.0
        if "fatal" in desc or "closure" in desc:
            severity = 1.5
        elif "blocking" in desc or "stalled" in desc:
            severity = 1.2
        quadrant_pressure[q] += severity * recency

    # Normalise so a quadrant tops out at a meaningful but bounded lift.
    max_q = max(quadrant_pressure.values()) if quadrant_pressure else 0.0
    if max_q > 0:
        for q in list(quadrant_pressure):
            quadrant_pressure[q] = min(1.0, quadrant_pressure[q] / max_q)

    for eid, d in demand.items():
        q = edge_quadrant(edges[eid]["name"])
        inc = quadrant_pressure.get(q, 0.0)
        if inc > 0.05:
            d["pressure"] = min(1.0, d["pressure"] + inc * 0.18)
            d["sources"].append("incidents")

    # --- statewide alerts raise a global floor -----------------------------
    alert_boost = 0.0
    for a in alerts_list:
        msg = (a.get("message") or "").upper()
        if any(k in msg for k in ("WINTER", "STORM", "SNOW", "ICE", "WIND", "CLOSURE")):
            alert_boost += 0.12
    alert_boost = min(0.3, alert_boost)

    for d in demand.values():
        d["pressure"] = min(1.0, d["pressure"] + alert_boost)
        if alert_boost > 0 and "alerts_511" not in d["sources"]:
            d["sources"].append("alerts_511")

    for eid in edges:
        demand.setdefault(eid, {"name": edges[eid]["name"], "pressure": 0.0,
                                "delay_min": 0.0, "volume": 0.0, "sources": []})

    stats = {
        "edges_total": len(edges),
        "edges_with_data": sum(1 for d in demand.values() if d["pressure"] > 0),
        "corridors_matched": matched_corridors,
        "corridors_total": len(travel),
        "alert_boost": round(alert_boost, 3),
    }
    return dict(demand), stats


def edge_quadrant(name):
    """Very rough NW/NE/SW/SE bucket from street-name compass tokens."""
    s = (name or "").upper()
    north = any(k in s for k in ("NW", "NE", "NORTH", "16 AVE", "16 AV"))
    west = s.endswith("NW") or " WEST" in s
    if north and west:
        return "NW"
    if north:
        return "NE"
    if west:
        return "SW"
    return "SE"


# --------------------------------------------------------------------------
# Optimization
# --------------------------------------------------------------------------
def score_plan(pressure_mean, pressure_p90, demand_scale, speed_scale,
               signal_green_bonus, n_hot):
    """Analytic cost model. Lower is better.

    A crude but monotone traffic-flow approximation:
      * congestion rises superlinearly with demand/speed ratio
      * more hot edges => more spillback
      * giving green to hot edges relieves the worst of it
    """
    load = pressure_mean * demand_scale / max(speed_scale, 0.2)
    delay_cost = (load ** 2.1) * 100.0
    spillback = (pressure_p90 * demand_scale) ** 1.6 * 40.0 * (1 + n_hot / 40.0)
    relief = signal_green_bonus * min(pressure_p90, 1.0) * 55.0
    throughput_bonus = (speed_scale - 1.0) * 6.0
    return delay_cost + spillback - relief - throughput_bonus


def optimize(demand, edges, close_threshold=0.82):
    """Search control settings, return the best plan.

    Plan fields:
      demand_scale, speed_scale, signal_mode, closed_edges[], close_threshold,
      metrics{}
    """
    pressures = sorted((d["pressure"] for d in demand.values()), reverse=True)
    n = len(pressures) or 1
    p_mean = sum(pressures) / n
    p_p90 = pressures[max(0, int(0.10 * n) - 1)] if n else 0.0
    hot = [eid for eid, d in demand.items() if d["pressure"] >= close_threshold]
    hot.sort(key=lambda e: -demand[e]["pressure"])

    best = None
    # Coarse grid; the cost surface is smooth so this converges fast.
    for demand_scale in (0.5, 0.65, 0.8, 0.9, 1.0, 1.1):
        for speed_scale in (0.7, 0.85, 1.0, 1.15):
            for green_bonus in (0.0, 0.25, 0.5, 0.75, 1.0):
                c = score_plan(p_mean, p_p90, demand_scale, speed_scale,
                               green_bonus, len(hot))
                if best is None or c < best[0]:
                    best = (c, demand_scale, speed_scale, green_bonus)

    cost, ds, ss, gb = best
    # Only close genuinely saturated edges, capped so the sim stays drivable.
    closed = hot[:25]

    plan = {
        "demand_scale": round(ds, 3),
        "speed_scale": round(ss, 3),
        "signal_mode": "normal",
        "signal_green_bonus": round(gb, 3),
        "closed_edges": closed,
        "metrics": {
            "cost": round(cost, 3),
            "pressure_mean": round(p_mean, 4),
            "pressure_p90": round(p_p90, 4),
            "hot_edges": len(hot),
            "edges_considered": n,
        },
    }
    return plan


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
PLAN_FIELDS = ["edge_id", "road_name", "pressure", "delay_min", "volume",
               "sources", "action", "target_speed_kph", "notes"]


def write_control_plan(path, demand, plan):
    """Write the per-edge control plan CSV consumed by the simulator."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    closed = set(plan.get("closed_edges", []))
    bonus = plan.get("signal_green_bonus", 0.0)

    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PLAN_FIELDS)
        w.writeheader()
        ranked = sorted(demand.items(), key=lambda kv: -kv[1]["pressure"])
        for eid, d in ranked:
            p = d["pressure"]
            if eid in closed:
                action, note = "close", "saturated; divert traffic"
            elif p >= 0.6:
                action = "signal_priority"
                note = f"green bonus {bonus:.2f}"
            elif p >= 0.35:
                action = "speed_relief"
                note = "raise speed limit in sim"
            else:
                action, note = "normal", ""
            w.writerow({
                "edge_id": eid,
                "road_name": d["name"],
                "pressure": round(p, 4),
                "delay_min": d["delay_min"],
                "volume": int(d["volume"]) if d["volume"] else "",
                "sources": "|".join(d["sources"]),
                "action": action,
                "target_speed_kph": round(50 * p, 1) if p > 0 else "",
                "notes": note,
            })
    return path


def apply_plan(plan, base="http://localhost:8765"):
    """POST the plan to a running calgary3d server."""
    import urllib.request
    body = json.dumps({
        "demandScale": plan["demand_scale"],
        "speedScale": plan["speed_scale"],
        "signalMode": plan["signal_mode"],
        "closeEdges": plan["closed_edges"],
    }).encode()
    req = urllib.request.Request(f"{base}/control", data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", "replace")


# Module-level for edge_quadrant's alert scan (kept simple + explicit).
alerts_list = []