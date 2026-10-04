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


def test_plans_within_capacity():
    """Oldest-first fills 8 x 5; the agent's flexible crews stay within their limits (at most 7 each)."""
    from dispatch.assign import MAX_JOBS
    agent, fifo = plans()
    assert len(job_ids(fifo)) <= CREWS * JOBS, f"fifo plan has {len(job_ids(fifo))} jobs"
    n = len(job_ids(agent))
    assert n <= sum(c["limit"] for c in agent["crews"]) <= CREWS * MAX_JOBS, f"agent plan has {n} jobs"


def test_no_ticket_assigned_twice():
    for name, plan in zip(("agent", "fifo"), plans()):
        ids = job_ids(plan)
        dups = sorted({i for i in ids if ids.count(i) > 1})
        assert not dups, f"{name} plan assigns twice: {dups}"


def test_no_crew_over_its_limit():
    """Every crew stays within its own job limit; oldest-first crews are standard (4 people, 5 jobs)."""
    from dispatch.assign import MAX_JOBS
    agent, fifo = plans()
    for name, plan in (("agent", agent), ("fifo", fifo)):
        over = {c["crew"]: (len(c["jobs"]), c["limit"]) for c in plan["crews"] if len(c["jobs"]) > c["limit"]}
        assert not over, f"{name} plan crews over their limit: {over}"
        assert all(c["limit"] <= MAX_JOBS for c in plan["crews"]), f"{name} plan has a limit over {MAX_JOBS}"
    assert all((c["workers"], c["limit"]) == (4, JOBS) for c in fifo["crews"]), "fifo crews are not standard"


def test_fifo_and_agent_same_settings():
    """Same crews, same 32 workers, same grouping code. Zones follow each plan's own jobs (compact crews)."""
    agent, fifo = plans()
    assert [c["crew"] for c in agent["crews"]] == [c["crew"] for c in fifo["crews"]], "different crew lists"
    assert len(agent["crews"]) == CREWS, f"agent has {len(agent['crews'])} crews"
    assert sum(c["workers"] for c in agent["crews"]) == sum(c["workers"] for c in fifo["crews"]) == CREWS * 4,         "plans use a different workforce"
    from dispatch.assign import make_plan
    assert make_plan(scored(), order="fifo", crews=CREWS, jobs=JOBS) == fifo, "fifo plan not built by make_plan"


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


def test_verified_numbers():
    from dispatch.metrics import compute
    agent, fifo = plans()
    new_plan, changes = replanned()
    assert len(scored()) == 99, f"{len(scored())} field-crew tickets, expected 99"
    assert compute(agent) == {"P": 140.75, "safety": 30, "n": 48}, compute(agent)
    assert compute(fifo) == {"P": 99.0, "safety": 16, "n": 40}, compute(fifo)
    noon = compute(new_plan, changes)
    assert (noon["n"], noon["moved"], noon["dropped"], noon["safety_dropped"]) == (42, 6, 6, 0), noon


def test_plans_are_deterministic():
    from dispatch.assign import make_plan
    assert make_plan(scored(), order="priority") == plans()[0], "priority plan changed between runs"
    assert make_plan(scored(), order="fifo") == plans()[1], "fifo plan changed between runs"


def test_replan_is_repeatable_and_leaves_input_alone():
    from dispatch.replan import apply_event
    agent, _ = plans()
    before = [len(c["jobs"]) for c in agent["crews"]]
    again = apply_event(agent, {"event": "crew_out", "crew": SICK_CREW, "capacity": 0.0})
    assert again[1]["moved"] == replanned()[1]["moved"] and again[1]["dropped"] == replanned()[1]["dropped"]
    assert [len(c["jobs"]) for c in agent["crews"]] == before, "apply_event modified the 8 a.m. plan"


def test_bad_events_fail_loudly_or_do_nothing():
    from dispatch.replan import apply_event
    agent, _ = plans()
    for bad in ({"event": "crew_out", "crew": 9, "capacity": 0.0},
                {"event": "crew_partial", "crew": 2, "capacity": 1.5}):
        try:
            apply_event(agent, bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")
    same, changes = apply_event(agent, {"event": "unclear", "crew": None, "capacity": 1.0, "question": "?"})
    assert same == agent and not changes["moved"] and not changes["dropped"], "unclear event changed the plan"


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
