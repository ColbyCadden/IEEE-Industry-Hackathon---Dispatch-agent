"""Live day updates: crew changes and new urgent jobs, applied one after another to the 8 a.m. plan.

The 8 a.m. plan comes from the engine. This module replays a log of updates on top of it, so the
supervisor can report new problems and crew changes all day long and every update builds on the last.

  new_day(plan)           -> day state (a deep copy of the plan plus slots, deferred jobs, moves)
  apply(day, event)       -> changes the day in place and returns an "effect" describing what happened
  replay(plan, log)       -> day with every event in the log applied in order
  bundle(plan, metrics, log) -> {"plan", "changes", "metrics", "slots", "effects"} for the dashboard

Events (all plain dicts):
  {"event": "crew_out", "crew": 4}
  {"event": "crew_partial", "crew": 2, "capacity": 0.5}     # capacity 1.0 = crew is back
  {"event": "new_job", "job": {"label", "severity", "lat", "lon", ...}}

Rules (deterministic, no model involved):
  * A crew works round(5 * capacity) jobs; a crew that is out works 0.
  * A displaced job tries the 3 nearest crews that still have working slots: a free slot, else it
    replaces that crew's lowest-P job if that one is strictly lower (the replaced job is deferred).
  * A new job gets P from its severity (0-3). Emergencies (3) jump the queue: P is above every job
    on the plan, so the nearest crews always take it.
  * When a crew's capacity goes up again, it takes the highest-P deferred jobs within MAX_REFILL_KM.
"""
import copy
import json
import math
import re
import urllib.parse
import urllib.request

from dispatch.assign import JOBS_PER_CREW

NEAREST_CREWS = 3          # a displaced or new job may only go to this many nearest working crews
MAX_REFILL_KM = 12.0       # a returning crew only takes deferred jobs this close to its area
SEVERITY_P = {0: 1.0, 1: 2.0, 2: 3.5}   # priority P for a job reported today (no days waiting yet)
EMERGENCY_FLOOR = 4.5      # severity 3 gets at least this P, and always more than any job on the plan
SEVERITY_LABELS = {0: "low", 1: "routine", 2: "high", 3: "emergency"}
BOUNDS = (50.80, 51.35, -114.45, -113.75)   # lat min, lat max, lon min, lon max: "is this Calgary?"
GEOCODE = True             # set False to skip the online address lookup (community names still work)
GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ieee-yp-hackathon-311-dispatch-demo/1.0 (student project)"

last_geocode = {"note": None}   # why the last address lookup failed, for the UI
_geo_cache: dict[str, dict | None] = {}


# --- engine ------------------------------------------------------------------

def new_day(plan_8am: dict) -> dict:
    plan = copy.deepcopy(plan_8am)
    return {
        "plan": plan,
        "slots": {c["crew"]: JOBS_PER_CREW for c in plan["crews"]},     # jobs each crew can work today
        "origin": {j["id"]: c["crew"] for c in plan["crews"] for j in c["jobs"]},
        "moved": {},        # job id -> {"id", "from", "to"} net of everything so far
        "deferred": [],     # jobs not done today
        "added": [],        # ids of jobs reported after 8 a.m.
        "effects": [],
    }


def _crew(day: dict, crew_id: int) -> dict:
    for c in day["plan"]["crews"]:
        if c["crew"] == crew_id:
            return c
    raise ValueError(f"crew {crew_id!r} is not in the plan")


def _dist(job: dict, centroid: list) -> float:
    return math.hypot(job["lat"] - centroid[0], job["lon"] - centroid[1])


def _km(job: dict, centroid: list) -> float:
    dy = (job["lat"] - centroid[0]) * 111.32
    dx = (job["lon"] - centroid[1]) * 111.32 * math.cos(math.radians(centroid[0]))
    return math.hypot(dx, dy)


def _note_move(day: dict, job: dict, to: int) -> None:
    origin = day["origin"].get(job["id"])
    if origin is None:          # a job reported today has no 8 a.m. crew to move from
        return
    if to == origin:
        day["moved"].pop(job["id"], None)
    else:
        day["moved"][job["id"]] = {"id": job["id"], "from": origin, "to": to}


def _defer(day: dict, job: dict) -> None:
    day["moved"].pop(job["id"], None)
    day["deferred"].append(job)


def _place(day: dict, job: dict, exclude: int | None = None) -> tuple[dict | None, dict | None]:
    """Put job on one of the nearest working crews. Returns (crew, bumped job) or (None, None)."""
    pool = [c for c in day["plan"]["crews"] if c["crew"] != exclude and day["slots"][c["crew"]] > 0]
    for crew in sorted(pool, key=lambda c: _dist(job, c["centroid"]))[:NEAREST_CREWS]:
        if len(crew["jobs"]) < day["slots"][crew["crew"]]:
            crew["jobs"].append(job)
            return crew, None
        # lowest P; on ties, the job added last
        low = min(reversed(range(len(crew["jobs"]))), key=lambda i: crew["jobs"][i]["P"])
        if crew["jobs"][low]["P"] < job["P"]:
            bumped = crew["jobs"][low]
            crew["jobs"][low] = job
            return crew, bumped
    return None, None


def _set_capacity(day: dict, event: dict) -> dict:
    crew_id = event["crew"]
    crew = _crew(day, crew_id)
    kind = event["event"]
    cap = float(event.get("capacity", 0.0))
    if not 0.0 <= cap <= 1.0:
        raise ValueError(f"capacity must be between 0 and 1, got {cap}")
    new = 0 if kind == "crew_out" else (JOBS_PER_CREW if cap >= 1.0 else round(JOBS_PER_CREW * cap))
    old = day["slots"][crew_id]
    day["slots"][crew_id] = new
    effect = {"kind": kind, "crew": crew_id, "zone": crew["zone"], "slots": new, "was": old,
              "moved": [], "dropped": [], "restored": []}

    crew["jobs"].sort(key=lambda j: j["P"], reverse=True)
    displaced = crew["jobs"][new:]
    crew["jobs"] = crew["jobs"][:new]
    for job in sorted(displaced, key=lambda j: j["P"], reverse=True):
        target, bumped = _place(day, job, exclude=crew_id)
        if target is None:
            _defer(day, job)
            effect["dropped"].append(job)
            continue
        _note_move(day, job, target["crew"])
        effect["moved"].append({"job": job, "to": target["crew"]})
        if bumped is not None:
            effect["moved"] = [m for m in effect["moved"] if m["job"]["id"] != bumped["id"]]
            _defer(day, bumped)
            effect["dropped"].append(bumped)

    while len(crew["jobs"]) < new:   # capacity went up: take back the best nearby deferred jobs
        near = [j for j in day["deferred"] if _km(j, crew["centroid"]) <= MAX_REFILL_KM]
        if not near:
            break
        best = max(near, key=lambda j: j["P"])
        day["deferred"].remove(best)
        crew["jobs"].append(best)
        _note_move(day, best, crew_id)
        effect["restored"].append(best)
    return effect


def job_priority(day: dict, severity: int) -> float:
    if severity >= 3:
        top = max((j["P"] for c in day["plan"]["crews"] for j in c["jobs"]), default=0.0)
        return round(max(EMERGENCY_FLOOR, top + 0.25), 2)
    return SEVERITY_P[max(0, severity)]


def _add_job(day: dict, spec: dict) -> dict:
    sev = max(0, min(3, int(spec.get("severity", 2))))
    n = len(day["added"]) + 1
    label = (spec.get("label") or "Urgent job").strip()
    job = {
        "id": f"NEW-{n}",
        "type": label,
        "service_name": f"Reported today: {label}",
        "community": str(spec.get("community") or nearest_community(spec["lat"], spec["lon"]) or "UNKNOWN").upper(),
        "P": job_priority(day, sev),
        "safety": sev >= 2,
        "lat": float(spec["lat"]),
        "lon": float(spec["lon"]),
        "reports": 1,
        "new": True,
        "severity": sev,
        "summary": spec.get("summary") or "",
        "address": spec.get("address") or "",
        "location_source": spec.get("location_source") or "",
    }
    day["added"].append(job["id"])
    target, bumped = _place(day, job)
    effect = {"kind": "new_job", "job": job, "crew": None, "zone": None, "bumped": bumped, "dropped": []}
    if target is None:
        _defer(day, job)
        effect["dropped"].append(job)
        return effect
    effect["crew"], effect["zone"] = target["crew"], target["zone"]
    if bumped is not None:
        _defer(day, bumped)
        effect["dropped"].append(bumped)
    return effect


def apply(day: dict, event: dict) -> dict:
    kind = event.get("event")
    if kind == "new_job":
        effect = _add_job(day, event["job"])
    elif kind in ("crew_out", "crew_partial"):
        effect = _set_capacity(day, event)
    else:
        effect = {"kind": "none"}
    day["effects"].append(effect)
    return effect


def replay(plan_8am: dict, log: list) -> dict:
    day = new_day(plan_8am)
    for event in log:
        apply(day, event)
    return day


def changes_of(day: dict) -> dict:
    """The engine's `changes` shape (moved / dropped / dropped_jobs) plus the ids added today."""
    return {"moved": list(day["moved"].values()),
            "dropped": [j["id"] for j in day["deferred"]],
            "dropped_jobs": list(day["deferred"]),
            "added": list(day["added"])}


def bundle(plan_8am: dict, metrics: dict, log: list) -> dict:
    """Everything the dashboard needs after replaying the log."""
    from dispatch.metrics import compute
    day = replay(plan_8am, log)
    changes = changes_of(day)
    m = compute(day["plan"], changes)
    m["added"] = len(changes["added"])
    out = {k: v for k, v in metrics.items() if k in ("8am", "fifo")}
    out["noon"] = m
    out["changes"] = changes
    return {"plan": day["plan"], "changes": changes, "metrics": out,
            "slots": dict(day["slots"]), "effects": day["effects"]}


def slots_now(plan_8am: dict, log: list) -> dict:
    return replay(plan_8am, log)["slots"]


# --- plain-English descriptions ------------------------------------------------

def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _short(job: dict) -> str:
    return f"{job['type']} in {str(job['community']).title()} (P {job['P']:.2f})"


def effect_text(event: dict, effect: dict) -> str:
    """One deterministic sentence about what an update did to the plan."""
    kind = effect.get("kind")
    if kind == "new_job":
        job = effect["job"]
        if effect["crew"] is None:
            return f"No working crew nearby could take it, so it is deferred. It got P {job['P']:.2f}."
        text = f"Crew {effect['crew']} ({effect['zone']}) takes it at P {job['P']:.2f}."
        if effect["bumped"] is not None:
            b = effect["bumped"]
            text += (f" To make room, {_short(b)} was deferred"
                     + (" (a safety ticket)." if b["safety"] else "."))
        else:
            text += " It fit in an open slot, so nothing was deferred."
        return text
    if kind in ("crew_out", "crew_partial"):
        parts = []
        if effect["slots"] > effect["was"]:
            parts.append(f"Crew {effect['crew']} can work {_plural(effect['slots'], 'job')} again")
            if effect["restored"]:
                parts.append(f"and picked up {_plural(len(effect['restored']), 'deferred job')}")
            return " ".join(parts) + "."
        parts.append(f"{_plural(len(effect['moved']), 'job')} moved to nearby crews")
        parts.append(f"{_plural(len(effect['dropped']), 'job')} deferred")
        safety = sum(bool(j["safety"]) for j in effect["dropped"])
        if effect["dropped"]:
            parts[-1] += f" ({safety} safety)"
        return ", ".join(parts) + "."
    return ""


def describe_event(event: dict) -> str:
    """Short label for the day log."""
    kind, crew = event.get("event"), event.get("crew")
    if kind == "crew_out":
        return f"Crew {crew} out for the day"
    if kind == "crew_partial":
        cap = float(event.get("capacity", 0.5))
        return f"Crew {crew} back at full strength" if cap >= 1.0 else f"Crew {crew} short-handed ({cap:.0%})"
    if kind == "new_job":
        job = event["job"]
        where = job.get("address") or (str(job.get("community") or "").title()) or "pinned location"
        return f"New {SEVERITY_LABELS[job['severity']]} job: {job['label']} at {where}"
    return "Update"


# --- locations -----------------------------------------------------------------

_comm_cache: dict | None = None


def communities() -> dict:
    """UPPERCASE community name -> (lat, lon), the average of that community's tickets."""
    global _comm_cache
    if _comm_cache is None:
        try:
            from dispatch.data_prep import load_clean_tickets
            df = load_clean_tickets()
            g = df.groupby("community")[["lat", "lon"]].mean()
            _comm_cache = {str(k).upper(): (float(r.lat), float(r.lon)) for k, r in g.iterrows()}
        except Exception:
            _comm_cache = {}
    return _comm_cache


def find_community(text: str) -> str | None:
    """A known community named in the text (longest name wins), as an UPPERCASE name."""
    low = (text or "").lower()
    for name in sorted(communities(), key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(name.lower())}(?![a-z])", low):
            return name
    return None


def nearest_community(lat: float, lon: float) -> str | None:
    best, best_d = None, float("inf")
    for name, (la, lo) in communities().items():
        d = math.hypot(lat - la, (lon - lo) * math.cos(math.radians(lat)))
        if d < best_d:
            best, best_d = name, d
    return best


def in_calgary(lat: float, lon: float) -> bool:
    return BOUNDS[0] <= lat <= BOUNDS[1] and BOUNDS[2] <= lon <= BOUNDS[3]


def geocode(address: str, community: str | None = None, timeout: float = 6.0) -> dict | None:
    """Street address or intersection -> {"lat", "lon", "name"} via OpenStreetMap Nominatim, or None.

    Sends only the address text (plus "Calgary, Alberta"). Results are cached per address.
    """
    last_geocode["note"] = None
    if not GEOCODE:
        last_geocode["note"] = "address lookup is switched off"
        return None
    query = ", ".join(x for x in (re.sub(r"\s+and\s+", " & ", address, flags=re.I),
                                  community.title() if community else None, "Calgary", "Alberta") if x)
    if query in _geo_cache:
        hit = _geo_cache[query]
        if hit is None:
            last_geocode["note"] = "no match for that address"
        return hit
    params = urllib.parse.urlencode({
        "q": query, "format": "jsonv2", "limit": 1, "countrycodes": "ca", "bounded": 1,
        "viewbox": f"{BOUNDS[2]},{BOUNDS[1]},{BOUNDS[3]},{BOUNDS[0]}",
    })
    try:
        req = urllib.request.Request(f"{GEOCODE_URL}?{params}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        last_geocode["note"] = f"address lookup unavailable ({type(e).__name__})"
        return None
    hit = None
    if rows:
        lat, lon = float(rows[0]["lat"]), float(rows[0]["lon"])
        if in_calgary(lat, lon):
            hit = {"lat": lat, "lon": lon, "name": rows[0].get("display_name", address)}
    _geo_cache[query] = hit
    if hit is None:
        last_geocode["note"] = "no match for that address"
    return hit


def finish_location(job: dict, lat: float, lon: float, source: str) -> dict:
    job.update(lat=float(lat), lon=float(lon), location_source=source)
    job["community"] = job.get("community") or nearest_community(job["lat"], job["lon"])
    return job


def locate(job: dict) -> dict | None:
    """Fill job["lat"], job["lon"], job["location_source"] if the address or community can be found.

    Returns the job when located, else None (the app then asks for a click on the map).
    """
    if job.get("lat") is not None and job.get("lon") is not None:
        return job
    note = None
    if job.get("address"):
        hit = geocode(job["address"], job.get("community"))
        if hit:
            job["resolved"] = hit["name"]
            return finish_location(job, hit["lat"], hit["lon"], "street address")
        note = last_geocode["note"]
    comm = (job.get("community") or "").upper()
    if comm in communities():
        return finish_location(job, communities()[comm][0], communities()[comm][1],
                               "community centre (approximate)")
    job["locate_note"] = note or ("no address or community found" if not job.get("address") else "")
    return None


# --- rule-based reading of a new-job message (the offline fallback, and a check on Claude) ----------

_HAZARDS = [  # (pattern, label, base severity); the first match with the highest severity names the job
    (r"sink ?holes?", "Sinkhole", 3),
    (r"cave[- ]?ins?|wash ?outs?|road (?:has )?collaps\w*|collapsed (?:road|pavement|culvert)", "Road collapse", 3),
    (r"gas leak|smell of gas|smells? gas", "Gas leak", 3),
    (r"water ?main|burst pipe|flood\w*", "Flooding", 3),
    (r"(?:down|fallen|snapped|broken)\s+(?:power ?lines?|wires?|poles?|trees?)|fallen tree|"
     r"tree (?:down|fell|across)|downed\s+\w+", "Fallen tree or line", 3),
    (r"accident|crash(?:ed)?|collision|(?:oil|fuel|chemical) spill", "Collision or spill", 3),
    (r"(?:traffic )?(?:signals?|lights?)\s+(?:is |are )?(?:out|down|dark|not working|broken|flashing)|"
     r"signal outage|power outage", "Signal outage", 3),
    (r"pot ?holes?", "Pothole", 2),
    (r"(?:stop|yield) sign|(?:missing|damaged|knocked[- ]?down|fallen|bent|downed)\s+(?:\w+\s+)?sign|"
     r"sign\s+(?:is |was |got )?(?:down|missing|knocked|damaged|fallen|gone|bent|hit)", "Damaged sign", 2),
    (r"debris|branch(?:es)?\b|rocks?\b|broken glass|obstruction|blocking (?:the )?(?:lane|road)", "Debris", 1),
    (r"\b(?:urgent|emergency|high[- ]priority)\s+(?:job|ticket|issue|problem|request|report|call)\b|"
     r"\b(?:new|add(?:ing)?)\s+(?:an?\s+)?(?:job|ticket|issue|problem|request)\b", "Urgent issue", 2),
]
_UP = re.compile(r"\b(?:urgent|emergency|asap|immediately|dangerous|hazard\w*|serious|severe|major|huge|massive|"
                 r"deep|blocking|blocked|school|hospital|playground|injur\w*|children|kids|ambulance)\b", re.I)
_DOWN = re.compile(r"\b(?:minor|small|tiny|low[- ]priority|not urgent|no rush|when you can|cosmetic)\b", re.I)
_STREET_WORD = (r"(?:ave|avenue|st|street|rd|road|dr|drive|blvd|boulevard|way|trail|tr|cres|crescent|pl|place|"
                r"ln|lane|hwy|highway|bridge|underpass|overpass|interchange|ramp|nw|ne|sw|se)")
_PREP = re.compile(r"(?:\b(?:at|on|near|by|outside|beside|behind|along|off|corner of|intersection of|in front of)\b|@)\s+(.+)",
                   re.I | re.S)
_CUT = re.compile(r"[.;!?\n]|,\s*(?=(?:and|but|it|its|it's|that|which|please|urgent|blocking|causing|can|could)\b)|"
                  r"\s+(?:blocking|causing|which|that|it'?s|it is|please|asap|urgent)\b|\s+-\s", re.I)


def hazard_in(text: str) -> tuple[str, int] | None:
    best = None
    for pat, label, sev in _HAZARDS:
        if re.search(pat, text or "", re.I) and (best is None or sev > best[1]):
            best = (label, sev)
    return best


def extract_location(text: str) -> tuple[str | None, str | None]:
    """(street address or intersection, community) found in a message; either may be None."""
    comm = find_community(text)
    for m in _PREP.finditer(text or ""):
        tail = _CUT.split(m.group(1))[0].strip(" ,")
        if comm:
            tail = re.sub(rf"\s*,?\s*(?:in|in the|of)?\s*{re.escape(comm.lower())}\s*$", "", tail, flags=re.I).strip(" ,")
            if tail.lower() == comm.lower() or tail.lower() in ("in " + comm.lower(),):
                continue
        tail = re.split(r"\s*,?\s+in\s+(?!front\b)", tail, maxsplit=1, flags=re.I)[0].strip(" ,")  # ", in Brentwood"
        if tail and (re.search(r"\d", tail) or re.search(rf"\b{_STREET_WORD}\b", tail, re.I)):
            return tail, comm
    return None, comm


def clamp_severity(value, default: int = 2) -> int:
    try:
        return max(0, min(3, int(value)))
    except (TypeError, ValueError):
        return default


def make_job_spec(label: str, severity, address: str | None, community: str | None, summary: str = "") -> dict:
    return {"label": (label or "Urgent job").strip()[:40], "severity": clamp_severity(severity),
            "address": (address or None), "community": (community or None), "summary": (summary or "").strip()[:200],
            "lat": None, "lon": None}


def rules_job(text: str) -> dict | None:
    """A job spec if the text reports a new problem, else None. Used when Claude is unavailable."""
    hit = hazard_in(text)
    if hit is None:
        return None
    label, sev = hit
    if _UP.search(text) and sev < 3:
        sev += 1
    if _DOWN.search(text):
        sev -= 1
    address, comm = extract_location(text)
    return make_job_spec(label, sev, address, comm, (text or "").strip())
