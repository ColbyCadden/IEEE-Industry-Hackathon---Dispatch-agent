"""CLI entry point: build 8 a.m. plan, apply disruption, replan, write outputs/."""
import json
from pathlib import Path

from dispatch.assign import make_plan
from dispatch.data_prep import load_clean_tickets
from dispatch.metrics import compute
from dispatch.replan import apply_event
from dispatch.scoring import score

OUT = Path(__file__).resolve().parent / "outputs"
EVENT = {"event": "crew_out", "crew": 4, "capacity": 0.0}


def _write(name: str, data) -> None:
    path = OUT / name
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"wrote {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")


def main(event: dict = EVENT) -> None:
    scored = score(load_clean_tickets())

    plan_8am = make_plan(scored, order="priority")
    plan_fifo = make_plan(scored, order="fifo")
    plan_noon, changes = apply_event(plan_8am, event)

    metrics = {
        "8am": compute(plan_8am),
        "fifo": compute(plan_fifo),
        "noon": compute(plan_noon, changes),
        "changes": changes,
    }

    # What the supervisor hears at 8 a.m. and at noon. The rule-based template keeps this file
    # reproducible; the dashboard asks Claude for the same briefings when a key is set.
    from dispatch.llm import template_briefing
    briefings = {
        "8am": template_briefing(plan_8am, metrics, "8am"),
        "noon": template_briefing(plan_noon, metrics, "noon", changes, event),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    _write("plan_8am.json", plan_8am)
    _write("plan_fifo.json", plan_fifo)
    _write("plan_noon.json", plan_noon)
    _write("metrics.json", metrics)
    _write("event.json", event)
    _write("briefings.json", briefings)

    for key in ("8am", "fifo", "noon"):
        print(f"{key:>5}: {metrics[key]}")
    print(f"\n8 a.m. briefing: {briefings['8am']}\n\nnoon briefing:   {briefings['noon']}")


if __name__ == "__main__":
    main()
