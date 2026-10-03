#!/usr/bin/env python3
"""Export Calgary downtown scene data from SUMO network + OSM polygons.

Reads dt.net.xml and poly.osm, writes scene.json with roads, buildings,
parks, water, and signal locations in SUMO-meter coordinates.
Uses ONLY stdlib + sumolib (no extra packages).
"""

import json
import os
import hashlib
import math
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

import sumolib


def simplify_ring(points, max_points=40):
    """Simplify a polygon ring to <= max_points using Douglas-Peucker.

    Keeps the first and last point; recursively subdivides until
    point budget is exhausted or max error is ~0.
    """
    if len(points) <= max_points:
        return points

    # Compute distances from each point to the line from first to last
    x1, y1 = points[0]
    x2, y2 = points[-1]
    dx = x2 - x1
    dy = y2 - y1
    line_len_sq = dx * dx + dy * dy

    if line_len_sq < 1e-9:
        # Degenerate: just subsample
        step = max(1, (len(points) - 1) // (max_points - 1))
        return [points[i] for i in range(0, len(points), step)][:max_points]

    max_dist = 0
    max_idx = 0
    inv_len = 1.0 / math.sqrt(line_len_sq)
    for i in range(1, len(points) - 1):
        px, py = points[i]
        # Perpendicular distance to line segment (first->last)
        cross = abs(dx * (y1 - py) - (x1 - px) * dy)
        dist = cross * inv_len
        if dist > max_dist:
            max_dist = dist
            max_idx = i

    # Allocate budget proportionally
    left_budget = max(2, int(max_points * max_idx / len(points)))
    right_budget = max(2, max_points - left_budget)

    left = simplify_ring(points[:max_idx + 1], left_budget)
    right = simplify_ring(points[max_idx:], right_budget)

    return left[:-1] + right


def osm_node_map(osm_root):
    """Build {node_id: (lat, lon)} map from OSM nodes."""
    nodes = {}
    for node in osm_root.findall('node'):
        nid = node.get('id')
        lat = float(node.get('lat'))
        lon = float(node.get('lon'))
        nodes[nid] = (lat, lon)
    return nodes


_NET = None


def latlon_to_xy(lat, lon, net_offset):
    """Convert OSM lat/lon to SUMO meter coordinates.

    net_offset is (x_offset, y_offset) from net.getLocationOffset().
    SUMO UTM projection: netOffset = UTM_x - net_x, so
    net_x = UTM_x - netOffset_x.
    """
    # Use SUMO's own projection (hand-rolled UTM used the wrong zone)
    x_utm, y_utm = latlon_to_utm(lat, lon)
    # SUMO: net = utm + netOffset (netOffset is negative here)
    return x_utm + net_offset[0], y_utm + net_offset[1]


def latlon_to_utm(lat, lon):
    """Convert WGS84 lat/lon to UTM zone 11N (EPSG:32611) meters.

    Simplified spherical model sufficient for small areas (~2km).
    """
    # UTM zone 11N central meridian = -117 deg
    a = 6378137.0
    f = 1 / 298.257223563
    k0 = 0.9996
    lon0 = -117.0  # central meridian for zone 11

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)

    e2 = 2 * f - f * f
    e4 = e2 * e2
    e6 = e4 * e2

    nu = a / math.sqrt(1 - e2 * math.sin(lat_rad) ** 2)
    p = lon_rad - math.radians(lon0)

    s = a * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat_rad)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat_rad)
        - (35 * e6 / 3072) * math.sin(6 * lat_rad)
    )

    x = k0 * nu * (
        p * math.cos(lat_rad)
        + (p ** 3 / 6) * math.cos(lat_rad) ** 3 * (1 - math.tan(lat_rad) ** 2 + e2 * math.cos(lat_rad) ** 2)
        + (p ** 5 / 120) * math.cos(lat_rad) ** 5
        * (5 - 18 * math.tan(lat_rad) ** 2 + math.tan(lat_rad) ** 4 + 14 * e2 * math.cos(lat_rad) ** 2 - 58 * e2 * math.cos(lat_rad) ** 2 * math.tan(lat_rad) ** 2)
    ) + 500000.0

    y = k0 * (
        s
        + nu * math.tan(lat_rad)
        * (
            p ** 2 / 2 * math.cos(lat_rad) ** 2
            + p ** 4 / 24 * math.cos(lat_rad) ** 4 * (5 - math.tan(lat_rad) ** 2 + 9 * e2 * math.cos(lat_rad) ** 2 + 4 * e2 ** 2 * math.cos(lat_rad) ** 4)
            + p ** 6 / 720 * math.cos(lat_rad) ** 6
            * (61 - 58 * math.tan(lat_rad) ** 2 + math.tan(lat_rad) ** 4 + 270 * e2 * math.cos(lat_rad) ** 2 - 330 * e2 * math.cos(lat_rad) ** 2 * math.tan(lat_rad) ** 2)
        )
    )
    if lat < 0:
        y += 10000000.0

    return x, y


def building_height(tags, way_id, center_x, center_y, bounds):
    """Compute building height in meters.

    Priority: OSM height tag > building:levels * 3.2 > hash-based 10-45.
    Hash values cluster taller near downtown center.
    """
    if 'height' in tags:
        try:
            h = float(tags['height'])
            if h > 0:
                return h
        except (ValueError, TypeError):
            pass

    if 'building:levels' in tags:
        try:
            levels = int(float(tags['building:levels']))
            if levels > 0:
                return levels * 3.2
        except (ValueError, TypeError):
            pass

    # Deterministic pseudo-random based on way id hash
    h = hashlib.md5(way_id.encode()).digest()
    base = (h[0] * 256 + h[1]) / 65535.0  # 0..1

    # Distance to center: closer = taller
    if bounds:
        cx = (bounds[0] + bounds[2]) / 2
        cy = (bounds[1] + bounds[3]) / 2
        max_dist = math.sqrt((bounds[2] - bounds[0]) ** 2 + (bounds[3] - bounds[1]) ** 2) / 2
        dist = math.sqrt((center_x - cx) ** 2 + (center_y - cy) ** 2)
        proximity = 1.0 - min(dist / max_dist, 1.0)  # 0 (far) to 1 (center)
    else:
        proximity = 0.5

    # Mix: proximity-weighted plus random variation
    h = 10 + proximity * 25 + base * 10
    return round(h, 1)


def compute_way_ring(way_elem, node_map, net_offset):
    """Convert OSM way node refs to SUMO XY coords, returning a list of [x,y]."""
    coords = []
    for nd in way_elem.findall('nd'):
        nid = nd.get('ref')
        if nid in node_map:
            lat, lon = node_map[nid]
            x, y = latlon_to_xy(lat, lon, net_offset)
            coords.append((x, y))
    return coords


def main():
    calgary_dir = os.environ.get(
        'CALGARY_DIR',
        os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            os.pardir, os.pardir, 'calgary')))
    output_path = os.environ.get('SCENE_OUT', os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        os.pardir, 'web', 'scene.json')))

    # --- Read SUMO network ---
    net = sumolib.net.readNet(f"{calgary_dir}/dt.net.xml")
    net_offset = net.getLocationOffset()  # (x, y)
    global _NET
    _NET = net

    # Bounds from net
    bounds_list = net.getBoundary()  # (xmin, ymin, xmax, ymax)

    # --- Load OSM way names (net.xml edges have no name attr) ---
    osm_way_names = {}
    try:
        osm_tree_roads = ET.parse(f"{calgary_dir}/dt.osm")
        for way in osm_tree_roads.getroot().findall('way'):
            for tag in way.findall('tag'):
                if tag.get('k') == 'name':
                    osm_way_names[way.get('id')] = tag.get('v')
                    break
    except Exception:
        pass  # fall back to empty names if OSM not available
    
    # --- Roads ---
    roads = []
    seen_edges = set()
    named_count = 0
    for edge in net.getEdges(withInternal=False):
        eid = edge.getID()
    
        # Name: net edge attr first, else OSM way tag by parsing way ID from edge ID
        name = edge.getName()
        if not name or name == eid:
            # Parse OSM way ID: strip '-', then take digits before '#'
            clean = eid.lstrip('-')
            wid = clean.split('#')[0]
            name = osm_way_names.get(wid, '')
        if name:
            named_count += 1
    
        lanes = edge.getLaneNumber()
        speed = edge.getSpeed()  # m/s
        width = sum(lane.getWidth() for lane in edge.getLanes()) / max(lanes, 1)
        shape = edge.getShape()  # list of (x, y)
        shape_rounded = [[round(p[0], 1), round(p[1], 1)] for p in shape]
    
        roads.append({
            "id": eid,
            "name": name,
            "lanes": lanes,
            "width": round(width, 1),
            "speed": round(speed, 1),
            "shape": shape_rounded
        })
        seen_edges.add(eid)

    # --- Signals ---
    signals = []
    for tls in net.getTrafficLights():
        tls_id = tls.getID()
        # Position from first edge's "to" junction
        edges = list(tls.getEdges())
        if edges:
            pos = edges[0].getToNode().getCoord()
            x, y = pos[0], pos[1]
        else:
            x, y = 0, 0
        signals.append({
            "id": tls_id,
            "x": round(x, 1),
            "y": round(y, 1)
        })

    # --- Read poly.osm ---
    osm_tree = ET.parse(f"{calgary_dir}/poly.osm")
    osm_root = osm_tree.getroot()
    node_map = osm_node_map(osm_root)

    buildings = []
    parks = []
    water = []

    bounds = bounds_list

    for way in osm_root.findall('way'):
        tags = {}
        for tag in way.findall('tag'):
            tags[tag.get('k')] = tag.get('v')

        way_id = way.get('id')
        is_building = 'building' in tags
        is_park = tags.get('leisure') == 'park'
        is_water = (
            ('natural' in tags and tags['natural'] in ('water', 'bay', 'strait'))
            or tags.get('waterway')
            or tags.get('landuse') == 'reservoir'
        )

        if not (is_building or is_park or is_water):
            continue

        ring = compute_way_ring(way, node_map, net_offset)
        if len(ring) < 3:
            continue

        # Compute center for height bias
        if ring:
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            wcx = sum(xs) / len(xs)
            wcy = sum(ys) / len(ys)
        else:
            wcx, wcy = 0, 0

        if is_building:
            h = building_height(tags, way_id, wcx, wcy, bounds)
            ring_simple = simplify_ring(ring)
            ring_rounded = [[round(p[0], 1), round(p[1], 1)] for p in ring_simple]
            buildings.append({
                "h": h,
                "ring": ring_rounded
            })
        elif is_park:
            ring_simple = simplify_ring(ring)
            ring_rounded = [[round(p[0], 1), round(p[1], 1)] for p in ring_simple]
            parks.append(ring_rounded)
        elif is_water:
            ring_simple = simplify_ring(ring)
            ring_rounded = [[round(p[0], 1), round(p[1], 1)] for p in ring_simple]
            water.append(ring_rounded)

    # --- Assemble scene ---
    scene = {
        "bounds": [round(b, 1) for b in bounds],
        "roads": roads,
        "buildings": buildings,
        "parks": parks,
        "water": water,
        "signals": signals
    }

    with open(output_path, 'w') as f:
        json.dump(scene, f)

    print(f"Wrote {output_path}")
    print(f"  bounds: {scene['bounds']}")
    print(f"  roads: {len(roads)} ({named_count} named, {len(roads)-named_count} unnamed)")
    print(f"  buildings: {len(buildings)}")
    print(f"  parks: {len(parks)}")
    print(f"  water: {len(water)}")
    print(f"  signals: {len(signals)}")
    print(f"  file size: {len(json.dumps(scene))} bytes")
    
    # Sanity check bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]
    print(f"  width: {bw:.0f}m, height: {bh:.0f}m")


if __name__ == '__main__':
    main()