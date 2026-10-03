"""Improvement-round demo: baseline -> first result -> improved result, on safety coverage.

Run from the repo root:  python -m dispatch.improvement

  1. BASELINE      oldest-first (FIFO) plan, ticket type ignored
  2. FIRST RESULT  priority-scored agent plan (the 8 a.m. plan)
  3. IMPROVED      the agent's plan replanned after one crew goes out

Nothing here changes the engine: it calls the same score / make_plan / apply_event / compute
that `python -m dispatch.run` uses, with the same defaults, so the numbers match
dispatch/outputs/metrics.json. Nothing is written to disk.

`comparison_rows` and `summary_line` are pure functions of a metrics dict, so the dashboard
(dispatch/app.py) shows the same three-way comparison from the same code.
"""

EVENT = {"event": "crew_out", "crew": 4, "capacity": 0.0}  # same disruption as dispatch/run.py

STAGES = (  # (key in the metrics dict, stage name, what it is)
    ("fifo", "1 BASELINE", "oldest-first (FIFO)"),
    ("8am", "2 FIRST RESULT", "priority-scored agent"),
    ("noon", "3 IMPROVED", "replanned, crew {crew} out"),
)


def compute_metrics(event: dict = EVENT) -> dict:
    """Build the three plans and return {"fifo", "8am", "noon", "changes"} (same shape as metrics.json)."""
    from dispatch.assign import make_plan
    from dispatch.data_prep import load_clean_tickets
    from dispatch.metrics import compute
    from dispatch.replan import apply_event
    from dispatch.scoring import score

    scored = score(load_clean_tickets())
    plan_fifo = make_plan(scored, order="fifo")
    plan_agent = make_plan(scored, order="priority")
    plan_noon, changes = apply_event(plan_agent, event)
    return {
        "fifo": compute(plan_fifo),
        "8am": compute(plan_agent),
        "noon": compute(plan_noon, changes),
        "changes": changes,
    }


def comparison_rows(metrics: dict, event: dict | None = None) -> list[dict]:
    """One row per stage: jobs, safety covered, total P; moved/deferred/safety dropped on stage 3 only.

    A stage whose metrics are missing (e.g. no replan has been run yet) comes back with m=None.
    """
    crew = (event or EVENT).get("crew", EVENT["crew"])
    rows = []
    for key, stage, what in STAGES:
        m = metrics.get(key)
        row = {"key": key, "stage": stage, "what": what.format(crew=crew), "m": None}
        if m is not None:
            row["m"] = {"jobs": m["n"], "safety": m["safety"], "P": m["P"]}
            if key == "noon":
                row["m"].update(moved=m.get("moved", 0), deferred=m.get("dropped", 0),
                                safety_dropped=m.get("safety_dropped", 0))
        rows.append(row)
    return rows


def summary_line(rows: list[dict], event: dict | None = None) -> str:
    """One plain-English sentence on what the improvement is, worded from the numbers."""
    crew = (event or EVENT).get("crew", EVENT["crew"])
    base, first, improved = (r["m"] for r in rows)
    if base is None or first is None or improved is None:
        return "Run `python -m dispatch.run` to generate the replanned (improved) result."
    lost = first["safety"] - improved["safety"]
    gain = improved["safety"] - base["safety"]
    if improved["safety_dropped"] == 0 and lost <= 0:
        return (f"With crew {crew} out, the replanned day still covers {improved['safety']} safety tickets "
                f"({base['safety']} for oldest-first with every crew working) and defers none of them: "
                f"{improved['moved']} jobs moved, {improved['deferred']} lower-priority jobs deferred.")
    verb = "still covers" if gain > 0 else "covers"
    return (f"With crew {crew} out, safety coverage goes from {first['safety']} to {improved['safety']} "
            f"({improved['safety_dropped']} safety tickets deferred); the replanned day {verb} "
            f"{abs(gain)} {'more' if gain >= 0 else 'fewer'} than oldest-first at full strength.")


def format_table(rows: list[dict]) -> str:
    """Fixed-width comparison table (ASCII only, so it prints on any Windows console)."""
    head = (f"{'Stage':<42}{'Jobs':>6}{'Safety covered':>16}{'Total P':>10}"
            f"{'Moved':>8}{'Deferred':>10}{'Safety dropped':>16}")
    lines = [head, "-" * len(head)]
    for r in rows:
        m = r["m"]
        label = f"{r['stage']}  {r['what']}"
        if m is None:
            lines.append(f"{label:<42}{'n/a':>6}")
            continue
        extra = ("-", "-", "-") if "moved" not in m else (m["moved"], m["deferred"], m["safety_dropped"])
        lines.append(f"{label:<42}{m['jobs']:>6}{m['safety']:>16}{m['P']:>10.2f}"
                     f"{extra[0]:>8}{extra[1]:>10}{extra[2]:>16}")
    return "\n".join(lines)


def main(event: dict = EVENT) -> None:
    metrics = compute_metrics(event)
    rows = comparison_rows(metrics, event)
    print("IMPROVEMENT ROUND: baseline -> first result -> improved result")
    from dispatch.weights import SAFETY_TYPES, SHORT_NAMES
    kinds = ", ".join(sorted(SHORT_NAMES[n].lower() for n in SAFETY_TYPES))
    print(f"Disruption: crew {event['crew']} out for the day. Safety coverage = safety tickets "
          f"({kinds}) in the day's plan.\n")
    print(format_table(rows))
    print(f"\n{summary_line(rows, event)}")


if __name__ == "__main__":
    main()
