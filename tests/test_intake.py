"""Caller-intake tests. Run from the repo root: python -m tests.test_intake

Rules-only and offline: the map lookup is replaced by a fake, and Claude is switched off.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dispatch import intake, llm, voice, voice_server

FAKE_MAP = {  # caller's location words (lower case) -> what the geocoder would return
    "17 avenue and 4 street sw": {"lat": 51.0378, "lon": -114.0715, "where": "17 Avenue SW & 4 Street SW",
                           "community": "BELTLINE", "precision": "exact"},
    "chinook centre": {"lat": 50.998, "lon": -114.0736, "where": "Chinook Centre, 6455, Macleod Trail SW",
                       "community": "MEADOWLARK PARK", "precision": "exact"},
    "1000 9 avenue sw": {"lat": 51.0454, "lon": -114.0841, "where": "Gibraltar Place, 1000, 9 Avenue SW",
                      "community": "DOWNTOWN WEST END", "precision": "exact"},
}


def fake_geocode(text: str):
    return FAKE_MAP.get(intake.normalize_street(text).lower())  # the real geocoder normalizes too


def call(*caller_lines: str) -> tuple[dict, list[str], bool]:
    """Play a call; return (state, call-taker replies, done after the last line)."""
    state, history, replies, done = intake.new_state(), [], [], False
    for line in caller_lines:
        history.append({"role": "user", "content": line})
        out = intake.intake_turn(history, state)
        history.append({"role": "assistant", "content": out["reply"]})
        replies.append(out["reply"])
        done = out["done"]
    return state, replies, done


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def main() -> int:
    intake.GEOCODER = fake_geocode
    llm.USE_LLM = False
    results = []

    # the case from the brief: "there's a pothole where I am right now" -> agent asks where
    s, r, done = call("Yeah, there's a pothole where I am right now")
    results.append(check("vague location -> asks where", s["service_name"] == "Roads - Pothole Maintenance"
                         and not done and "where exactly" in r[-1].lower(), r[-1]))
    s, r, done = call("Yeah, there's a pothole where I am right now", "17th ave and 4th st SW")
    results.append(check("answer located -> asks hazard", s["where"] == "17 Avenue SW & 4 Street SW"
                         and not done and "hazard" in r[-1].lower(), r[-1]))
    s, r, done = call("Yeah, there's a pothole where I am right now", "17th ave and 4th st SW",
                      "yes it's in the driving lane, cars are swerving")
    results.append(check("hazard answered -> logged", done and "logged" in r[-1].lower(), r[-1]))

    # everything in one sentence: only the hazard question is left
    s, r, done = call("There's a big pothole on 17 Ave and 4 St SW")
    results.append(check("type + location in one line", s["lat"] is not None and "hazard" in r[-1].lower(), r[-1]))

    # unknown location -> says it couldn't find it, asks for a cross street
    s, r, done = call("a stop sign got knocked down", "on Nowhere Lane")
    results.append(check("unfindable location -> asks again", not done and "couldn't find" in r[-1]
                         and s["service_name"] == "Roads - Signs - Missing - Damaged", r[-1]))

    # landmark
    s, r, done = call("there's broken glass all over the road near Chinook Centre")
    results.append(check("debris near a landmark", s["service_name"].startswith("Roads - Debris")
                         and s["where"].startswith("Chinook") and done, f"{s['service_name']} {s['where']} {done}"))

    # non-safety types don't get the hazard question
    s, r, done = call("my garbage wasn't picked up", "1000 9 Ave SW")
    results.append(check("missed pickup -> logged without hazard question", done
                         and s["service_name"] == "WRS - Waste - Residential", r[-1]))

    # no type yet -> asks what the problem is
    s, r, done = call("hi I want to report something")
    results.append(check("no problem stated -> asks what", s["service_name"] is None
                         and r[-1].startswith("Sure.") and "what is the problem" in r[-1].lower(), r[-1]))

    # talking about the call itself is never looked up on the map
    pothole = "hi there's a pothole where I am right now"
    s, r, done = call(pothole, "sorry could u repeat again")
    results.append(check("'repeat' after 'where?' -> repeats the question", r[-1].startswith("Sure.")
                         and "where exactly" in r[-1].lower() and s["failed_location"] is None
                         and "repeat" not in s["details"][-1], r[-1]))
    s, r, done = call(pothole, "sorry could u repeat again", "what?", "17th ave and 4th st SW")
    results.append(check("repeat twice, then answer -> located", r[1] == r[2] and s["lat"] is not None
                         and "hazard" in r[-1].lower(), " | ".join(r)))
    s, r, done = call(pothole, "17th ave and 4th st SW", "sorry what was the question")
    results.append(check("'what was the question' at hazard -> repeats it, not counted as the answer",
                         "hazard" in r[-1].lower() and not s["hazard_answered"] and not done, r[-1]))
    s, r, done = call(pothole, "can you hear me")
    results.append(check("'can you hear me' -> yes + question", r[-1].startswith("Yes, I can hear you"), r[-1]))
    s, r, done = call("hello")
    results.append(check("greeting only -> greets back + asks what", r[-1].startswith("Hi there")
                         and "what problem" in r[-1].lower(), r[-1]))
    s, r, done = call(pothole, "I don't know the address")
    results.append(check("doesn't know the address -> helps, no failed lookup",
                         "closest street" in r[-1] and s["failed_location"] is None, r[-1]))
    s, r, done = call(pothole, "how long will it take to fix")
    results.append(check("caller question -> answered + asks again", r[-1].startswith("Crews are scheduled")
                         and "where exactly" in r[-1].lower(), r[-1]))
    s, r, done = call(pothole, "um yeah okay so")
    results.append(check("filler -> asks where again, no lookup", s["failed_location"] is None
                         and "didn't catch a location" in r[-1], r[-1]))
    s, r, done = call(pothole, "never mind, forget it")
    results.append(check("caller hangs up -> done, nothing logged", done and not intake.ready(s), r[-1]))
    s, r, done = call(pothole, "17th ave and 4th st SW", "no that's wrong", "1000 9 Ave SW")
    results.append(check("'that's wrong' -> asks again, new place used", s["where"].startswith("Gibraltar")
                         and "hazard" in r[-1].lower(), " | ".join(r)))
    s, r, done = call(pothole, "1000 9 Ave SW", "no actually it's at 17th ave and 4th st SW")
    results.append(check("correction with a new place -> re-located", s["where"] == "17 Avenue SW & 4 Street SW",
                         s["where"]))
    s, r, done = call("my streetlight is out")
    results.append(check("other City team -> says so", "different City team" in r[-1], r[-1]))

    # emergency wording -> 911 advice first
    s, r, done = call("there was a crash and someone is hurt, a sign is down")
    results.append(check("emergency -> 911 first", r[-1].startswith("If anyone is hurt"), r[-1]))

    # street normalization for the geocoder
    results.append(check("normalize '17th ave sw'", intake.normalize_street("17th ave sw") == "17 Avenue SW",
                         intake.normalize_street("17th ave sw")))

    # ticket: scored by code, nearest crew from the real 8 a.m. plan
    plan = json.loads((Path("dispatch") / "outputs" / "plan_8am.json").read_text(encoding="utf-8"))
    s, r, done = call("There's a big pothole on 17 Ave and 4 St SW", "yes, right in the lane")
    t = intake.make_ticket(s, plan)
    results.append(check("ticket scored and routed", t["P"] == 3.0 and t["safety"] and t["crew"] in range(1, 9)
                         and "P" in t["fit"], json.dumps(t)[:200]))

    # the voice call's local API: one call driven over HTTP, and /tts says 503 without an ElevenLabs key
    port = voice_server.start(8602)
    call_id = voice_server.new_call(plan, 7)

    def post(path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, {}

    for line in ["there's a pothole where I am", "17 ave and 4 st sw", "yes it's in the lane"]:
        status, out = post("/turn", {"call_id": call_id, "text": line})
    ticket = (voice_server.get(call_id) or {}).get("ticket") or {}
    results.append(check("voice API: full call over HTTP", status == 200 and out.get("done")
                         and ticket.get("id") == "CALL-0007", json.dumps(out)))
    saved = os.environ.pop("ELEVENLABS_API_KEY", None)
    voice.load_env = lambda *a, **k: None  # ignore any local .env for this check
    tts_status, _ = post("/tts", {"text": "hello"})
    if saved:
        os.environ["ELEVENLABS_API_KEY"] = saved
    results.append(check("voice API: /tts is 503 without a key (browser voice takes over)", tts_status == 503,
                         str(tts_status)))
    results.append(check("voice API: unknown call is 404", post("/turn", {"call_id": "nope", "text": "hi"})[0] == 404))

    print(f"\n{sum(results)}/{len(results)} intake checks pass")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
