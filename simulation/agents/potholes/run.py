#!/usr/bin/env python
"""
Pothole agents (two roles in one module, both write to agents/data/):

  scout    - queries the City of Calgary Live Maps ArcGIS service (the same
             backend the Motorola LiveMaps page draws from) for pothole 311
             requests: layer 51 Open, 52 Closed, 53 Overdue.
  analyst  - reads your 311_dispatch_sample.csv, keeps pothole rows, drops
             duplicates and closed requests.

Both project lon/lat into SUMO metres and flag whether each pothole is INSIDE
the downtown network. Output: potholes.csv + potholes_report.json.
Potholes are display-only: nothing here touches traffic.

    python -m agents.potholes.run              # scout + analyst
    python -m agents.potholes.run --only scout
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
LAYERS = {51: "Open", 52: "Closed", 53: "Overdue"}
FIELDS = ["source", "id", "status", "opened", "location", "community",
          "lon", "lat", "x", "y", "in_network"]
CSV_IN = os.path.join(DATA, "311_dispatch_sample.csv")


def net_info():
    """netOffset and convBoundary straight from the SUMO network file."""
    head = open(os.path.join(ROOT, "calgary", "dt.net.xml"), encoding="utf-8").read(4000)
    off = tuple(float(v) for v in re.search(r'netOffset="([^"]+)"', head).group(1).split(","))
    bnd = [float(v) for v in re.search(r'convBoundary="([^"]+)"', head).group(1).split(",")]
    return off, bnd


def project(lon, lat, off, bnd):
    x, y = latlon_to_xy(lat, lon, off)
    inside = bnd[0] <= x <= bnd[2] and bnd[1] <= y <= bnd[3]
    return round(x, 1), round(y, 1), int(inside)


def scout(off, bnd):
    rows, meta = [], {"ok": True, "layers": {}}
    for lid, status in LAYERS.items():
        q = urllib.parse.urlencode({"where": "1=1", "outSR": 4326, "f": "json",
              "outFields": "SR_NUMBER,STATUS,OPENED_DATE,LOCATION,COMMUNITY",
              "returnGeometry": "true", "resultRecordCount": 2000})
        try:
            with urllib.request.urlopen(f"{BASE}/{lid}/query?{q}", timeout=40) as r:
                d = json.load(r)
            feats = d["features"]
            meta["layers"][status] = len(feats)
        except Exception as e:           # a dead source degrades to zero rows
            meta["ok"] = False
            meta["layers"][status] = f"error: {e}"
            continue
        for f in feats:
            a, g = f["attributes"], f.get("geometry") or {}
            if "x" not in g:
                continue
            x, y, ins = project(g["x"], g["y"], off, bnd)
            ts = a.get("OPENED_DATE")
            rows.append(dict(source="live", id=a["SR_NUMBER"], status=a.get("STATUS") or status,
                opened=datetime.fromtimestamp(ts / 1000, timezone.utc).date().isoformat() if ts else "",
                location=a.get("LOCATION") or "", community=a.get("COMMUNITY") or "",
                lon=round(g["x"], 6), lat=round(g["y"], 6), x=x, y=y, in_network=ins))
    return rows, meta


def analyst(off, bnd):
    rows, meta = [], {"ok": os.path.exists(CSV_IN), "total_rows": 0}
    if not meta["ok"]:
        return rows, meta
    seen, dup, closed = set(), 0, 0
    with open(CSV_IN, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            meta["total_rows"] += 1
            if "pothole" not in r["service_name"].lower():
                continue
            st = r["status_description"]
            if "duplicate" in st.lower():
                dup += 1; continue
            if st.lower() == "closed":
                closed += 1; continue
            key = (r["longitude"], r["latitude"])
            if key in seen:
                dup += 1; continue
            seen.add(key)
            lon, lat = float(r["longitude"]), float(r["latitude"])
            x, y, ins = project(lon, lat, off, bnd)
            rows.append(dict(source="csv", id=r["service_request_id"], status=st,
                opened=r["requested_date"], location=r["address"], community=r["comm_name"],
                lon=round(lon, 6), lat=round(lat, 6), x=x, y=y, in_network=ins))
    meta.update(duplicates_dropped=dup, closed_dropped=closed, open_kept=len(rows))
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
        print(f"[pothole scout]   live layers {m['layers']}")
    if a.only in (None, "analyst"):
        r, m = analyst(off, bnd); rows += r; report["analyst"] = m
        print(f"[pothole analyst] {m}")
    # keep the other source's rows if only one agent ran
    out = os.path.join(DATA, "potholes.csv")
    if a.only and os.path.exists(out):
        keep = "csv" if a.only == "scout" else "live"
        with open(out, encoding="utf-8", newline="") as fh:
            rows += [r for r in csv.DictReader(fh) if r["source"] == keep]
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    inn = [r for r in rows if str(r["in_network"]) == "1" and r["status"].lower() != "closed"]
    report["total"] = len(rows)
    report["in_network_open"] = len(inn)
    with open(os.path.join(DATA, "potholes_report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"potholes.csv: {len(rows)} rows, {len(inn)} open inside the sim area -> {out}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
