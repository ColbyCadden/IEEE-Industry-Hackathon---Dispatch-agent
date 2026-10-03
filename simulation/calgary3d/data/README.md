# Calgary Traffic Data Sources - Verification Report

## Summary

**Verified**: 5 real traffic data sources for Calgary, Alberta (HTTP tested, actual data sampled)

**Key Finding**: **NO FREE real-time per-road-segment speed/congestion feed exists**

## Quick Reference

| Source | Endpoint | Format | Update | Cost | Best For |
|--------|----------|--------|--------|------|----------|
| Calgary Incidents | `data.calgary.ca/api/views/35ra-9556` | Socrata JSON | 10 min | Free | Historical incidents, lane closures |
| Calgary Current | `data.calgary.ca/api/views/4jah-h97u` | Socrata JSON | 10 min | Free | Real-time incident monitoring |
| Traffic Cameras | `data.calgary.ca/api/views/k7p9-kppz` | Socrata JSON | Live | Free | Visual assessment (manual) |
| Alberta 511 API | `511.alberta.ca/api/v2/get/event` | REST JSON/XML | Real-time | Free (reg.) | Multi-source incident feed |
| OpenStreetMap | `overpass-api.de/api/interpreter` | OSM JSON/XML | ~1 day | Free | Road network basemap |

## Files in This Directory

1. **sources_verified.json** - Machine-readable JSON with all source details, fields, metadata
2. **SOURCES_REPORT.txt** - Detailed text report with HTTP responses and sample data
3. **README.md** - This file

## Incident Data Available (Free)

### Calgary Incidents (Socrata)
- **Endpoint**: `https://data.calgary.ca/api/views/35ra-9556/rows.json`
- **Resource ID**: 35ra-9556
- **HTTP Status**: 200 OK
- **Update Frequency**: Every 10 minutes
- **Fields**: incident location, type, description, timestamps, lane impact, geometry (WKT POINT)
- **Records**: 64,800+ historical incidents
- **Licence**: Open Government Licence - City of Calgary v2.1
- **Attribution**: "The City of Calgary"

### Alberta 511 API
- **Endpoint**: `https://511.alberta.ca/api/v2/get/event?key={API_KEY}`
- **API Key**: Free (register at https://511.alberta.ca/my511/register)
- **Update Frequency**: Real-time
- **Fields**: RoadwayName, Description, Severity, EventType, Latitude/Longitude, EncodedPolyline
- **Coverage**: All of Alberta (includes Calgary + surrounding areas)
- **Licence**: Government of Alberta
- **Attribution**: "Alberta Transportation (511 Alberta)"

## NO Speed/Volume Data Available (Free)

- Google Maps API, TomTom, HERE - All require paid API keys
- Calgary does not publish traffic speed or volume metrics
- Waze data requires partner agreement

## How to Use

### Get Historical Incidents
```bash
curl "https://data.calgary.ca/api/views/35ra-9556/rows.json?limit=100&offset=0"
```

### Get Current Incidents
```bash
curl "https://data.calgary.ca/api/views/4jah-h97u/rows.json?limit=100"
```

### Get 511 Alberta Events (requires API key)
```bash
curl "https://511.alberta.ca/api/v2/get/event?key={YOUR_KEY}&format=json"
```

## Licence & Attribution

**Required Attribution**:
- "The City of Calgary" (for Calgary data)
- "Alberta Transportation (511 Alberta)" (for Alberta 511 data)
- "© OpenStreetMap contributors" (for OSM data)

**Licence Terms**:
- Open Government Licence - City of Calgary v2.1: Allows commercial use, modification, redistribution (with attribution)
- Details: https://data.calgary.ca/stories/s/Open-Calgary-Terms-of-Use/u45n-7awa/

## Next Steps

1. **For incident monitoring**: Use Calgary + Alberta 511 APIs
2. **For speed data**: Evaluate paid APIs (Google, TomTom, HERE)
3. **For visual assessment**: Use camera locations + live feeds
4. **For roadmap**: Use OpenStreetMap / Overpass

---
Report Generated: 2026-10-02 | All HTTP responses verified via curl
