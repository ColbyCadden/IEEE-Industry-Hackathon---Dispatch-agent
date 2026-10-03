#!/usr/bin/env python
"""
Simulator agent - reads scraper CSVs and produces a traffic control plan.

Usage:
    python -m agents.simulator.run              # plan only, writes CSV
    python -m agents.simulator.run --apply      # also POST to a running server
    python -m agents.simulator.run --json       # print the plan as JSON
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agents.simulator import optimizer as O

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(HERE, "data")


def main():
    ap = argparse.ArgumentParser(description="Simulator/optimizer agent")
    ap.add_argument("--apply", action="store_true",
                    help="POST the plan to a running calgary3d server")
    ap.add_argument("--server", default="http://localhost:8765")
    ap.add_argument("--close-threshold", type=float, default=0.82)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    travel = O.load_travel_times()
    incidents = O.load_incidents()
    counts = O.load_counts()
    O.alerts_list = O.load_alerts()
    edges = O.load_scene_edges()

    if not edges:
        print("ERROR: could not load scene.json road edges", file=sys.stderr)
        return 2

    demand, stats = O.build_demand_model(travel, incidents, counts, edges)
    plan = O.optimize(demand, edges, close_threshold=args.close_threshold)

    plan_path = os.path.join(DATA_DIR, "control_plan.csv")
    O.write_control_plan(plan_path, demand, plan)

    summary = {"inputs": {
                   "travel_times_rows": len(travel),
                   "incidents_rows": len(incidents),
                   "traffic_count_rows": len(counts),
                   "alerts_rows": len(O.alerts_list),
               },
               "model": stats,
               "plan": plan,
               "control_plan_csv": plan_path}

    if args.as_json:
        print(json.dumps(summary, indent=2))
    else:
        print("=== simulator agent ===")
        print(f"inputs   : {summary['inputs']}")
        print(f"model    : {stats}")
        m = plan["metrics"]
        print(f"plan     : demand_scale={plan['demand_scale']} "
              f"speed_scale={plan['speed_scale']} "
              f"signal_green_bonus={plan['signal_green_bonus']}")
        print(f"           closing {len(plan['closed_edges'])} saturated edges")
        print(f"cost     : {m['cost']} (lower is better)")
        print(f"edges    : {m['edges_considered']} considered, "
              f"{m['hot_edges']} hot (pressure>= {args.close_threshold})")
        print(f"csv      : {plan_path}")

    if args.apply:
        try:
            resp = O.apply_plan(plan, base=args.server)
            print(f"\napplied to {args.server} -> {resp}")
        except Exception as e:
            print(f"\napply failed: {e}", file=sys.stderr)
            return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())