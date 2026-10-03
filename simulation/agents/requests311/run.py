#!/usr/bin/env python
"""
311 request agents (display only - nothing here touches traffic).

  scout   - queries the City of Calgary Live Maps ArcGIS service (backend of the
            Motorola LiveMaps page) for every Roads category with an Open /
            Closed / Overdue layer.
  analyst - reads 311_dispatch_sample.csv (potholes, debris, signs, trash...),
            drops duplicates, keeps open + closed.

Rows outside the downtown SUMO network are counted in the report but not
written to requests.csv (the sim can't show them).

    python -m agents.requests311.run [--only scout|analyst]
"""
import argparse, csv, json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
sys.path.insert(0, os.path.join(ROOT, "calgary3d", "server"))
from export_scene import latlon_to_xy  # noqa: E402

BASE = ("https://calgary-csrprod.motorolasolutions.com/arcgis/rest/services/"
        "Calgary_LiveMaps/CalgaryLiveMaps_AllAvailableSRs_Split/MapServer")
# category -> (live layer ids [open, closed, overdue] groups, CSV service_name substrings)
CATEGORIES = {
    "pothole":   ([(51, 52, 53)], ["pothole"]),
    "debris":    ([(26, 27, 28)], ["debris"]),
    "sign":      ([(69, 70, 71), (73, 74, 75)], ["signs -"]),
    "signal":    ([(56, 57, 58), (60, 61, 62), (64, 65, 66)], []),
    "trash":     ([], ["wrs -"]),
    "street_cleaning": ([(82, 83, 84)], []),
    "dead_animal":     ([(30, 31, 32)], []),
    "sidewalk_curb":   ([(14, 15, 16)], []),
    "road_maintenance": ([(2, 3, 4), (6, 7, 8), (10, 11, 12), (34, 35, 36), (38, 39, 40)], []),
    "markings":  ([(42, 43, 44)], []),
    "snow_ice":  ([(77, 78, 79)], []),
}
STATUSES = ("Open", "Closed", "Overdue")
FIELDS = ["category", "source", "id", "status", "opened", "location", "community",
          "lon", "lat", "x", "y", "in_network"]
CSV_IN = os.path.join(DATA, "311_dispatch_sample.csv")


def net_info():
    head = open(os.path.join(ROOT, "calgary", "dt.net.xml"), encoding="utf-8").read(4000)
    off = tuple(float(v) for v in re.search(r'netOffset="([^"]+)"', head).group(1).split(","))
    bnd = [float(v) for v in re.search(r'convBoundary="([^"]+)"', head).group(1).split(",")]
    return off, bnd


def project(lon, lat, off, bnd):
    x, y = latlon_to_xy(lat, lon, off)
    return round(x, 1), round(y, 1), int(bnd[0] <= x <= bnd[2] and bnd[1] <= y <= bnd[3])


def scout(off, bnd):
    rows, meta = [], {"ok": True, "per_category": {}}
    for cat, (groups, _) in CATEGORIES.items():
        tot = ins = 0
        for grp in groups:
            for lid, status in zip(grp, STATUSES):
                q = urllib.parse.urlencode({"where": "1=1", "outSR": 4326, "f": "json",
                      "outFields": "SR_NUMBER,STATUS,OPENED_DATE,LOCATION,COMMUNITY",
                      "returnGeometry": "true", "resultRecordCount": 2000})
                try:
                    with urllib.request.urlopen(f"{BASE}/{lid}/query?{q}", timeout=40) as r:
                        feats = json.load(r)["features"]
                except Exception as e:      # dead layer degrades to zero rows
                    meta["ok"] = False
                    meta.setdefault("errors", []).append(f"{cat}/{lid}: {e}")
                    continue
                for f in feats:
                    a, g = f["attributes"], f.get("geometry") or {}
                    if "x" not in g:
                        continue
                    tot += 1
                    x, y, i = project(g["x"], g["y"], off, bnd)
                    if not i:
                        continue
                    ins += 1
                    ts = a.get("OPENED_DATE")
                    rows.append(dict(category=cat, source="live", id=a["SR_NUMBER"],
                        status=a.get("STATUS") or status,
                        opened=datetime.fromtimestamp(ts / 1000, timezone.utc).date().isoformat() if ts else "",
                        location=a.get("LOCATION") or "", community=a.get("COMMUNITY") or "",
                        lon=round(g["x"], 6), lat=round(g["y"], 6), x=x, y=y, in_network=1))
        meta["per_category"][cat] = {"fetched": tot, "in_network": ins}
    return rows, meta


def analyst(off, bnd):
    rows, meta = [], {"ok": os.path.exists(CSV_IN), "total_rows": 0, "per_category": {}}
    if not meta["ok"]:
        return rows, meta
    seen = set()
    with open(CSV_IN, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            meta["total_rows"] += 1
            svc = r["service_name"].lower()
            cat = next((c for c, (_, keys) in CATEGORIES.items() if any(k in svc for k in keys)), None)
            if not cat or "duplicate" in r["status_description"].lower():
                continue
            key = (r["longitude"], r["latitude"], cat)
            if key in seen:
                continue
            seen.add(key)
            lon, lat = float(r["longitude"]), float(r["latitude"])
            x, y, i = project(lon, lat, off, bnd)
            m = meta["per_category"].setdefault(cat, {"fetched": 0, "in_network": 0})
            m["fetched"] += 1
            if not i:
                continue
            m["in_network"] += 1
            rows.append(dict(category=cat, source="csv", id=r["service_request_id"],
                status=r["status_description"], opened=r["requested_date"],
                location=r["address"], community=r["comm_name"],
                lon=round(lon, 6), lat=round(lat, 6), x=x, y=y, in_network=1))
    return rows, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["scout", "analyst"])
    a = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    off, bnd = net_info()
    rows, report = [], {"fetched_at": datetime.now(timezone.utc).isoformat()}
    if a.only in (None, "scout"):
        r, m = scout(off, bnd); rows += r; report["scout"] = m
    if a.only in (None, "analyst"):
        r, m = analyst(off, bnd); rows += r; report["analyst"] = m
    out = os.path.join(DATA, "requests.csv")
    if a.only and os.path.exists(out):
        keep = "csv" if a.only == "scout" else "live"
        with open(out, encoding="utf-8", newline="") as fh:
            rows += [r for r in csv.DictReader(fh) if r["source"] == keep]
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    report["rows_in_sim_area"] = len(rows)
    with open(os.path.join(DATA, "requests_report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    for src in ("scout", "analyst"):
        for c, m in report.get(src, {}).get("per_category", {}).items():
            print(f"[{src:7}] {c:17} fetched {m['fetched']:5}  in sim area {m['in_network']:4}")
    by = {}
    for r in rows:
        by[r["category"]] = by.get(r["category"], 0) + 1
    print(f"requests.csv: {len(rows)} rows in sim area  {by}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
