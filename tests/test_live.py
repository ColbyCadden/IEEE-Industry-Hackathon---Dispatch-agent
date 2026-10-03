"""Live-update tests: new urgent jobs and stacked crew changes. Run from the repo root: python -m tests.test_live

Plain asserts, no network and no API key (address lookup and Claude are not called).
"""
import json
from pathlib import Path

from dispatch import live, llm
from dispatch.metrics import compute
from dispatch.replan import apply_event

PLAN = json.loads((Path("dispatch/outputs") / "plan_8am.json").read_text())


def ids(plan):
    return [[j["id"] for j in c["jobs"]] for c in plan["crews"]]


def test_matches_replan_for_one_event():
    for ev in ({"event": "crew_out", "crew": 4}, {"event": "crew_partial", "crew": 2, "capacity": 0.5},
               {"event": "crew_partial", "crew": 7, "capacity": 0.75}):
        old_plan, old_changes = apply_event(PLAN, ev)
        day = live.replay(PLAN, [ev])
        new = live.changes_of(day)
        assert ids(old_plan) == ids(day["plan"]), ev
        assert sorted(old_changes["dropped"]) == sorted(new["dropped"]), ev
        assert sorted((m["id"], m["to"]) for m in old_changes["moved"]) == sorted((m["id"], m["to"]) for m in new["moved"])


def test_new_emergency_goes_to_top_and_nobody_is_lost():
    job = {"label": "Sinkhole", "severity": 3, "lat": 51.04, "lon": -114.07, "community": "BELTLINE"}
    day = live.replay(PLAN, [{"event": "new_job", "job": job}])
    placed = [j for c in day["plan"]["crews"] for j in c["jobs"] if j.get("new")]
    assert len(placed) == 1 and placed[0]["P"] > max(j["P"] for c in PLAN["crews"] for j in c["jobs"])
    everyone = [j["id"] for c in day["plan"]["crews"] for j in c["jobs"]] + [j["id"] for j in day["deferred"]]
    assert len(everyone) == len(set(everyone)) == 41          # 40 original jobs + 1 new, none duplicated or lost
    assert all(len(c["jobs"]) <= 5 for c in day["plan"]["crews"])


def test_out_crew_never_receives_new_jobs():
    log = [{"event": "crew_out", "crew": 4},
           {"event": "new_job", "job": {"label": "Pothole", "severity": 2, "lat": 50.89, "lon": -113.94}}]
    day = live.replay(PLAN, log)
    assert day["plan"]["crews"][3]["jobs"] == [] and day["slots"][4] == 0


def test_updates_stack_and_crew_can_return():
    log = [{"event": "crew_out", "crew": 4},
           {"event": "new_job", "job": {"label": "Sinkhole", "severity": 3, "lat": 51.04, "lon": -114.07}},
           {"event": "crew_partial", "crew": 2, "capacity": 0.5},
           {"event": "crew_partial", "crew": 4, "capacity": 1.0}]
    day = live.replay(PLAN, log)
    assert day["slots"] == {1: 5, 2: 2, 3: 5, 4: 5, 5: 5, 6: 5, 7: 5, 8: 5}
    assert len(day["plan"]["crews"][3]["jobs"]) > 0            # crew 4 took deferred work back
    total = sum(len(c["jobs"]) for c in day["plan"]["crews"]) + len(day["deferred"])
    assert total == 41
    m = compute(day["plan"], live.changes_of(day))
    assert m["n"] == sum(len(c["jobs"]) for c in day["plan"]["crews"])


def test_severity_sets_priority():
    day = live.new_day(PLAN)
    assert live.job_priority(day, 0) == 1.0 and live.job_priority(day, 1) == 2.0 and live.job_priority(day, 2) == 3.5
    assert live.job_priority(day, 3) >= 4.5


def test_message_reading_without_claude():
    cases = [("Sinkhole at 8 Ave SW and 4 St SW blocking traffic", "Sinkhole", 3, "8 Ave SW and 4 St SW"),
             ("urgent pothole in Marlborough near a school", "Pothole", 3, None),
             ("stop sign knocked down at 12 Ave and 5 St SE", "Damaged sign", 2, "12 Ave and 5 St SE"),
             ("small pothole at 123 Main St", "Pothole", 1, "123 Main St"),
             ("debris on 16 Ave NW, in Brentwood", "Debris", 1, "16 Ave NW")]
    for text, label, sev, addr in cases:
        spec = live.rules_job(text)
        assert spec and spec["label"] == label and spec["severity"] == sev and spec["address"] == addr, (text, spec)
    assert live.rules_job("Crew 4 called in sick") is None
    assert live.find_community("urgent pothole in Marlborough") == "MARLBOROUGH"


def test_parser_routes_crew_vs_job():
    llm.USE_LLM = False
    assert llm.parse_event("Crew 4 called in sick")["event"] == "crew_out"
    assert llm.parse_event("crew 4 hit a pothole and the truck broke down")["event"] == "crew_out"
    ev = llm.parse_event("gas leak at 5 Ave and 3 St SW")
    assert ev["event"] == "new_job" and ev["job"]["severity"] == 3
    assert llm.parse_event("something happened")["event"] == "unclear"


def test_claude_answer_is_checked():
    text = "Sinkhole on 8 Ave SW in Beltline"
    ok = {"event": "new_job", "crew": None, "capacity": 1, "question": None, "job_label": "Sinkhole",
          "severity": 3, "address": "8 Ave SW", "community": "Beltline", "summary": "sinkhole"}
    ev = llm._valid_event(ok, text)
    assert ev["job"]["address"] == "8 Ave SW" and ev["job"]["community"] == "BELTLINE"
    made_up = llm._valid_event({**ok, "address": "99 Fake Rd NW"}, text)
    assert made_up["job"]["address"] is None                   # a location the message never contains is dropped
    for bad in ({**ok, "severity": None}, {**ok, "job_label": ""}, {**ok, "severity": True}):
        try:
            llm._valid_event(bad, text)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)
