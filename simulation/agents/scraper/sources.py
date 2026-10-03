"""
Scraper agent - sources for Calgary / Alberta traffic data.

Every source here was verified live against the real endpoints. No API keys.
Each fetch_* function returns (rows, meta) where rows is a list of dicts.
"""
import csv
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")

ENDPOINT_511 = "https://511.alberta.ca/Alert/GetUpdatedAlerts?lang=en-US"
ENDPOINT_TRAVEL_TIMES = "https://www.calgary.ca/roads/conditions/travel-times.html"
ENDPOINT_INCIDENTS = "https://data.calgary.ca/resource/35ra-9556.json"
TC_PROXY = "https://trafficcounts.calgary.ca/proxy/proxy.ashx"
TC_MAPSERVER = "http://GISServerExtName/rest/services/ext_TrafficCounts/TCDynamicMap/MapServer"


def _get(url, timeout=30):
    """GET with a browser UA. Returns decoded text."""
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _now():
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Source 1: 511 Alberta statewide alerts (LIVE)
# --------------------------------------------------------------------------
def fetch_511_alerts():
    """Live statewide road/weather alerts from 511 Alberta.

    Returns rows: {alert_id, message, region, high_importance, url, fetched_at}
    This feed is frequently EMPTY - that is correct behaviour, not an error.
    """
    try:
        data = json.loads(_get(ENDPOINT_511, timeout=25))
    except Exception as e:
        return [], {"source": "511_alberta", "ok": False, "error": str(e)}

    alerts = data.get("alerts") or []
    rows = []
    for i, a in enumerate(alerts):
        regions = a.get("regions") or []
        rows.append({
            "alert_id": a.get("id") or f"511-{i}",
            "message": (a.get("message") or "").strip(),
            "region": ",".join(regions) if isinstance(regions, list) else str(regions),
            "high_importance": bool(a.get("highImportance")),
            "url": ENDPOINT_511,
            "fetched_at": _now(),
        })
    meta = {"source": "511_alberta", "ok": True, "count": len(rows),
            "emergency": bool(data.get("emergencyAlertHash")),
            "note": "statewide banner feed; often empty at night"}
    return rows, meta


# --------------------------------------------------------------------------
# Source 2: City of Calgary travel times (LIVE, measured)
# --------------------------------------------------------------------------
_DIR_RE = re.compile(r"(Westbound|Southbound|Northbound|Eastbound)\s+travel time", re.I)
_MIN_RE = re.compile(r"(\d+)\s*MIN", re.I)
_DELAY_RE = re.compile(r"(\d+)\s*MIN\s*Delay", re.I)


def _strip_tags(html):
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</(p|div|li|h\d|tr)>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    html = html.replace("&#39;", "'").replace("&quot;", '"')
    return html


_ROUTE_RE = re.compile(r"^(.+?)\s+to\s+(.+?)\s+via\s+(.+)$", re.I)
_SIMPLE_RE = re.compile(r"^(.+?)\s+to\s+(.+)$", re.I)

# Each corridor is a <li> holding the route in one <div> and the duration
# (optionally with a <span class="delay">) in the next.
_LI_RE = re.compile(
    r"<li[^>]*>\s*<div>(?P<route>.*?)</div>\s*<div[^>]*>(?P<dur>.*?)</div>\s*</li>",
    re.S | re.I)
_DIR_H3_RE = re.compile(r"<h3[^>]*>\s*(Westbound|Southbound|Northbound|Eastbound)"
                        r"\s+travel time:?\s*</h3>", re.I)
_DELAY_SPAN_RE = re.compile(r"class=\"[^\"]*\bdelay\b[^\"]*\"", re.I)


def _parse_corridors_html(html):
    """Parse the travel-times table straight from its HTML.

    Returns (rows, last_updated). Each row:
        (route, origin, destination, via, minutes, delay)
    """
    # Split the document so each direction heading scopes its <li> items.
    marks = [(m.start(), m.group(1).title()) for m in _DIR_H3_RE.finditer(html)]
    rows = []
    for i, (pos, direction) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(html)
        chunk = html[pos:end]
        for m in _LI_RE.finditer(chunk):
            route = _strip_tags(m.group("route")).strip()
            route = re.sub(r"\s+", " ", route)
            dur_raw = m.group("dur")
            dur_text = re.sub(r"\s+", " ", _strip_tags(dur_raw)).strip()

            mins = _MIN_RE.search(dur_text)
            if not mins:
                continue
            minutes = int(mins.group(1))

            # Delay is the span flagged class="... delay".
            delay = 0
            for span in _DELAY_SPAN_RE.finditer(dur_raw):
                st = _strip_tags(span.group(0))
                m2 = _DELAY_RE.search(dur_text) if span else None
                if m2:
                    delay = int(m2.group(1))
                    break
            if delay == 0:
                m3 = _DELAY_RE.search(dur_text)
                if m3:
                    delay = int(m3.group(1))

            m = _ROUTE_RE.match(route)
            if m:
                origin, destination, via = m.group(1), m.group(2), m.group(3)
            else:
                m2 = _SIMPLE_RE.match(route)
                if m2:
                    origin, destination, via = m2.group(1), m2.group(2), ""
                else:
                    origin, destination, via = route, "", ""

            rows.append((route, origin.strip(), destination.strip(),
                         via.strip(), minutes, delay))
    return rows


def fetch_travel_times():
    """Live measured drive times between major Calgary points.

    Returns rows: {corridor, direction, origin, destination, via, minutes,
                   delay_min, congestion, page_last_updated, url, fetched_at}
    congestion = delay-derived: free / light / moderate / heavy
    """
    try:
        html = _get(ENDPOINT_TRAVEL_TIMES, timeout=30)
    except Exception as e:
        return [], {"source": "calgary_travel_times", "ok": False, "error": str(e)}

    updated = ""
    m = re.search(r"Last updated:\s*([^<]+)", html)
    if m:
        updated = m.group(1).strip()

    parsed = _parse_corridors_html(html)
    rows = []
    # Re-walk to attach direction per row.
    direction = ""
    marks = [(m.start(), m.group(1).title()) for m in _DIR_H3_RE.finditer(html)]
    dir_of = {}
    for i, (pos, d) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(html)
        for lm in _LI_RE.finditer(html[pos:end]):
            route = re.sub(r"\s+", " ", _strip_tags(lm.group("route")).strip())
            dir_of.setdefault(route, d)

    for (route, origin, destination, via, minutes, delay) in parsed:
        if delay >= 5:
            congestion = "heavy"
        elif delay >= 2:
            congestion = "moderate"
        elif delay >= 1:
            congestion = "light"
        else:
            congestion = "free"
        rows.append({
            "corridor": route,
            "direction": dir_of.get(route, direction),
            "origin": origin,
            "destination": destination,
            "via": via,
            "minutes": minutes,
            "delay_min": delay,
            "congestion": congestion,
            "page_last_updated": updated,
            "url": ENDPOINT_TRAVEL_TIMES,
            "fetched_at": _now(),
        })

    meta = {"source": "calgary_travel_times", "ok": True, "count": len(rows),
            "note": "measured drive times, refreshed ~every 3 min by the city"}
    return rows, meta


# --------------------------------------------------------------------------
# Source 3: Calgary traffic counts via the site's own proxy (STATIC volumes)
# --------------------------------------------------------------------------
def _tc_query(layer, where="1=1", out_fields=None, count_only=False,
              geometry=False, page_size=1000, max_records=None, offset=0):
    """Query the TCDynamicMap MapServer through trafficcounts' forwarder.

    The ENTIRE target URL (including its query string) must be percent-encoded
    as a single proxy parameter, otherwise the proxy rejects it.
    """
    params = {"where": where, "f": "json"}
    if out_fields:
        params["outFields"] = out_fields
    if count_only:
        params["returnCountOnly"] = "true"
    else:
        params["returnGeometry"] = "true" if geometry else "false"
        params["resultRecordCount"] = str(page_size)
        if offset:
            params["resultOffset"] = str(offset)
        if geometry:
            params["outSR"] = "4326"

    target = f"{TC_MAPSERVER}/{layer}/query?" + urllib.parse.urlencode(params)
    url = TC_PROXY + "?" + urllib.parse.quote(target, safe="")

    txt = _get(url, timeout=45)
    return json.loads(txt)


def fetch_traffic_counts(layer=0, limit=None, with_geometry=False,
                         page_size=1000, max_pages=40):
    """Historic annual traffic volumes at counting locations.

    layer 0 = Traffic Counts at Segment (~9230 rows, the richest).
    Returns rows: {count_id, description, volume, study_date, ...}
    NOTE: these are annual volumes (AADT-like), NOT live speeds.
    """
    fields = "ID,DISTANCE,LATEST_STUDY_DATE,LATEST_STUDY_VOLUME,DESCRIPTION"
    rows = []
    error = None
    try:
        offset = 0
        for _ in range(max_pages):
            data = _tc_query(layer, out_fields=fields,
                             geometry=with_geometry, page_size=page_size,
                             offset=offset)
            feats = data.get("features") or []
            if not feats:
                break
            for f in feats:
                a = f.get("attributes") or {}
                ms = a.get("LATEST_STUDY_DATE")
                study = ""
                if isinstance(ms, (int, float)) and ms > 0:
                    # ArcGIS epoch millis -> ISO date
                    study = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).date().isoformat()
                row = {
                    "count_id": a.get("ID"),
                    "description": a.get("DESCRIPTION") or "",
                    "volume": a.get("LATEST_STUDY_VOLUME"),
                    "study_date": study,
                    "distance_m": a.get("DISTANCE"),
                }
                if with_geometry and f.get("geometry"):
                    x, y = f["geometry"].get("x"), f["geometry"].get("y")
                    if x is not None:
                        row["lon"], row["lat"] = round(x, 6), round(y, 6)
                rows.append(row)
            if len(feats) < page_size:
                break
            offset += page_size
    except Exception as e:
        error = str(e)

    if limit:
        rows = rows[:limit]

    meta = {"source": "traffic_counts", "ok": error is None, "count": len(rows),
            "layer": layer, "geometry": with_geometry,
            "note": "annual volume counts, study dates 2002-2018; not live speed"}
    if error:
        meta["error"] = error
    return rows, meta


def fetch_counts_total(layer=0):
    """Total feature count available on a layer (fast, no payload)."""
    try:
        d = _tc_query(layer, count_only=True)
        return int(d.get("count", 0))
    except Exception:
        return None


# --------------------------------------------------------------------------
# Source 4: Socrata live traffic incidents
# --------------------------------------------------------------------------
def fetch_incidents():
    """Live incident locations from the City of Calgary open data portal."""
    try:
        data = json.loads(_get(ENDPOINT_INCIDENTS + "?$limit=500", timeout=30))
    except Exception as e:
        return [], {"source": "socrata_incidents", "ok": False, "error": str(e)}

    rows = []
    for i, r in enumerate(data if isinstance(data, list) else []):
        def f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return ""
        rows.append({
            "incident_id": r.get("id") or f"inc-{i}",
            "location": r.get("incident_info") or "",
            "description": r.get("description") or "",
            "quadrant": r.get("quadrant") or "",
            "lat": f(r.get("latitude")),
            "lon": f(r.get("longitude")),
            "start_utc": r.get("start_dt_utc") or "",
            "modified_utc": r.get("modified_dt_utc") or "",
            "url": ENDPOINT_INCIDENTS,
            "fetched_at": _now(),
        })
    return rows, {"source": "socrata_incidents", "ok": True, "count": len(rows),
                  "note": "live incident feed from City of Calgary"}


# --------------------------------------------------------------------------
# CSV writing
# --------------------------------------------------------------------------
def write_csv(path, rows, fieldnames=None):
    """Write rows to CSV. Returns path. Creates parent dirs."""
    import os
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if not rows:
        # Still write a header-only file so downstream agents see a valid schema.
        with open(path, "w", newline="", encoding="utf-8") as fh:
            if fieldnames:
                csv.DictWriter(fh, fieldnames=fieldnames).writeheader()
        return path
    if not fieldnames:
        fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))