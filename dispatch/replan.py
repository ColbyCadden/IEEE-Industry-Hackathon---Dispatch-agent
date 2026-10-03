"""Apply one disruption (a crew out sick or at partial capacity) to a plan and replan."""
import copy
import math

from dispatch.assign import JOBS_PER_CREW

NEAREST_CREWS = 3  # a displaced job may only move to one of this many nearest crews


def apply_event(plan: dict, event: dict, jobs: int = JOBS_PER_CREW) -> tuple[dict, dict]:
    """Return (new_plan, changes); changes = {"moved": [{"id","from","to"}], "dropped": [ids]}.

    changes also carries "dropped_jobs" (the dropped job dicts) so metrics can count safety_dropped.
    crew_out: the crew loses all its jobs. crew_partial: it keeps its top round(jobs*capacity).
    Each displaced job (P desc) tries the NEAREST_CREWS nearest other crews by centroid, nearest
    first: it takes a free slot if there is one, else swaps out that crew's lowest-P job if it is
    strictly lower (the bumped job is dropped). A job none of them will take is dropped.
    """
    new_plan = copy.deepcopy(plan)
    changes = {"moved": [], "dropped": [], "dropped_jobs": []}

    kind = event.get("event")
    if kind not in ("crew_out", "crew_partial"):
        return new_plan, changes  # "unclear" or unknown: leave the plan alone

    crews = {c["crew"]: c for c in new_plan["crews"]}
    out_id = event.get("crew")
    if out_id not in crews:
        raise ValueError(f"crew {out_id!r} is not in the plan")

    capacity = float(event.get("capacity", 0.0))
    if not 0.0 <= capacity <= 1.0:
        raise ValueError(f"capacity must be between 0 and 1, got {capacity}")
    keep = 0 if kind == "crew_out" else round(jobs * capacity)
    out_crew = crews[out_id]
    out_crew["jobs"].sort(key=lambda j: j["P"], reverse=True)
    displaced = out_crew["jobs"][keep:]
    out_crew["jobs"] = out_crew["jobs"][:keep]

    others = [c for c in new_plan["crews"] if c["crew"] != out_id]
    for job in sorted(displaced, key=lambda j: j["P"], reverse=True):
        target, bumped = _place(job, others, jobs)
        if target is None:
            _drop(job, changes)
            continue
        changes["moved"].append({"id": job["id"], "from": out_id, "to": target["crew"]})
        if bumped is not None:
            # a job moved in earlier can be bumped later; it is then dropped, not moved
            changes["moved"] = [m for m in changes["moved"] if m["id"] != bumped["id"]]
            _drop(bumped, changes)

    return new_plan, changes


def _place(job: dict, others: list[dict], jobs: int) -> tuple[dict | None, dict | None]:
    """Put job on one of the nearest crews with room or a strictly lower-P job. Return (crew, bumped)."""
    nearest = sorted(others, key=lambda c: _dist(job, c["centroid"]))[:NEAREST_CREWS]
    for crew in nearest:
        if len(crew["jobs"]) < jobs:
            crew["jobs"].append(job)
            return crew, None
        # lowest P; on ties, the job added last (lowest in the original order)
        low = min(reversed(range(len(crew["jobs"]))), key=lambda i: crew["jobs"][i]["P"])
        if crew["jobs"][low]["P"] < job["P"]:
            bumped = crew["jobs"][low]
            crew["jobs"][low] = job
            return crew, bumped
    return None, None


def _drop(job: dict, changes: dict) -> None:
    changes["dropped"].append(job["id"])
    changes["dropped_jobs"].append(job)


def _dist(job: dict, centroid: list[float]) -> float:
    return math.hypot(job["lat"] - centroid[0], job["lon"] - centroid[1])


def _km(job: dict, centroid: list[float]) -> float:
    """Approximate ground distance in km (equirectangular; fine at city scale)."""
    dy = (job["lat"] - centroid[0]) * 111.32
    dx = (job["lon"] - centroid[1]) * 111.32 * math.cos(math.radians(centroid[0]))
    return math.hypot(dx, dy)


if __name__ == "__main__":
    from dispatch.assign import make_plan
    from dispatch.data_prep import load_clean_tickets
    from dispatch.metrics import compute
    from dispatch.scoring import score

    scored = score(load_clean_tickets())

    plan = make_plan(scored, order="priority")
    for event in ({"event": "crew_out", "crew": 4, "capacity": 0.0},
                  {"event": "crew_partial", "crew": 4, "capacity": 0.4}):
        new_plan, changes = apply_event(plan, event)
        m = compute(new_plan, changes)
        sizes = {c["crew"]: len(c["jobs"]) for c in new_plan["crews"]}
        ids = [j["id"] for c in new_plan["crews"] for j in c["jobs"]]
        print(f"\n{event}")
        print(f"  sizes={sizes} total={m['n']} unique={len(set(ids))}")
        print(f"  P {compute(plan)['P']} -> {m['P']}   safety {compute(plan)['safety']} -> {m['safety']}")
        print(f"  moved={m['moved']} dropped={m['dropped']} safety_dropped={m['safety_dropped']}")
        by_crew = {c["crew"]: c for c in new_plan["crews"]}
        for mv in changes["moved"]:
            to = by_crew[mv["to"]]
            job = next(j for j in to["jobs"] if j["id"] == mv["id"])
            print(f"    moved   {mv['id']}  crew {mv['from']} -> {mv['to']}  "
                  f"{_km(job, to['centroid']):.1f} km to new crew centroid")
        for j in changes["dropped_jobs"]:
            print(f"    dropped {j['id']}  P={j['P']} safety={j['safety']}")
    print(f"\nplan unchanged by replan: {sum(len(c['jobs']) for c in plan['crews']) == 40}")
