"""
STAND-IN for the priority agent that doesn't exist yet.

Writes agents/data/priority_list.csv - the file the planner reads. The real
priority agent only has to produce the same columns (see README.md) and the
planner needs no changes.

Picks 48 open/overdue 311 requests inside the sim area (the planner uses the top
8 x teams = 40, the rest are backfill when issues get fixed), with made-up
severity scores. Deterministic (seeded).
"""
import csv, os, random, sys

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT = os.path.join(DATA, "priority_list.csv")
FIELDS = ["rank", "request_id", "category", "priority", "x", "y", "location", "source_agent"]
BASE = {"signal": 9, "pothole": 8, "debris": 7, "road_maintenance": 6, "dead_animal": 5,
        "sign": 4, "markings": 3}


def build(n=48, seed=11, max_per_cat=999):
    rnd = random.Random(seed)
    rows = []
    with open(os.path.join(DATA, "requests.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["status"].lower() in ("open", "overdue") and r["in_network"] == "1":
                p = BASE.get(r["category"], 4) + (1 if r["status"].lower() == "overdue" else 0) + rnd.choice((-1, 0, 0, 1, 2))
                rows.append((p, rnd.random(), r))
    rows.sort(key=lambda t: (-t[0], t[1]))
    rows = rows[:]
    pick, per = [], {}
    for p, _, r in rows:
        if per.get(r["category"], 0) >= max_per_cat:
            continue
        per[r["category"]] = per.get(r["category"], 0) + 1
        pick.append((max(1, min(p, 10)), r))
        if len(pick) == n:
            break
    return [dict(rank=i + 1, request_id=r["id"], category=r["category"], priority=p,
                 x=r["x"], y=r["y"], location=r["location"], source_agent="fake_priority_demo")
            for i, (p, r) in enumerate(pick)]


def main():
    rows = build()
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    print(f"wrote FAKE priority list: {len(rows)} locations -> {OUT}")
    for r in rows:
        print(f"  #{r['rank']} p={r['priority']} {r['category']:16} {r['location'][:42]}")

if __name__ == "__main__":
    sys.exit(main())
