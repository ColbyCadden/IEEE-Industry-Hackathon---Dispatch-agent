"""Core sanity tests. Run from the repo root: python -m tests.test_core

Plain asserts, no pytest. A test whose engine module is missing or still a stub
prints "SKIPPED: <module>" instead of crashing.
"""
import functools
import sys
import traceback
from pathlib import Path

import pandas as pd

CSV = "data/311_dispatch_sample.csv"
CREWS, JOBS = 8, 5
SICK_CREW = 4


class Skip(Exception):
    pass


@functools.lru_cache(maxsize=None)
def scored():
    from dispatch.data_prep import load_clean_tickets
    from dispatch.scoring import score
    return score(load_clean_tickets())


@functools.lru_cache(maxsize=None)
def plans():
    from dispatch.assign import make_plan
    return (make_plan(scored(), order="priority", crews=CREWS, jobs=JOBS),
            make_plan(scored(), order="fifo", crews=CREWS, jobs=JOBS))


@functools.lru_cache(maxsize=None)
def replanned():
    from dispatch.replan import apply_event
    agent, _ = plans()
    return apply_event(agent, {"event": "crew_out", "crew": SICK_CREW, "capacity": 0.0})


def job_ids(plan):
    return [j["id"] for c in plan["crews"] for j in c["jobs"]]


# --- tests -----------------------------------------------------------------

def test_every_csv_type_has_weight():
    from dispatch.weights import WEIGHTS
    types = set(pd.read_csv(CSV)["service_name"].unique())
    missing = types - set(WEIGHTS)
    assert not missing, f"no weight for: {sorted(missing)}"


def test_plans_at_most_40_jobs():
    for name, plan in zip(("agent", "fifo"), plans()):
        n = len(job_ids(plan))
        assert n <= CREWS * JOBS, f"{name} plan has {n} jobs"


def test_no_ticket_assigned_twice():
    for name, plan in zip(("agent", "fifo"), plans()):
        ids = job_ids(plan)
        dups = sorted({i for i in ids if ids.count(i) > 1})
        assert not dups, f"{name} plan assigns twice: {dups}"


def test_no_crew_over_5_jobs():
    for name, plan in zip(("agent", "fifo"), plans()):
        over = {c["crew"]: len(c["jobs"]) for c in plan["crews"] if len(c["jobs"]) > JOBS}
        assert not over, f"{name} plan crews over {JOBS}: {over}"


def test_fifo_and_agent_same_settings():
    agent, fifo = plans()
    assert [c["crew"] for c in agent["crews"]] == [c["crew"] for c in fifo["crews"]], "different crew lists"
    assert len(agent["crews"]) == CREWS, f"agent has {len(agent['crews'])} crews"
    assert [c.get("zone") for c in agent["crews"]] == [c.get("zone") for c in fifo["crews"]], "different zones"
    assert len(job_ids(agent)) == len(job_ids(fifo)), "plans fill a different number of slots"


def test_crew_out_leaves_crew_empty():
    new_plan, _ = replanned()
    crew = next((c for c in new_plan["crews"] if c["crew"] == SICK_CREW), None)
    assert crew is not None, f"crew {SICK_CREW} missing from new plan"
    assert len(crew["jobs"]) == 0, f"crew {SICK_CREW} still has {len(crew['jobs'])} jobs"


def test_no_safety_dropped_over_lower_p_nonsafety():
    agent, _ = plans()
    new_plan, changes = replanned()
    before = {j["id"]: j for c in agent["crews"] for j in c["jobs"]}
    kept = [j for c in new_plan["crews"] for j in c["jobs"]]
    bad = []
    for jid in changes["dropped"]:
        job = before[jid]
        if not job["safety"]:
            continue
        lower = [k["id"] for k in kept if not k["safety"] and k["P"] < job["P"]]
        if lower:
            bad.append(f"{jid} (P={job['P']}) dropped while kept: {lower}")
    assert not bad, "; ".join(bad)


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]


def _skip_module(exc):
    if isinstance(exc, ImportError):
        return exc.name or str(exc)
    frame = traceback.extract_tb(exc.__traceback__)[-1]
    return Path(frame.filename).stem


def main() -> int:
    failed = 0
    for test in TESTS:
        name = test.__name__[len("test_"):]
        try:
            test()
            print(f"PASS     {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL     {name}: {e}")
        except (ImportError, NotImplementedError) as e:
            print(f"SKIPPED: {_skip_module(e)}  ({name})")
        except Exception as e:  # unexpected crash in an engine module
            failed += 1
            print(f"ERROR    {name}: {type(e).__name__}: {e}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} not failing")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
