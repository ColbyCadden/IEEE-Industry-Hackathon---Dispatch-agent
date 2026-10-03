#!/usr/bin/env python
"""
Orchestrator - runs the scraper, then the simulator agent, in one shot.

Usage:
    python -m agents.run_pipeline                 # scrape + plan
    python -m agents.run_pipeline --apply         # ... and push to the sim
    python -m agents.run_pipeline --watch 300     # loop every 5 min, forever
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable


def _run(args):
    """Run a module with the project venv. Returns (code, stdout+stderr)."""
    cmd = [PY, "-m"] + args
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def cycle(apply=False, quiet=False):
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"\n=== cycle {stamp} UTC ===")

    code, out = _run(["agents.requests311.run"])
    print(out.strip())
    code, out = _run(["agents.scraper.run"])
    if not quiet:
        print(out.strip())
    if code != 0:
        print("scraper reported failures (see above)", file=sys.stderr)

    # Priority list: the real priority agent will write agents/data/priority_list.csv.
    # Until it exists, a clearly-labelled DEMO list is generated so routing has input.
    if not os.path.exists(os.path.join(ROOT, "agents", "data", "priority_list.csv")):
        _, o = _run(["agents.dispatch.fake_priority"])
        print(o.strip())
    code3, o3 = _run(["agents.dispatch.planner"])
    print(o3.strip().splitlines()[0] if o3.strip() else "planner: no output")
    if code3 != 0:
        print("route planner failed: " + o3, file=sys.stderr)

    sim_args = ["agents.simulator.run"]
    if apply:
        sim_args.append("--apply")
    code2, out2 = _run(sim_args)
    print(out2.strip())
    if code2 != 0:
        print("simulator agent failed", file=sys.stderr)
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="scraper -> simulator pipeline")
    ap.add_argument("--apply", action="store_true",
                    help="push the plan to the running calgary3d server")
    ap.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                    help="repeat on an interval instead of running once")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.watch:
        interval = max(30, args.watch)
        print(f"watching every {interval}s - Ctrl-C to stop")
        while True:
            try:
                cycle(apply=args.apply, quiet=args.quiet)
            except KeyboardInterrupt:
                print("\nstopped")
                return 0
            except Exception as e:
                print(f"cycle error: {e}", file=sys.stderr)
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                print("\nstopped")
                return 0
    else:
        return 0 if cycle(apply=args.apply, quiet=args.quiet) else 1


if __name__ == "__main__":
    sys.exit(main())