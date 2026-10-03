"""
Calls agent - turns a caller's words into a live update.

The browser (Calls panel) captures speech (browser speech recognition) or typed
text and POSTs it to the server, which calls handle_call():
    classify  -> resolved | reopened | escalate | downgrade
    match     -> which 311 request they mean (street/number/category tokens)
    apply     -> append to live_updates.jsonl, then re-run the route planner
The map overlay (pin colours) and the priority list/routes all derive from that
log, so one update flows to both.

ElevenLabs speaks the agent's side of the call (server proxies /tts so the API
key never reaches the browser).

    python -m agents.dispatch.calls "debris at 728 6 street southwest was picked up"
"""
import csv, json, os, subprocess, sys, threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from agents.dispatch import updates as U  # noqa: E402

DATA = os.path.join(HERE, "data")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable
_replan_lock = threading.Lock()


def area_requests():
    p = os.path.join(DATA, "requests.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def effective_status(r, st):
    if r["id"] in st["resolved"]:
        return "Closed (call)"
    if r["status"].lower() == "closed" and r["id"] not in st["resolved"]:
        # a 'reopened' call flips a city-closed request back to open
        for u in U.load():
            if u["request_id"] == r["id"] and u["action"] == "reopened":
                return "Open (reopened by call)"
    return r["status"]


def replan(blocking=False):
    """Re-run the planner in a subprocess so a crash can't take the server down."""
    def run():
        with _replan_lock:
            subprocess.run([PY, "-m", "agents.dispatch.planner"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
    if blocking:
        run()
        return None
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Voice call: the agent drives a short interview, then applies the update.
# Limited ElevenLabs keys cannot create ConvAI agents or transcribe audio, so the
# dialogue logic lives here; the browser supplies speech in, the server speaks the
# agent's lines with ElevenLabs. Swap in a ConvAI agent once the key has
# convai_write + speech_to_text.
# ---------------------------------------------------------------------------
CATEGORIES = ("pothole", "debris", "sign", "signal", "dead_animal",
              "road_maintenance", "markings")
OPEN_Q = {
    "debris": "Is the debris still on the road, or has it been picked up?",
    "pothole": "Has the pothole been filled, or is it still there?",
    "sign": "Has the sign been repaired, or is it still damaged?",
    "signal": "Has the traffic light been repaired, or is it still broken?",
    "dead_animal": "Has the animal been removed, or is it still there?",
    "road_maintenance": "Has that work been finished, or is it still outstanding?",
    "markings": "Have the markings been repainted, or are they still faded?",
}
FOLLOWUPS = [
    ("escalate", "Thanks. How urgent is it? Is anyone in danger, or is it blocking traffic?"),
    ("resolved", "Got it. Anything else about that location I should know before I close it?"),
]


def _open_now():
    st = U.state()
    return [r for r in area_requests() if effective_status(r, st).lower().startswith(("open", "overdue"))]


def next_question(call_id, utterance=None, request_id=None, action=None):
    """Advance the call by one turn. Returns the agent's next question + state.

    Phases: asking_location -> asking_status -> (asking_urgency) -> done -> wrap.
    Caller may skip phase 1 by passing request_id (UI candidate buttons, or a
    dispatch map popup), and phase 2 by passing action.
    """
    sess = SESSIONS.setdefault(call_id, {"phase": "location", "id": None, "action": None})

    def say(state, question, **kw):
        sess["phase"] = state
        r = {"call_id": call_id, "state": state, "question": question}
        r.update(kw)
        return r

    # ---- phase: identify the issue -------------------------------------
    if sess["phase"] == "location":
        if request_id:                       # caller/UI picked it
            sess["id"] = request_id
        elif utterance:
            best, ranked = U.match_request(utterance, _open_now())
            if not best and ranked:
                sess["pending"] = ranked[0][1]["id"]
                return say("confirm", "I found a few that could match. Which one did you mean?",
                           candidates=[{"id": c["id"], "category": c["category"],
                                        "location": c["location"] or c["community"]}
                                       for _, c in ranked], awaiting="candidate")
            if best:
                sess["id"] = best["id"]
        if not sess["id"]:
            return say("location", "Which issue are you calling about? Tell me the street "
                                  "and what the problem is.", awaiting="location",
                       open_count=len(_open_now()))
        req = next((r for r in _open_now() if r["id"] == sess["id"]), {})
        cat = req.get("category", "")
        sess["cat"] = cat
        loc = req.get("location") or req.get("community") or "your location"
        sess["action"] = action or U.classify(utterance or "")
        return say("status", "Thanks, I've got the %s at %s. " % (cat.replace("_", " "), loc)
                   + OPEN_Q.get(cat, "What's the current status of that issue?"),
                   awaiting="status", request={"id": sess["id"], "category": cat, "location": loc})

    # ---- phase: status of that issue ------------------------------------
    if sess["phase"] in ("status", "clarify"):
        a2 = action or U.classify(utterance or "")
        if not a2:
            return say("clarify", "Sorry, I didn't catch that. Was it fixed, still there, "
                                  "or more urgent than before?", awaiting="status")
        sess["action"] = a2
        if a2 == "escalate":
            return say("urgency", FOLLOWUPS[0][1], awaiting="urgency", action=a2)
        res = handle_call(utterance or "", caller=sess.get("caller", "voice"),
                          request_id=sess["id"], action=a2, replan_now=True)
        sess["result"] = res
        return say("done", res.get("reply", ""), result=res, awaiting=None)

    # ---- phase: urgency detail -----------------------------------------
    if sess["phase"] == "urgency":
        note = (utterance or "")[:200]
        res = handle_call(note or "escalated", caller=sess.get("caller", "voice"),
                          request_id=sess["id"], action="escalate", replan_now=True)
        if res.get("applied") and note:
            allu = U.load()
            allu[-1]["note"] = note
            with open(U.LOG, "w", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(x) for x in allu) + "\n")
        sess["result"] = res
        return say("done", res.get("reply", ""), result=res, awaiting=None)

    # ---- finished -------------------------------------------------------
    return say("wrap", "Thanks, that's logged. Anything else I can help with?", awaiting=None)


SESSIONS = {}
CALLS_JSON = os.path.join(DATA, "calls.json")


def handle_call(transcript, caller="caller", request_id=None, action=None, replan_now=True):
    transcript = (transcript or "").strip()
    st = U.state()
    reqs = area_requests()
    act = action or U.classify(transcript)
    out = {"transcript": transcript, "action": act, "request": None, "applied": False,
           "candidates": [], "reply": ""}
    if not act:
        out["reply"] = ("Sorry, I couldn't tell what changed. Was the problem fixed, "
                        "still there, or more urgent than before?")
        return out
    byid = {r["id"]: r for r in reqs}
    if request_id:
        r = byid.get(request_id)
        best, ranked = r, []
    else:
        open_now = [r for r in reqs if effective_status(r, st).lower().startswith(("open", "overdue"))]
        pool = open_now if act in ("resolved", "escalate", "downgrade") else \
            [r for r in reqs if r not in open_now] or reqs
        best, ranked = U.match_request(transcript, pool)
    if not best:
        out["candidates"] = [{"id": c["id"], "category": c["category"], "location": c["location"],
                              "score": s} for s, c in ranked]
        out["reply"] = ("I found %d possible locations. Which one did you mean?" % len(ranked)
                        if ranked else
                        "I couldn't match that to a known request. Please say the street and number, "
                        "and what the problem is.")
        return out
    rec = U.append(best["id"], act, caller=caller, transcript=transcript)
    out.update(request={"id": best["id"], "category": best["category"],
                        "location": best["location"]}, applied=True, record=rec)
    label = best["location"] or best["category"]
    out["reply"] = {
        "resolved": "Thanks. I've marked the %s at %s as cleared and updated the map." % (best["category"].replace("_", " "), label),
        "reopened": "Understood, the %s at %s is still a problem. I've reopened it." % (best["category"].replace("_", " "), label),
        "escalate": "Noted, that sounds urgent. I've raised the priority of the %s at %s and the crews are being rerouted." % (best["category"].replace("_", " "), label),
        "downgrade": "Okay, I've lowered the priority of the %s at %s." % (best["category"].replace("_", " "), label),
    }[act]
    if replan_now:
        replan(blocking=False)
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(handle_call(" ".join(sys.argv[1:]), replan_now=False), indent=1))
