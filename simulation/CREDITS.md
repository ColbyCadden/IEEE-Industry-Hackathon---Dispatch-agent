# Credits and Acknowledgements

## Author

**Semir Haji** — [@semirhaji07](https://github.com/semirhaji07) — semirhaji07@gmail.com

Parts of this project were developed with AI coding assistance. Where AI-authored
documentation introduced factual errors, it was corrected or deleted — see
`HISTORY.md` for specifics.

## Upstream projects

This project stands on the work of several open-source projects. It is grateful
for all of them.

| Project | Role | Licence |
|---|---|---|
| [Eclipse SUMO](https://sumo.dlr.de/) | Traffic simulation engine | EPL-2.0 |
| [TraCI](https://sumo.dlr.de/docs/TraCI/) | Python control interface for SUMO | EPL-2.0 |
| [three.js](https://threejs.org/) | 3D rendering in the browser | MIT |
| [OpenStreetMap](https://www.openstreetmap.org/) | Road network, street names, building footprints, parks, water | ODbL 1.0 |
| [City of Calgary Open Data](https://data.calgary.ca/) | Live traffic incident feed | Open Government Licence — City of Calgary |
| [Overpass API](https://overpass-api.de/) | OSM data extraction | — |

## Data attribution

**© OpenStreetMap contributors**, licensed under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).

This repository **redistributes ODbL-licensed data**: the road network, street
names, building footprints, parks and water polygons, via an Overpass snapshot
taken **1 October 2026**.

**The City of Calgary** — Current Traffic Incidents dataset (`4jah-h97u`),
licensed under the City's Open Government Licence.

See the Data Provenance section in `README.md` for what is live data and what is
simulated or modelled.

## A note on accuracy

Traffic demand in this simulation is randomly generated and the signal timings
are simulator defaults, **not** measurements of real Calgary traffic. The
per-street congestion colouring is a modelled estimate, not measured speed data.
This is stated plainly in the README and labelled in the application interface,
and it should stay that way in any derived work.