"""Caller intake: a 311 call-taker chat that turns a caller's report into a located, scored ticket.

The agent needs three things before it logs a ticket: what the problem is (one of the dispatchable
service types in weights.py), where it is (geocoded to a point in Calgary), and a short description.
It asks one follow-up question at a time for whatever is missing ("Where exactly is that pothole?").

Claude runs the conversation when a key is set; a rule-based call-taker runs it otherwise. Either
way the location is geocoded and the ticket is scored by code, never by the model.
Geocoding uses OpenStreetMap: Nominatim for addresses and landmarks, Overpass for cross streets.
"""
import json
import logging
import math
import re
import urllib.parse
import urllib.request

from dispatch import llm
from dispatch.weights import SAFETY_TYPES, SHORT_NAMES, WEIGHTS

log = logging.getLogger(__name__)

# Dispatchable types (weight > 0) and the words callers use for them; first match wins, so the
# specific sign types come before the generic "sign".
CATEGORIES = [
    ("Roads - Pothole Maintenance", r"pot ?holes?|hole in the (?:road|street|pavement)|crater|sinkhole"),
    ("Roads - Signs - Parking", r"parking sign|no parking sign|parking signs?"),
    ("Roads - Signs - Missing - Damaged",
     r"(?:stop|yield|street|speed|traffic)? ?signs?\b.*\b(?:missing|down|knocked|bent|broken|damaged|fallen|stolen|gone|"
     r"vandali[sz]ed|snapped|leaning)|(?:missing|knocked[- ]down|broken|damaged|fallen|stolen|bent) (?:stop |yield |street |speed )?signs?"),
    ("Roads - Signs - Traffic and Roadmarking",
     r"faded|road ?markings?|lane (?:lines?|markings?)|crosswalk (?:lines?|paint)|paint(?:ed)? lines?|lines are|traffic signs?"),
    ("Roads - Debris on Street/Sidewalk/Boulevard",
     r"debris|branch(?:es)?|tree (?:fell|down|limb)|fallen tree|rocks?|gravel|glass|mattress|couch|furniture|"
     r"something (?:on|in) the road|blocking the (?:road|lane|sidewalk)|junk on the|dumped"),
    ("WRS - New Service - Carts", r"new (?:cart|bin)|need (?:a|another) (?:cart|bin)|replacement (?:cart|bin)|"
                                  r"(?:cart|bin) (?:is )?(?:broken|cracked|stolen|missing)"),
    ("WRS - Commercial Collection Services", r"(?:business|commercial|store|restaurant|shop)\b.*\b(?:bin|garbage|dumpster|pickup|collection)|dumpster"),
    ("WRS - Waste - Residential",
     r"(?:garbage|recycling|compost|black (?:cart|bin)|blue (?:cart|bin)|green (?:cart|bin)|trash)\b.*\b(?:missed|not (?:been )?picked|"
     r"wasn'?t picked|didn'?t (?:get )?pick|skipped)|missed (?:garbage|recycling|compost|pickup|collection)"),
]
CATEGORIES = [(name, re.compile(rx, re.I)) for name, rx in CATEGORIES if WEIGHTS.get(name, 0) > 0]
_EMERGENCY = re.compile(r"\b(?:injur\w*|hurt|bleeding|unconscious|crash(?:ed)?|collision|accident|fire|smoke|gas leak|"
                        r"smell gas|live wire|downed (?:power )?lines?|power lines? down|flood(?:ing)?|someone (?:fell|hit))\b", re.I)

# Calgary bounding box (west, south, east, north), the same area the 311 sample covers
BBOX = (-114.32, 50.84, -113.86, 51.21)
USER_AGENT = "calgary-311-dispatch-demo/1.0 (IEEE YP hackathon)"
_ABBREV = [(r"\b(\d+)(?:st|nd|rd|th)\b", r"\1"), (r"\bave?\.?(?=\s|$)", "Avenue"), (r"\bst\.?(?=\s|$)", "Street"),
           (r"\brd\.?(?=\s|$)", "Road"), (r"\bdr\.?(?=\s|$)", "Drive"), (r"\btr(?:l)?\.?(?=\s|$)", "Trail"),
           (r"\bblvd\.?(?=\s|$)", "Boulevard"), (r"\bcres\.?(?=\s|$)", "Crescent"), (r"\bhwy\.?(?=\s|$)", "Highway"),
           (r"\b(nw|ne|sw|se)\b", lambda m: m.group(1).upper())]
_STREETY = re.compile(r"\b(?:\d+\s*(?:st|nd|rd|th)?\s+(?:av|ave|avenue|st|street)|avenue|street|road|drive|trail|boulevard|"
                      r"crescent|way|lane|gate|close|court|place|highway|blvd|ave|st|rd|dr|tr|trl|nw|ne|sw|se)\b", re.I)
_LOCATION_CUE = re.compile(r"\b(?:at|on|near|by|outside|in front of|beside|across from|corner of|close to|next to|behind)\s+"
                           r"(?:the\s+)?(.+?)(?=[.!?]|,\s*(?:and|it|there|which)\b|$)", re.I)
_VAGUE = re.compile(r"^(?:here|there|where i am|where i'?m (?:at|standing)|right here|my (?:house|place|street)|"
                    r"the road|the street|outside|nearby|around here)\b", re.I)
_cache: dict[str, dict | None] = {}


# --- geocoding -----------------------------------------------------------------

def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Ground distance in km (equirectangular; fine at city scale)."""
    return math.hypot((lat1 - lat2) * 111.32, (lon1 - lon2) * 111.32 * math.cos(math.radians(lat1)))


def normalize_street(text: str) -> str:
    """'17th ave sw' -> '17 Avenue SW' (the way OpenStreetMap names Calgary streets)."""
    out = " ".join(text.strip().split())
    for pat, rep in _ABBREV:
        out = re.sub(pat, rep, out, flags=re.I)
    return out


def _get_json(url: str, data: bytes | None = None, timeout: float = 12.0):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _nominatim(query: str) -> dict | None:
    w, s, e, n = BBOX
    params = {"q": f"{query}, Calgary, Alberta", "format": "jsonv2", "limit": 1, "addressdetails": 1,
              "viewbox": f"{w},{n},{e},{s}", "bounded": 1, "countrycodes": "ca"}
    hits = _get_json("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params))
    if not hits or hits[0].get("addresstype") in ("city", "county", "state", "country"):
        return None  # "Calgary" itself is not a location for a crew
    h, addr = hits[0], hits[0].get("address", {})
    return {"lat": float(h["lat"]), "lon": float(h["lon"]), "where": ", ".join(h["display_name"].split(", ")[:3]),
            "community": (addr.get("suburb") or addr.get("neighbourhood") or addr.get("quarter") or "").upper()}


def _street_pattern(name: str) -> str:
    """Overpass regex for one street; a missing quadrant matches any (Crowchild Trail -> Crowchild Trail NW)."""
    base = re.sub(r"\s+(NW|NE|SW|SE)$", "", name)
    quad = name[len(base):].strip()
    return f"^{re.escape(base)}{' ' + quad if quad else '( (NW|NE|SW|SE|N|S|E|W))?'}$"


def _intersection(a: str, b: str) -> dict | None:
    """The node where two named streets meet (Overpass). None if they don't share one."""
    w, s, e, n = BBOX
    box = f"{s},{w},{n},{e}"
    q = (f'[out:json][timeout:15];way({box})["highway"]["name"~"{_street_pattern(a)}",i]->.a;'
         f'way({box})["highway"]["name"~"{_street_pattern(b)}",i]->.b;node(w.a)(w.b);out 1;')
    hits = _get_json("https://overpass-api.de/api/interpreter", urllib.parse.urlencode({"data": q}).encode(), 20)
    if not hits.get("elements"):
        return None
    el = hits["elements"][0]
    return {"lat": el["lat"], "lon": el["lon"], "where": f"{a} & {b}", "community": _community(el["lat"], el["lon"])}


def _community(lat: float, lon: float) -> str:
    """Community name at a point (Nominatim reverse); '' if the lookup fails."""
    try:
        params = {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 16, "addressdetails": 1}
        addr = _get_json("https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(params)).get("address", {})
        return (addr.get("suburb") or addr.get("neighbourhood") or addr.get("quarter") or "").upper()
    except Exception:
        return ""


def geocode(text: str) -> dict | None:
    """Caller's words -> {lat, lon, where, community, precision} inside Calgary, or None.

    precision: "exact" (address, landmark or intersection) or "approximate" (one street of a pair).
    Network failures count as "not found", so the call-taker just asks for a better location.
    """
    key = text.strip().lower()
    if key in _cache:
        return _cache[key]
    clean = normalize_street(re.sub(r"^(?:the\s+)?(?:corner of|intersection of)\s+", "", text.strip(), flags=re.I))
    result = None
    try:
        parts = re.split(r"\s+(?:and|&|at|/)\s+", clean, maxsplit=1, flags=re.I)
        if len(parts) == 2 and all(_STREETY.search(p) for p in parts):
            a, b = parts
            qa, qb = re.search(r"(NW|NE|SW|SE)$", a), re.search(r"(NW|NE|SW|SE)$", b)
            if qb and not qa:  # "17 Ave and 4 St SW": both streets are SW
                a = f"{a} {qb.group(1)}"
            try:
                result = _intersection(a, b)
            except Exception as e:  # Overpass busy: fall through to the first street
                log.info("intersection lookup failed: %s", e)
            if result:
                result["precision"] = "exact"
            else:
                result = _nominatim(a)
                if result:
                    result.update(precision="approximate", where=f"{a} (near {b})")
        else:
            result = _nominatim(clean)
            if result:
                result["precision"] = "exact"
    except Exception as e:
        log.info("geocode failed for %r: %s", text, e)
        return None  # not cached: a network blip shouldn't stick
    if result and not (BBOX[1] <= result["lat"] <= BBOX[3] and BBOX[0] <= result["lon"] <= BBOX[2]):
        result = None
    _cache[key] = result
    return result


GEOCODER = geocode  # tests swap this for an offline fake


# --- the conversation ----------------------------------------------------------------

def new_state() -> dict:
    return {"service_name": None, "location_text": None, "lat": None, "lon": None, "where": None,
            "community": None, "precision": None, "details": [], "hazard_asked": False, "hazard_answered": False,
            "emergency": False,
            "failed_location": None}


OPENING = "Calgary 311, what problem would you like to report?"


def classify(text: str) -> str | None:
    for name, rx in CATEGORIES:
        if rx.search(text):
            return name
    if re.search(r"\bsigns?\b", text, re.I):  # a sign with no detail: treat as damaged (the safety case)
        return "Roads - Signs - Missing - Damaged"
    return None


def find_location(text: str, expecting: bool) -> str | None:
    """A location phrase in the caller's words, or None. expecting: we just asked 'where?'."""
    for m in _LOCATION_CUE.finditer(text):
        phrase = m.group(1).strip(" ,")
        if phrase and not _VAGUE.match(phrase) and (_STREETY.search(phrase) or re.search(r"[A-Z]", phrase)):
            return phrase
    stripped = text.strip(" .!?")
    if (expecting and stripped and not _VAGUE.match(stripped) and len(stripped.split()) <= 10
            and not re.match(r"(?:yes|yeah|yep|no|nope|nah|i don'?t know|not sure)", stripped, re.I)):
        return re.sub(r"^(?:it'?s|its|it is|i'?m|im|i am|uh|um|yeah|ok(?:ay)?|so)[,\s]+", "", stripped, flags=re.I)
    if _STREETY.search(text) and re.search(r"\d", text):
        return stripped
    return None


def _short(service_name: str) -> str:
    return SHORT_NAMES.get(service_name, service_name).lower()


def _located(state: dict) -> bool:
    return state["lat"] is not None


def _apply_location(state: dict, phrase: str) -> None:
    hit = GEOCODER(phrase)
    if hit:
        state.update(location_text=phrase, failed_location=None, **hit)
        if hit.get("community"):
            state["community"] = hit["community"]
    else:
        state["failed_location"] = phrase


def _ready(state: dict) -> bool:
    safety = state["service_name"] in SAFETY_TYPES
    return bool(state["service_name"] and _located(state) and (state["hazard_answered"] or not safety))


def _next_question(state: dict) -> str:
    """The one thing to ask next (shared by both paths so the order is always the same)."""
    if not state["service_name"]:
        return ("What is the problem, exactly? For example a pothole, a missing or damaged sign, debris on the "
                "road, or a missed garbage pickup.")
    what = _short(state["service_name"])
    if not _located(state):
        if state["failed_location"]:
            return (f"I couldn't find “{state['failed_location']}” on the map. What's the nearest street "
                    f"address, cross street (like 17 Ave and 4 St SW), or landmark?")
        return (f"Where exactly is the {what}? A street address, the nearest cross street, or a landmark all work.")
    state["hazard_asked"] = True
    return (f"Got it, {state['where']}. Is the {what} causing a hazard right now, for example in a driving lane "
            f"or a crosswalk?")


def _final_reply(state: dict) -> str:
    approx = " (approximate)" if state["precision"] == "approximate" else ""
    return (f"Thank you. I've logged a {_short(state['service_name'])} report at {state['where']}{approx}. "
            f"A Roads supervisor will see it on today's board. Thanks for calling.")


def rules_turn(history: list[dict], state: dict) -> str:
    """Rule-based call-taker: classify, locate, then ask for the next missing piece."""
    text = history[-1]["content"]
    asked_where = state["service_name"] and not _located(state)
    if state["hazard_asked"]:
        state["hazard_answered"] = True  # whatever they said answers "is it a hazard right now?"
    state["details"].append(text)
    if _EMERGENCY.search(text):
        state["emergency"] = True
    if not state["service_name"]:
        state["service_name"] = classify(text)
    if not _located(state):
        phrase = find_location(text, expecting=bool(asked_where))
        if phrase:
            _apply_location(state, phrase)
    if _ready(state):
        return _final_reply(state)
    return _next_question(state)


_INTAKE_SYSTEM = (
    "You are a calm, friendly City of Calgary 311 call-taker. A caller is reporting a road or waste problem. "
    "Your job is to collect three things, asking ONE short question at a time (under 25 words): what the problem "
    "is, exactly where it is (a street address, cross street, or landmark that can be found on a map; 'here' or "
    "'where I am' is not enough, so ask for the nearest cross street), and for potholes and missing or damaged "
    "signs whether it is a hazard right now. Do not ask for anything else (no names, no phone numbers). If "
    "anyone is hurt or in danger, tell them to hang up and call 911 first.\n"
    "Return JSON: reply (what you say next), service_name (one of the allowed types, or null if not yet "
    "clear), location (the caller's location words, in full, or null), hazard_answered (true once the caller "
    "has said whether it is a hazard now), emergency (true if someone is hurt or in danger).\n"
    "Allowed service_name values: " + "; ".join(name for name, _ in CATEGORIES) + "."
)
_INTAKE_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "reply": {"type": "string"},
            "service_name": {"anyOf": [{"type": "string", "enum": [name for name, _ in CATEGORIES]}, {"type": "null"}]},
            "location": {"type": ["string", "null"]},
            "hazard_answered": {"type": "boolean"},
            "emergency": {"type": "boolean"},
        },
        "required": ["reply", "service_name", "location", "hazard_answered", "emergency"],
        "additionalProperties": False,
    },
}


def claude_turn(history: list[dict], state: dict) -> str:
    """Claude reads the whole call and says the next line; code still geocodes and decides when it's done."""
    transcript = "\n".join(f"{'Caller' if m['role'] == 'user' else 'Call-taker'}: {m['content']}" for m in history)
    known = {"service_name": state["service_name"], "location_found": state["where"],
             "location_not_found": state["failed_location"]}
    user = f"Call so far:\n{transcript}\n\nAlready captured (from our map lookup):\n{json.dumps(known)}"
    out = json.loads(llm._ask_claude(_INTAKE_SYSTEM, user, max_tokens=600, output_format=_INTAKE_FORMAT))
    state["details"].append(history[-1]["content"])
    if state["hazard_asked"] or out.get("hazard_answered"):
        state["hazard_answered"] = True
    state["emergency"] = state["emergency"] or bool(out.get("emergency"))
    if out.get("service_name") in WEIGHTS and WEIGHTS[out["service_name"]] > 0:
        state["service_name"] = out["service_name"]
    loc = (out.get("location") or "").strip()
    if loc and loc != state["location_text"] and not _VAGUE.match(loc):
        _apply_location(state, loc)
    if _ready(state):
        return _final_reply(state)
    if state["failed_location"] or (state["service_name"] and _located(state) and not state["hazard_answered"]):
        return _next_question(state)  # code knows the map lookup result; say it the same way every time
    return out["reply"].strip() or _next_question(state)


def intake_turn(history: list[dict], state: dict) -> dict:
    """One caller message in, the call-taker's reply out. history ends with the caller's message.

    Returns {"reply", "done", "source"}; state is updated in place.
    """
    source = "rules"
    if llm.USE_LLM and llm.has_api_key():
        try:
            reply = claude_turn(history, state)
            source = "claude"
        except Exception as e:
            log.info("intake fell back to rules: %s: %s", type(e).__name__, e)
            reply = rules_turn(history, state)
    else:
        reply = rules_turn(history, state)
    if state["emergency"] and not reply.lower().startswith("if anyone"):
        reply = "If anyone is hurt or in danger, please hang up and call 911. " + reply
        state["emergency"] = False  # say it once
    return {"reply": reply, "done": _ready(state), "source": source}


# --- the ticket -------------------------------------------------------------------------------

def make_ticket(state: dict, plan: dict, number: int = 1) -> dict:
    """Score the new report like any other ticket and say where it fits in today's plan.

    A brand-new single report has P = its type weight (0 days open, no duplicates).
    """
    name = state["service_name"]
    p = float(WEIGHTS[name])
    lat, lon = state["lat"], state["lon"]
    crews = [c for c in plan["crews"] if c.get("jobs")]
    nearest = min(crews or plan["crews"], key=lambda c: _km(lat, lon, *c["centroid"]))
    km = _km(lat, lon, *nearest["centroid"])
    lowest = min((j["P"] for j in nearest["jobs"]), default=None)
    if lowest is None:
        fit = f"Crew {nearest['crew']} has no jobs today, so this can go straight onto its list."
    elif p > lowest:
        fit = (f"It outranks crew {nearest['crew']}'s lowest job today (P {lowest:.2f}), so it can replace that "
               f"job on today's plan.")
    else:
        fit = (f"Crew {nearest['crew']}'s lowest job today is P {lowest:.2f}, so this joins the top of tomorrow's "
               f"queue; it gains 0.25 P for each day it waits.")
    return {"id": f"CALL-{number:04d}", "service_name": name, "type": SHORT_NAMES.get(name, name),
            "safety": name in SAFETY_TYPES, "P": p, "lat": lat, "lon": lon, "where": state["where"],
            "community": state["community"] or "", "precision": state["precision"],
            "details": " / ".join(state["details"]), "crew": nearest["crew"], "zone": nearest["zone"],
            "crew_km": round(km, 1), "fit": fit}
