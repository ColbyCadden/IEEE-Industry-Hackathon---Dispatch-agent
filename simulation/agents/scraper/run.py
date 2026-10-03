#!/usr/bin/env python
"""
Scraper agent - pulls traffic data from every source into CSV.

Usage:
    python -m agents.scraper.run                  # all sources
    python -m agents.scraper.run --only travel_times
    python -m agents.scraper.run --counts-limit 5000

Writes into agents/data/ (the contract the simulator agent reads).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agents.scraper import sources as S

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")

ALERT_FIELDS = ["alert_id", "message", "region", "high_importance", "url", "fetched_at"]
TRAVEL_FIELDS = ["corridor", "direction", "origin", "destination", "via", "minutes",
                 "delay_min", "congestion", "page_last_updated", "url", "fetched_at"]
COUNT_FIELDS = ["count_id", "description", "volume", "study_date", "distance_m"]
INCIDENT_FIELDS = ["incident_id", "location", "description", "quadrant",
                   "lat", "lon", "start_utc", "modified_utc", "url", "fetched_at"]


def main():
    ap = argparse.ArgumentParser(description="Calgary traffic scraper agent")
    ap.add_argument("--only", help="travel_times | alerts | counts | incidents")
    ap.add_argument("--counts-limit", type=int, default=None,
                    help="cap traffic-count rows (default: all)")
    ap.add_argument("--counts-geometry", action="store_true",
                    help="include lon/lat for count locations")
    ap.add_argument("--out", default=DATA_DIR)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    only = args.only
    report = {}

    def want(name):
        return only is None or only == name

    # --- live travel times ---
    if want("travel_times"):
        t0 = time.time()
        rows, meta = S.fetch_travel_times()
        p = os.path.join(args.out, "travel_times.csv")
        S.write_csv(p, rows, TRAVEL_FIELDS)
        meta["seconds"] = round(time.time() - t0, 2)
        meta["file"] = p
        report["travel_times"] = meta
        print(f"[travel_times] {meta.get('count', 0)} corridors -> {p}")

    # --- 511 statewide alerts ---
    if want("alerts"):
        t0 = time.time()
        rows, meta = S.fetch_511_alerts()
        p = os.path.join(args.out, "alerts_511.csv")
        S.write_csv(p, rows, ALERT_FIELDS)
        meta["seconds"] = round(time.time() - t0, 2)
        meta["file"] = p
        report["alerts_511"] = meta
        print(f"[alerts_511]   {meta.get('count', 0)} alerts -> {p}")

    # --- socrata incidents ---
    if want("incidents"):
        t0 = time.time()
        rows, meta = S.fetch_incidents()
        p = os.path.join(args.out, "incidents.csv")
        S.write_csv(p, rows, INCIDENT_FIELDS)
        meta["seconds"] = round(time.time() - t0, 2)
        meta["file"] = p
        report["incidents"] = meta
        print(f"[incidents]    {meta.get('count', 0)} incidents -> {p}")

    # --- traffic counts (paginated) ---
    if want("counts"):
        t0 = time.time()
        fields = COUNT_FIELDS + (["lon", "lat"] if args.counts_geometry else [])
        rows, meta = S.fetch_traffic_counts(0, limit=args.counts_limit,
                                            with_geometry=args.counts_geometry)
        total = S.fetch_counts_total(0)
        p = os.path.join(args.out, "traffic_counts.csv")
        S.write_csv(p, rows, fields)
        meta["available_total"] = total
        meta["seconds"] = round(time.time() - t0, 2)
        meta["file"] = p
        report["traffic_counts"] = meta
        print(f"[counts]       {len(rows)} of {total} segments -> {p}")

    rp = os.path.join(args.out, "scrape_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nreport -> {rp}")
    bad = [k for k, v in report.items() if not v.get("ok")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())