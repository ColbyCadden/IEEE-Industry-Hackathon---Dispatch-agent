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
import csv, os, subprocess, sys, threading

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
