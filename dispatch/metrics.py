"""Summarise a plan: total priority covered, safety jobs, and disruption churn."""


def compute(plan: dict, changes: dict | None = None) -> dict:
    """Return {"P", "safety", "n"}, plus "moved", "dropped", "safety_dropped" when changes is given.

    safety_dropped is read from changes["dropped_jobs"] (job dicts that replan adds
    alongside the contract's "dropped" id list).
    """
    jobs = [j for c in plan["crews"] for j in c["jobs"]]
    out = {
        "P": round(sum(j["P"] for j in jobs), 2),
        "safety": sum(bool(j["safety"]) for j in jobs),
        "n": len(jobs),
    }
    if changes is not None:
        out["moved"] = len(changes.get("moved", []))
        out["dropped"] = len(changes.get("dropped", []))
        out["safety_dropped"] = sum(bool(j["safety"]) for j in changes.get("dropped_jobs", []))
    return out


if __name__ == "__main__":
    from dispatch.assign import make_plan
    from dispatch.data_prep import load_clean_tickets
    from dispatch.scoring import score

    scored = score(load_clean_tickets())

    agent = compute(make_plan(scored, order="priority"))
    fifo = compute(make_plan(scored, order="fifo"))
    available = int(scored["safety"].sum())

    print(f"{'':8}{'P':>9}{'safety':>9}{'n':>5}")
    print(f"{'agent':8}{agent['P']:>9.2f}{agent['safety']:>9}{agent['n']:>5}")
    print(f"{'fifo':8}{fifo['P']:>9.2f}{fifo['safety']:>9}{fifo['n']:>5}")
    print(f"{'delta':8}{agent['P'] - fifo['P']:>+9.2f}{agent['safety'] - fifo['safety']:>+9}")
    print(f"\nSafety tickets available in the backlog: {available}")
