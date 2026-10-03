# Traffic count analysis: Tue 2026-09-22 vs baseline Tue 2026-09-15

Files: counts_current.csv (2026-09-22), counts_baseline.csv (2026-09-15), 15-min counts by approach N/S/E/W, 06:00-19:45.
Scripts: an.py (QC), an2.py (analysis) in the same folder.
Charts: analyst_counts_by_approach.png, analyst_pm_peak_volume.png (same folder).

Headline: the only material change is the EASTBOUND approach in the PM peak (16:00-18:00): 799 veh vs 614 baseline (+30.1%). All other approaches and all off-peak periods are within noise. This is a one-day vs one-day comparison, so it is a signal to investigate, not an established trend.

## Data quality

| Check | Current (09-22) | Baseline (09-15) |
|---|---|---|
| Rows | 225 (224 expected) | 224 |
| Intervals | 56 x 15 min, 06:00-19:45 | same |
| Missing approach-intervals | none | none |
| Non-numeric values | none | none |
| Duplicate keys | 1: 10:15 N appears twice (line 70: 20; line 226, appended at end of file: -5) | none |
| Out-of-range values | -5 (invalid; negative count) | none (min 13, max 88) |

Handling: the appended row (10:15 N = -5) was discarded and the in-sequence value (20, consistent with neighbours 23 and 19) was kept. This is my assumption; N daily total is 1840 with it dropped. If the -5 was actually a corrected value, the effect is under 25 veh on a 1,840 day total and does not change any finding.

Not stated in the files, so unverified: units (assumed vehicles per 15 min, all movements combined per approach), timezone (assumed local, same for both days), lane count, detector type, whether approach = entering direction. Both days are Tuesdays with no weather/incident/event/construction/signal-plan metadata.

Note on the shape of the data: both days show an abrupt step at 16:00 (~20 veh/15 min to 70-110) and an abrupt drop at 18:00, rather than a gradual ramp. Real arterial peaks usually ramp. This may reflect how the data was produced (aggregation, synthetic or test data) and I could not check. Treat peak-timing statements (e.g. exact peak-hour start) with caution.

## Baseline

- Built from a single day: Tue 2026-09-15, same weekday, one week earlier, same season. n = 1 day. No day-to-day variance estimate exists, so the baseline carries unknown error.
- The confidence intervals below only capture variation between 15-min intervals within the day (paired differences, current minus baseline, t-distribution). They do not capture day-to-day variability, which is probably larger. Intervals within a day are also serially correlated, so the CIs are optimistic.
- A one-week gap does not control for school/holiday calendars or weather. Not checked.

## Findings (ranked)

Volume, vehicles (current / baseline / change):

| Period | N | S | E | W | All |
|---|---|---|---|---|---|
| Day 06:00-20:00 | 1840 / 1826 / +0.8% | 1427 / 1457 / -2.1% | 2035 / 1833 / +11.0% | 1844 / 1814 / +1.7% | 7146 / 6930 / +3.1% |
| Off-peak 06:00-16:00 | +0.6% | -1.2% | +1.6% | +1.4% | 4084 / 4055 / +0.7% |
| PM peak 16:00-18:00 | 590 / 587 / +0.5% | 464 / 472 / -1.7% | 799 / 614 / +30.1% | 613 / 602 / +1.8% | 2466 / 2275 / +8.4% |
| Evening 18:00-20:00 | 158 / 154 | 120 / 132 (-9.1%, small n, 12 veh) | 159 / 159 | 159 / 155 | 596 / 600 / -0.7% |

1. Eastbound PM peak surge (strong evidence within these two days). Mean paired difference per 15-min interval in 16:00-18:00: +23.1 veh (95% CI +16.6 to +29.6, n = 8 intervals, sd 7.8). All 8 intervals are higher than baseline (E 105, 99, 105, 95, 103, 87, 110, 95 vs 76, 71, 87, 69, 74, 81, 85, 71). Peak 15 min: 110 vs 87. Peak hour E: 404 veh (16:00-17:00) vs 311 (16:30-17:30 on baseline), +30%. Cause unknown.
2. Everything else is consistent with baseline. PM peak 15-min mean differences for N, S, W have CIs spanning zero (N -5.7 to +6.5, S -6.6 to +4.6, W -6.9 to +9.7). Off-peak paired mean differences are within about +/-1.5 veh/15 min for all approaches (n = 40 each; CIs include zero).
3. Directional split shifted toward E-W in the PM peak. E-W share of 4-approach volume rose from 53.5% to 57.3% (EW 1412 vs 1216; NS 1054 vs 1059). E's share of E-W rose from 50.5% to 56.6%. This is a restatement of finding 1, not an independent result.
4. Peak-hour factor (4 x 15-min, 16:00-19:45 window): intersection 0.959 current vs 0.977 baseline; E 0.918 vs 0.894. Both high (flat peak), which is consistent with the step-shaped profile noted above. Single-day values.
5. Evening S -9.1% is 12 vehicles over two hours; not distinguishable from noise.

Chart: analyst_counts_by_approach.png (15-min series by approach), analyst_pm_peak_volume.png (PM peak bars).

## Bottlenecks

Ranked by evidence strength. Volume-only data cannot show a bottleneck directly: there are no signal timings, lane counts, saturation flows, queue lengths, headways or arrival-on-green data, so v/c, platoon dispersion and queue growth per cycle were NOT computed.

| Rank | Approach / time | Evidence | Strength |
|---|---|---|---|
| 1 | Eastbound, 16:00-18:00 | Highest-volume approach in both days' PM peak; +30% vs baseline; 404 veh/h peak hour, 440 veh/h peak 15-min rate; E carries 32% of the 4-approach PM volume vs 27% baseline | Demand evidence only. Whether it is a capacity problem depends on E green split and lanes, which I do not have |
| 2 | Westbound, 16:00-18:00 | 613 veh, second highest, flat vs baseline | Weak; listed for coordination (E-W pair shares phase) |
| 3 | North, 16:00-18:00 | 590 veh, flat vs baseline | Weak |

South is the lowest-demand approach all day (PM peak 464).

## Candidate timing changes (hypotheses only)

Not validated; none should be treated as safe or optimal. Engineering sign-off rests with the city. Everything below is judgment unless a source is noted.

1. Shift green split toward the E-W phase during 16:00-18:00.
   - Hypothesis: E demand is up ~30% while N-S is flat, so moving a few seconds of green from N-S to E-W reduces E delay/queue with a small N-S penalty.
   - Rationale: E-W share of volume rose from 53.5% to 57.3%; splits are normally set in proportion to critical-lane flow ratios (FHWA Signal Timing Manual, see Sources).
   - Risk: N-S carries 1054 veh in the peak and any green cut raises N-S delay; minimum green/pedestrian clearance limits; the E increase may be a one-day anomaly.
   - How to test: build the intersection in a simulator, with these counts as inputs, compare E delay/queue and N-S delay for splits of current vs +3/+5 s to E-W; field trial only after a second week confirms the increase.
2. Review the cycle length for the peak period.
   - Hypothesis: if E queues clear poorly at present, a modestly longer cycle could reduce lost-time share; if they clear fine, no change.
   - Risk: longer cycles raise delay for all minor movements and pedestrians.
   - How to test: simulation; needs the current cycle and split, which I do not have.
3. Check progression offset for eastbound platoons (if the intersection is in a coordinated corridor).
   - Hypothesis: with higher E volume, arrival-on-green share matters more.
   - Test: needs timestamped upstream arrivals or travel time; not available. Do not change offsets from this data alone.
4. Data action before any change: collect at least 3-4 more Tuesdays (and other weekdays) to build a baseline with a real variance, and find out what happened on 09-22 (event, detour, incident, school/work schedule, detector fault).

Expected directions of effect are directional expectations only, not quantified predictions.

## Sources

- Input files: counts_current.csv and counts_baseline.csv (analysed this session; computation in an.py, an2.py).
- FHWA Signal Timing Manual (split/cycle/offset principles): https://ops.fhwa.dot.gov/publications/fhwahop08024/ (cited from knowledge, page not re-fetched this session).
- Highway Capacity Manual, TRB (signalised intersection methodology, v/c, saturation flow, PHF): https://www.trb.org/Main/Blurbs/175169.aspx (cited from knowledge, not re-fetched).
- Statistical method: paired differences of matched 15-min intervals, t critical 2.365 (df 7) for n = 8 and approx 2.0 for n = 40.

## Could not verify

- Cause of the eastbound increase (demand shift, event, diversion, detector error).
- Whether the baseline day was typical; only one week exists.
- Count units, timezone, lane assignment, whether counts include all movements.
- Meaning of the -5 duplicate row at 10:15 N (discarded).
- Current signal plan, cycle, splits, offsets, lane geometry, saturation flows: so no v/c, delay, queue or platoon results.
- Whether the sharp 16:00/18:00 step is real or an artefact of how the data was produced.
- The two HCM/FHWA URLs were not opened in this session.
