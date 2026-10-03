"""
Live-update store shared by the planner, the server and the calls UI.

Updates are append-only JSON lines in agents/data/live_updates.jsonl:
  {"ts", "request_id", "action", "caller", "transcript", "note"}
actions: resolved | reopened | escalate | downgrade

State derived from the log (replayed in order, so the latest word wins):
  resolved  - set of request ids reported fixed/cleared
  delta     - per-request priority adjustment (escalate +3, downgrade -3)
"""
import json, os, re, time

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOG = os.path.join(DATA, "live_updates.jsonl")
ACTIONS = ("resolved", "reopened", "escalate", "downgrade")
STEP = 3

_ABBR = {"AVENUE": "AV", "AVE": "AV", "STREET": "ST", "ROAD": "RD", "TRAIL": "TR",
         "DRIVE": "DR", "SOUTHWEST": "SW", "SOUTHEAST": "SE", "NORTHWEST": "NW",
         "NORTHEAST": "NE", "BOULEVARD": "BV", "BLVD": "BV", "FIRST": "1", "SECOND": "2",
         "THIRD": "3", "FOURTH": "4", "FIFTH": "5", "SIXTH": "6", "SEVENTH": "7",
         "EIGHTH": "8", "NINTH": "9", "TENTH": "10"}
_STOP = {"THE", "AT", "ON", "OF", "A", "AN", "IS", "WAS", "HAS", "BEEN", "NEAR", "THERE",
         "IT", "AND", "IN", "TO", "HAVE", "THAT", "THIS", "JUST", "NOW", "CALGARY", "AB"}
_CAT_WORDS = {"debris": ["DEBRIS", "GARBAGE", "TRASH", "JUNK"],
              "pothole": ["POTHOLE", "HOLE"], "sign": ["SIGN"],
              "signal": ["SIGNAL", "LIGHT", "LIGHTS"], "dead_animal": ["ANIMAL", "DEER", "RACCOON"],
              "markings": ["MARKING", "MARKINGS", "PAINT", "LINES"],
              "road_maintenance": ["MAINTENANCE", "REPAIR"]}

_WORDS = {
    "resolved": r"picked up|pick(ed)? it up|cleared?|cleaned|removed|taken away|fixed|repaired|"
                r"filled|replaced|gone|done|finished|resolved|all good|no longer",
    "reopened": r"still (there|here|broken|not)|not (fixed|cleared|picked|gone|done)|came back|"
                r"back again|again|reopen|wasn.?t",
    "escalate": r"urgent|dangerous|danger|blocking|blocked|accident|injur|worse|emergency|"
                r"serious|major|hazard|critical",
    "downgrade": r"minor|not urgent|small|low priority|can wait|not a big",
}


def _norm(s):
    out = []
    for t in re.findall(r"[A-Za-z0-9]+", (s or "").upper()):
        t = _ABBR.get(t, t)
        t = re.sub(r"^(\d+)(ST|ND|RD|TH)$", r"\1", t)
        if t not in _STOP:
            out.append(t)
    return out


def classify(text):
    """Transcript -> action. Order matters: 'not fixed' must beat 'fixed'."""
    t = (text or "").lower()
    for a in ("reopened", "escalate", "downgrade", "resolved"):
        if re.search(_WORDS[a], t):
            return a
    return None


def match_request(text, candidates):
    """candidates: dicts with id, location, category. Returns (best, ranked) where
    best is None when nothing scores >= 2 or the top score is tied (ambiguous);
    ranked is the top few (score, candidate) for the UI to offer."""
    toks = set(_norm(text))
    scored = []
    for c in candidates:
        score = len(toks & set(_norm(c.get("location", ""))))
        for w in _CAT_WORDS.get(c.get("category", ""), []):
            if w in toks:
                score += 1
        # A spoken house number that differs from the candidate's is evidence AGAINST it.
        said = {t for t in toks if t.isdigit() and len(t) >= 3}
        has = {t for t in _norm(c.get("location", "")) if t.isdigit() and len(t) >= 3}
        if said and has and not (said & has):
            score -= 2
        if c["id"].lower() in (text or "").lower():
            score += 10
        if score >= 2:
            scored.append((score, c))
    scored.sort(key=lambda t: -t[0])
    if not scored:
        return None, []
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, scored[:4]
    return scored[0][1], scored[:4]


def load():
    out = []
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    return out


def append(request_id, action, caller="", transcript="", note=""):
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    os.makedirs(DATA, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "request_id": request_id,
           "action": action, "caller": caller, "transcript": transcript, "note": note}
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def state(updates=None):
    resolved, delta = set(), {}
    for u in (load() if updates is None else updates):
        rid, a = u.get("request_id"), u.get("action")
        if a == "resolved":
            resolved.add(rid)
        elif a == "reopened":
            resolved.discard(rid)
        elif a == "escalate":
            delta[rid] = delta.get(rid, 0) + STEP
        elif a == "downgrade":
            delta[rid] = delta.get(rid, 0) - STEP
    return {"resolved": resolved, "delta": delta}
