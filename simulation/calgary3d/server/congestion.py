#!/usr/bin/env python3
"""Congestion model: fetch live incidents + compute per-edge congestion levels.

Fetches City of Calgary Current Traffic Incidents from Socrata API.
Computes congestion level per edge based on:
  - MEASURED travel times from calgary.ca (live, ~3 min refresh)
  - Historic annual traffic volumes from trafficcounts.calgary.ca
  - Live incident proximity
  - Typical-by-hour model ONLY as a last-resort floor

The measured sources are supplied by the scraper agent, which writes
    agents/data/travel_times.csv
    agents/data/traffic_counts.csv
    agents/data/incidents.csv
    agents/data/control_plan.csv
When those files are present they drive the model; when absent the server
degrades gracefully to the previous heuristic (and says so in `mode`).

Output: per-edge congestion level 0..1 (only level >= 0.05 reported).
Caching: 5-min cache on incidents, stale flag on fetch failure.
Never crashes the server: all failures are caught and logged.
"""

import csv
import json
import os
import re
import time
import math
import threading
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# Try to get Alberta timezone; fall back to fixed UTC-6 if tzdata unavailable
try:
    CALGARY_TZ = ZoneInfo('America/Edmonton')
except Exception:
    # Fallback: fixed UTC-6 (always, no DST simulation)
    CALGARY_TZ = timezone(timedelta(hours=-6))

SOCRATA_URL = "https://data.calgary.ca/resource/4jah-h97u.json"
FETCH_TIMEOUT_S = 10
CACHE_TTL_S = 300  # 5 minutes

# agents/data/ lives two levels up from calgary3d/server/
_AGENT_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'agents', 'data')

# Road-name tokens that carry no identifying information. Calgary's grid is
# numeric ("16 AVE NW"), so digits ARE meaningful - these words are not.
_STOPWORDS = {
    'STREET', 'AVENUE', 'ROAD', 'TRAIL', 'BOULEVARD', 'BLVD', 'DRIVE',
    'WAY', 'CRESCENT', 'COURT', 'PLACE', 'TERRACE', 'HIGHWAY', 'PARKWAY',
    'LANE', 'CRES', 'ROW', 'WALK', 'CLOSE', 'GARDENS', 'PARK', 'RIDGE',
    'HILL', 'HILLS', 'VALLEY', 'MEWS', 'LINK', 'SQUARE', 'POINT', 'LOOP',
    'ALLEY', 'MIN', 'DELAY', 'VIA', 'INTERSECTION', 'NORTH', 'SOUTH',
    'EAST', 'WEST', 'MINUTE', 'AVEN', 'STR',
}

_TYPE_CANON = {
    'ST': 'STREET', 'STR': 'STREET', 'AVE': 'AVENUE', 'AV': 'AVENUE',
    'RD': 'ROAD', 'DR': 'DRIVE', 'BLVD': 'BOULEVARD', 'BV': 'BOULEVARD',
    'TR': 'TRAIL', 'TL': 'TRAIL', 'CR': 'CRESCENT', 'CT': 'COURT',
    'PL': 'PLACE', 'TER': 'TERRACE', 'HWY': 'HIGHWAY', 'PKWY': 'PARKWAY',
    'LN': 'LANE', 'CRES': 'CRESCENT', 'ROW': 'ROW', 'WALK': 'WALK',
    'CL': 'CLOSE', 'GDNS': 'GARDENS', 'PK': 'PARK', 'RDGE': 'RIDGE',
    'HILL': 'HILL', 'VAL': 'VALLEY', 'MEWS': 'MEWS', 'SQ': 'SQUARE',
    'PT': 'POINT', 'ALY': 'ALLEY', 'WY': 'WAY', 'TRL': 'TRAIL',
}

# Thread-safe cache
_cache_lock = threading.Lock()
_cache = {
    'incidents': [],
    'fetched_at': 0,
    'stale': False,
    'error': None,
}

_measured_lock = threading.Lock()
_measured_cache = {'token_index': None, 'mtime': 0, 'loaded_at': 0}


def _tokens(name):
    """Normalised road-name tokens; digits are first-class (Calgary grid)."""
    s = (name or '').upper()
    s = re.sub(r'\b(AT|NB|SB|EB|WB)\b', ' ', s)
    s = re.sub(r'\b(NORTHBOUND|SOUTHBOUND|EASTBOUND|WESTBOUND)\b', ' ', s)
    s = re.sub(r'\b(VI[A-Z]*|TO|FROM|AND|NEAR|OF|THE|WITH)\b', ' ', s)
    out = []
    for p in re.split(r'[^A-Z0-9]+', s):
        if not p:
            continue
        if p.isdigit():
            out.append(p)
        elif p in _TYPE_CANON:
            out.append(_TYPE_CANON[p])
        elif len(p) > 2 and p.isalpha():
            out.append(p)
    return {p for p in out if p not in _STOPWORDS}


def _load_measured_index(roads):
    """Build token -> edge-id index, enriched with count volumes.

    Returns (token_index, volumes) where volumes maps edge_id -> annual
    volume from traffic_counts.csv. Re-read only when the CSV changes.
    """
    with _measured_lock:
        tt = os.path.join(_AGENT_DATA, 'traffic_counts.csv')
        mtime = os.path.getmtime(tt) if os.path.exists(tt) else 0
        if _measured_cache['token_index'] is not None and mtime == _measured_cache['mtime']:
            return _measured_cache['token_index'], _measured_cache.get('volumes', {})

    token_index = defaultdict(list)
    for road in roads:
        for t in _tokens(road.get('name', '')):
            token_index[t].append(road['id'])

    volumes = {}
    if mtime:
        # description -> highest observed annual volume
        best_by_desc = {}
        try:
            with open(tt, newline='', encoding='utf-8') as fh:
                for row in csv.DictReader(fh):
                    try:
                        vol = float(row.get('volume') or 0)
                    except (TypeError, ValueError):
                        continue
                    if vol <= 0:
                        continue
                    key = _tokens(row.get('description', ''))
                    if not key:
                        continue
                    sig = frozenset(key)
                    if vol > best_by_desc.get(sig, 0):
                        best_by_desc[sig] = vol
        except Exception:
            best_by_desc = {}

        for sig, vol in best_by_desc.items():
            hits = set()
            nums = {t for t in sig if t.isdigit()}
            words = sig - nums
            strong = {t for t in words if len(t) >= 4 and len(token_index.get(t, [])) <= 60}
            for t in strong:
                hits.update(token_index.get(t, []))
            if not hits:
                for n in nums:
                    if 0 < len(token_index.get(n, [])) <= 40:
                        hits.update(token_index.get(n, []))
            for eid in hits:
                if vol > volumes.get(eid, 0):
                    volumes[eid] = vol

    with _measured_lock:
        _measured_cache['token_index'] = token_index
        _measured_cache['volumes'] = volumes
        _measured_cache['mtime'] = mtime
        _measured_cache['loaded_at'] = time.time()
    return token_index, volumes


def _load_travel_pressure(token_index):
    """Measured travel-time delays -> {edge_id: weight 0..1}.

    This is the only genuinely MEASURED congestion signal available for
    Calgary; it drives the model whenever the scraper has run.
    """
    path = os.path.join(_AGENT_DATA, 'travel_times.csv')
    if not os.path.exists(path):
        return {}, 0
    out = {}
    total = 0
    try:
        with open(path, newline='', encoding='utf-8') as fh:
            for row in csv.DictReader(fh):
                total += 1
                try:
                    delay = float(row.get('delay_min') or 0)
                    minutes = float(row.get('minutes') or 0)
                except (TypeError, ValueError):
                    continue
                label = ' '.join(str(row.get(k) or '') for k in
                                 ('origin', 'destination', 'via', 'corridor'))
                toks = _tokens(label)
                if not toks:
                    continue
                nums = {t for t in toks if t.isdigit()}
                words = toks - nums
                hits = set()
                strong = {t for t in words if len(t) >= 4 and len(token_index.get(t, [])) <= 60}
                for t in strong:
                    hits.update(token_index.get(t, []))
                if not hits:
                    for n in nums:
                        if 0 < len(token_index.get(n, [])) <= 40:
                            hits.update(token_index.get(n, []))
                if not hits:
                    continue
                weight = min(1.0, delay / 5.0) * 0.65 + min(1.0, minutes / 45.0) * 0.35
                for eid in hits:
                    if weight > out.get(eid, 0.0):
                        out[eid] = weight
    except Exception:
        return {}, 0
    return out, total


def _point_to_line_distance(px, py, x1, y1, x2, y2):
    """Perpendicular distance from point (px, py) to line segment (x1,y1)-(x2,y2)."""
    dx = x2 - x1
    dy = y2 - y1
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-9:
        return math.sqrt((px - x1) ** 2 + (py - y1) ** 2)
    
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / len_sq))
    closest_x = x1 + t * dx
    closest_y = y1 + t * dy
    return math.sqrt((px - closest_x) ** 2 + (py - closest_y) ** 2)


def _nearest_distance_to_edge_shape(px, py, shape):
    """Nearest perpendicular distance from point to edge polyline."""
    if len(shape) < 2:
        return float('inf')
    
    min_dist = float('inf')
    for i in range(len(shape) - 1):
        x1, y1 = shape[i]
        x2, y2 = shape[i + 1]
        dist = _point_to_line_distance(px, py, x1, y1, x2, y2)
        min_dist = min(min_dist, dist)
    return min_dist


def _is_severe_incident(incident_type):
    """Return True if incident is closure/accident (higher boost), else construction/info."""
    if not incident_type:
        return False
    lower = incident_type.lower()
    return any(kw in lower for kw in ['closure', 'closed', 'accident', 'collision', 'crash', 'hazard'])


def _typical_by_hour(hour, is_weekend, road_class_level):
    """Estimate typical congestion for a given hour (local time).
    
    Args:
        hour: 0..23 (local time, America/Edmonton)
        is_weekend: bool
        road_class_level: 0=local (lower), 1=major (higher)
    
    Returns:
        Congestion level 0..1 (assumption, not measured data).
    """
    # Very rough heuristic:
    # - Weekday peaks: 07-09 (morning), 16-18 (evening)
    # - Midday: moderate
    # - Night: low
    # - Weekend: flatter
    
    if is_weekend:
        # Weekend: low baseline, slight rise midday
        if 10 <= hour < 18:
            return 0.25 + 0.1 * (road_class_level - 0.5)
        else:
            return 0.1 + 0.05 * (road_class_level - 0.5)
    else:
        # Weekday
        if 7 <= hour < 9:  # Morning peak
            return 0.6 + 0.15 * (road_class_level - 0.5)
        elif 16 <= hour < 18:  # Evening peak
            return 0.65 + 0.15 * (road_class_level - 0.5)
        elif 9 <= hour < 16:  # Midday
            return 0.35 + 0.1 * (road_class_level - 0.5)
        else:  # Night
            return 0.1 + 0.05 * (road_class_level - 0.5)


def _fetch_incidents():
    """Fetch current incidents from City of Calgary Socrata API.
    
    Returns: list of dicts with keys:
      - incident_info (str): location/description
      - description (str): detail
      - start_dt_utc (str): ISO8601
      - modified_dt_utc (str): ISO8601
      - quadrant (str): NE, NW, SE, SW, or empty
      - latitude (float)
      - longitude (float)
    """
    try:
        req = urllib.request.Request(SOCRATA_URL)
        req.add_header('User-Agent', 'calgary-traffic-sim/1.0')
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as response:
            data = json.loads(response.read().decode('utf-8'))
            return list(data) if isinstance(data, list) else []
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, Exception) as e:
        return None


def refresh_cache():
    """Fetch fresh incidents if cache expired. Called by server before returning data."""
    global _cache
    
    with _cache_lock:
        now = time.time()
        if now - _cache['fetched_at'] < CACHE_TTL_S:
            # Cache still fresh
            return
        
        # Try to fetch
        incidents = _fetch_incidents()
        _cache['fetched_at'] = now
        
        if incidents is not None:
            _cache['incidents'] = incidents
            _cache['stale'] = False
            _cache['error'] = None
        else:
            # Fetch failed: keep last incidents, set stale flag
            _cache['stale'] = True


def compute_congestion(roads, net_offset):
    """Compute per-edge congestion level.
    
    Args:
        roads: list of dicts with keys id, name, lanes, speed (m/s), shape [[x,y]...]
        net_offset: tuple (x_offset, y_offset) for OSM->SUMO conversion
    
    Returns: dict {
        'updated': ISO8601 timestamp,
        'stale': bool (True if last fetch failed),
        'hour': int (local hour 0..23),
        'isWeekday': bool,
        'mode': str (description of model),
        'incidents': [ {id, type, desc, x, y, lat, lon, started, modified} ],
        'edges': { '<edgeId>': level, ... }  (only level >= 0.05),
        'disclaimer': str
    }
    """
    refresh_cache()
    
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(CALGARY_TZ)
    hour = now_local.hour
    is_weekday = now_local.weekday() < 5  # Mon-Fri
    
    with _cache_lock:
        incidents = list(_cache['incidents'])
        stale = _cache['stale']
    
    # Convert lat/lon to SUMO coords for incidents
    from export_scene import latlon_to_xy
    
    incident_points = []
    for inc in incidents:
        try:
            lat = float(inc.get('latitude', 0))
            lon = float(inc.get('longitude', 0))
            if lat == 0 and lon == 0:
                continue  # Skip invalid coords
            
            x, y = latlon_to_xy(lat, lon, net_offset)
            
            inc_type = inc.get('incident_info', '').split()[0] if inc.get('incident_info') else ''
            incident_points.append({
                'x': x,
                'y': y,
                'lat': lat,
                'lon': lon,
                'type': inc_type,
                'desc': inc.get('description', ''),
                'started': inc.get('start_dt_utc', ''),
                'modified': inc.get('modified_dt_utc', ''),
            })
        except Exception:
            continue
    
    # Compute per-edge level
    edges_dict = {}

    # Measured inputs from the scraper agent (may be absent).
    token_index, volumes = _load_measured_index(roads)
    travel_pressure, corridor_total = _load_travel_pressure(token_index)
    measured_available = bool(travel_pressure or volumes)

    if measured_available:
        mode = ('MEASURED travel times + annual volumes + live incidents'
                if travel_pressure else
                'annual volumes + live incidents')
    else:
        mode = 'typical-by-hour estimate + live incidents (no measured data)'

    for road in roads:
        eid = road['id']
        lanes = road.get('lanes', 1)
        speed_ms = road.get('speed', 13.9)
        shape = road.get('shape', [])

        # Road class: major if high speed or many lanes, else local
        road_class = 1.0 if (speed_ms >= 16 or lanes >= 2) else 0.5

        # The old flat heuristic becomes a FLOOR, not the answer. It used to
        # return ~0.1 for every edge at night, which made the whole city one
        # flat colour and read as "the layer is broken".
        level = _typical_by_hour(hour, not is_weekday, road_class) * 0.45

        # MEASURED signal: live travel-time delay on this corridor.
        if eid in travel_pressure:
            level = max(level, travel_pressure[eid])

        # Historic annual volume gives a stable structural baseline.
        vol = volumes.get(eid, 0)
        if vol > 0:
            vol_level = min(0.62, math.log10(max(vol, 1)) / 4.6 * 0.62)
            level = max(level, vol_level)

        # Incident boost: check distance to all incidents
        for inc in incident_points:
            dist = _nearest_distance_to_edge_shape(inc['x'], inc['y'], shape)

            if dist < 250:  # Within 250m
                # Linear decay: at 0m = full boost, at 250m = 0
                proximity = 1.0 - (dist / 250.0)

                # Boost amount depends on incident type
                is_severe = _is_severe_incident(inc['type'])
                boost = 0.7 if is_severe else 0.5

                incident_contribution = proximity * boost
                level = min(1.0, level + incident_contribution)

        # Only report if level >= 0.05
        if level >= 0.05:
            edges_dict[eid] = round(level, 2)

    return {
        'updated': now_utc.isoformat(),
        'stale': stale,
        'hour': hour,
        'isWeekday': is_weekday,
        'mode': mode,
        'measured': {
            'available': measured_available,
            'corridors_read': corridor_total,
            'edges_with_travel_times': len(travel_pressure),
            'edges_with_volumes': len(volumes),
        },
        'incidents': incident_points,
        'edges': edges_dict,
        'disclaimer': (
            'Road colouring blends MEASURED travel times from calgary.ca '
            '(live, ~3 min refresh) and historic annual volumes from '
            'trafficcounts.calgary.ca with live incident locations. '
            'Only the travel-time and incident layers are current; volumes are '
            'structural (study years 2002-2018). Vehicles remain a SUMO '
            'simulation, not real traffic. See /sources.json for attribution.'
        ) if measured_available else (
            'No measured data found - falling back to a simple hourly model '
            'plus live incidents. Run the scraper agent to enable measured '
            'travel times and traffic volumes.'
        )
    }


def get_sources(net_offset_info):
    """Return list of all data sources.
    
    Args:
        net_offset_info: tuple (net_offset, bounds, roads_count, buildings_count)
    
    Returns: dict {
        'generated': ISO8601,
        'sources': [ {name, provides, url, licence, attribution, status, updated, note} ]
    }
    """
    refresh_cache()
    
    with _cache_lock:
        incidents = _cache['incidents']
        fetch_time = _cache['fetched_at']
    
    now_utc = datetime.now(timezone.utc).isoformat()
    last_fetch = datetime.fromtimestamp(fetch_time, tz=timezone.utc).isoformat() if fetch_time else 'never'

    # Report what the scraper agent actually delivered, if it has run.
    def _agent_file(name):
        p = os.path.join(_AGENT_DATA, name)
        if not os.path.exists(p):
            return None
        return datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc).isoformat()

    tt_at = _agent_file('travel_times.csv')
    tc_at = _agent_file('traffic_counts.csv')

    sources = [
        {
            'name': 'City of Calgary Travel Times (MEASURED)',
            'provides': 'Real measured drive times and delays between major points',
            'url': 'https://www.calgary.ca/roads/conditions/travel-times.html',
            'licence': 'Open Government Licence - City of Calgary',
            'attribution': 'The City of Calgary',
            'status': 'live' if tt_at else 'not-loaded',
            'updated': tt_at or 'run python -m agents.run_pipeline',
            'note': 'Refreshed by the city every ~3 minutes; scraped by the agent. '
                    'This is the only genuinely MEASURED congestion signal used.'
        },
        {
            'name': 'City of Calgary Traffic Counts (annual volumes)',
            'provides': 'Historic annual traffic volumes at 9,230 counting locations',
            'url': 'https://trafficcounts.calgary.ca/',
            'licence': 'Open Government Licence - City of Calgary',
            'attribution': 'The City of Calgary',
            'status': 'static' if tc_at else 'not-loaded',
            'updated': tc_at or 'run python -m agents.run_pipeline',
            'note': 'Study years 2002-2018. Structural demand baseline, NOT live speed.'
        },
        {
            'name': 'City of Calgary Current Traffic Incidents',
            'provides': 'Live incident locations, types, descriptions',
            'url': 'https://data.calgary.ca/resource/35ra-9556.json',
            'licence': 'Open Government Licence - City of Calgary',
            'attribution': 'The City of Calgary',
            'status': 'live',
            'updated': last_fetch,
            'note': f'~5 min cache; last fetch {len(incidents)} incidents'
        },
        {
            'name': '511 Alberta Provincial Alerts',
            'provides': 'Statewide road, weather and closure alerts',
            'url': 'https://511.alberta.ca/Alert/GetUpdatedAlerts?lang=en-US',
            'licence': 'Government of Alberta open data',
            'attribution': 'Government of Alberta',
            'status': 'live',
            'updated': _agent_file('alerts_511.csv') or 'not loaded',
            'note': 'Statewide banner feed; frequently empty overnight.'
        },
        {
            'name': 'OpenStreetMap (Overpass API)',
            'provides': 'Road network, street names, building footprints, parks, water',
            'url': 'https://overpass-api.de/api/interpreter',
            'licence': 'Open Data Commons Open Database License (ODbL)',
            'attribution': '© OpenStreetMap contributors',
            'status': 'static',
            'updated': '2026-10-01',
            'note': 'Snapshot queried via Overpass during export_scene.py'
        },
        {
            'name': 'Building heights (OSM + pseudo-random)',
            'provides': 'Building height estimates for 3D rendering',
            'url': 'https://www.openstreetmap.org/',
            'licence': 'ODbL (OSM), original (pseudo-random fallback)',
            'attribution': '© OpenStreetMap contributors (where tags present)',
            'status': 'modelled',
            'updated': '2026-10-01',
            'note': 'OSM height/levels tags where present; pseudo-random 10-45m otherwise'
        },
        {
            'name': 'SUMO microscopic traffic simulator',
            'provides': 'Vehicle movement, signal timing (defaults)',
            'url': 'https://sumo.dlr.de/',
            'licence': 'Eclipse Public Licence 2.0 (EPL-2.0)',
            'attribution': 'Eclipse SUMO Project',
            'status': 'placeholder',
            'updated': 'v1.27.1',
            'note': 'Vehicle demand from SUMO randomTrips (NOT real counts); signal timings from netconvert actuated defaults (NOT Calgary real plans)'
        },
        {
            'name': 'Typical-by-hour congestion model',
            'provides': 'Baseline congestion estimates by time of day',
            'url': 'local',
            'licence': 'Project-internal assumption',
            'attribution': 'This project',
            'status': 'modelled',
            'updated': now_utc,
            'note': 'Simple heuristic: weekday peaks 07-09, 16-18; higher for major roads; weekend flatter. NOT measured data.'
        },
        {
            'name': 'three.js graphics library',
            'provides': '3D rendering on web client',
            'url': 'https://threejs.org/',
            'licence': 'MIT',
            'attribution': 'three.js contributors',
            'status': 'static',
            'updated': 'r150+',
            'note': 'Client-side 3D rendering'
        },
        {
            'name': 'Calgary Traffic Cameras',
            'provides': 'Live traffic camera feeds (NOT used)',
            'url': 'https://data.calgary.ca/resource/k7p9-kppz.json',
            'licence': 'Open Government Licence - City of Calgary',
            'attribution': 'The City of Calgary',
            'status': 'available-not-used',
            'updated': 'varies',
            'note': 'Dataset k7p9-kppz; live feeds available but not integrated into congestion model'
        },
        {
            'name': 'Alberta 511 Traffic Information',
            'provides': 'Real-time traffic, weather, incident alerts (NOT used)',
            'url': 'https://www.511.ab.ca/',
            'licence': 'Government of Alberta',
            'attribution': 'Government of Alberta',
            'status': 'available-not-used',
            'updated': 'live',
            'note': 'Real-time feed available with API key; not used due to API key requirement'
        },
    ]
    
    return {
        'generated': now_utc,
        'sources': sources
    }
