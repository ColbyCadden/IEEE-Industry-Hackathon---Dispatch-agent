"""CLI entry point: build 8 a.m. plan, apply disruption, replan, write outputs/."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "outputs"


def main() -> None:
    # tickets = data_prep.load_tickets()
    # plan_8am = assign.assign_scored(scoring.score_tickets(tickets))
    # event = replan.apply_event({...}); plan_noon = replan.replan(tickets, plan_8am, event)
    # write plan_8am.json, plan_noon.json, metrics.json, event.json to OUT
    raise NotImplementedError


if __name__ == "__main__":
    main()
