# SUMO intersection simulation - UNCALIBRATED / PLACEHOLDER GEOMETRY

All results below use invented geometry and signal timing (none was supplied). They show direction of effect only.

## Inputs
- Counts: counts_current.csv (15-min counts, N/S/E/W, 2026-09-22 06:00-19:45).
- Simulated window: PM peak 16:00-18:00 (highest demand, about 1233 veh/h total), plus a drain period until the network empties.
- Data fix: row 2026-09-22 10:15 N had a duplicate with count -5 (invalid). It was dropped and the other row (20) kept. This is outside the simulated window.

## Placeholders (NOT measured)
- Geometry: 4-leg, 1 lane per approach, 300 m approaches, 50 km/h, no turn lanes, no pedestrians.
- Turn split: 70% through / 15% left / 15% right on every approach. The counts have no movement data.
- Baseline signal: 2 phases, 36 s green each, 4 s yellow, 2 s all-red; 90 s cycle. Left turns are permissive.
- Driver model: tau 1.0, minGap 2.5, accel 2.6, decel 4.5, sigma 0.5 (SUMO-like defaults, not tuned).
- Arrivals are random (Bernoulli per second) around each 15-min count, so seeds differ.

## Results (5 seeds each: 1-5; mean, SD in results.csv)
scenario    | seeds | mean_delay_s | max_queue_veh | throughput_veh_h | vs_baseline_%
baseline    | 5 | 58.0 (SD 10.3) | 31.2 (SD 3.6) | 1251 (SD 20.4) |   0.0
split_prop  | 5 | 40.6 (SD 3.5)  | 26.4 (SD 7.1) | 1251 (SD 20.4) | -30.0
cycle60     | 5 | 55.7 (SD 13.4) | 28.0 (SD 3.8) | 1251 (SD 20.4) |  -4.0

- vs_baseline_% is the change in mean delay (negative = less delay).
- split_prop: same 90 s cycle, green split 33 s N-S / 45 s E-W, proportional to the PM-peak flow ratio.
- cycle60: 60 s cycle, 24 s green each.
- Delay = SUMO timeLoss per completed vehicle. Max queue = peak halted vehicles on any single approach lane (speed < 0.1 m/s).
- Throughput is identical across scenarios because every vehicle cleared (0 left in the network) and the demand was the same. It measures demand served, not capacity. Only delay and queue separate the scenarios.
- Only split_prop's gain (-30%) is large relative to seed spread. cycle60 (-4%) is within noise (baseline SD 10 s, cycle60 SD 13 s).

## Limitations
- No calibration: no measured delay, queue or headway data were available, so no error per metric can be given (target would be ~10-15%). Parameters are defaults.
- No validation: counts_baseline.csv exists but was not used. It is a different condition, not a held-out check of this model.
- Geometry, turn split and signal plan are all invented. Absolute numbers (delay, queue) are not meaningful for a real site. Only the ranking of scenarios under these assumptions is informative.
- The split_prop and cycle60 variants are my own illustrative choices, not the user's plans.
- Camera counts may undercount, and queues from them would be underestimated. Not checked.
- Simulation shows direction of effect, not safety or regulatory approval.
- Only the PM peak was run. AM peak and off-peak were not simulated.

## Files (absolute paths, sim/)
net.net.xml, nodes.nod.xml, edges.edg.xml, routes.rou.xml, tls_baseline.add.xml, tls_split_prop.add.xml, tls_cycle60.add.xml,
baseline.sumocfg, split_prop.sumocfg, cycle60.sumocfg, results.csv, results_by_seed.csv, report.md, build.py, run.py
