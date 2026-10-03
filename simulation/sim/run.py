import os, sys, json, csv, statistics as st
import xml.etree.ElementTree as ET
import traci
W = os.path.dirname(os.path.abspath(__file__))
os.chdir(W)
meta = json.load(open('meta.json')); END = meta['END']
SUMO = os.path.join(sys.prefix, 'Scripts', 'sumo.exe')
rows = []
for name in ('baseline', 'split_prop', 'cycle60'):
    for seed in range(1, 6):
        trip = f'trip_{name}_{seed}.xml'
        traci.start([SUMO, '-c', f'{name}.sumocfg', '--seed', str(seed), '--tripinfo-output', trip,
                     '--no-step-log', 'true', '--no-warnings', 'true'], label=f'{name}{seed}')
        mq = 0
        while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < END + 900:
            traci.simulationStep()
            q = max(traci.lane.getLastStepHaltingNumber(f'{a}in_0') for a in 'NSEW')
            mq = max(mq, q)
        left = traci.vehicle.getIDCount()
        traci.close()
        ti = [t for t in ET.parse(trip).getroot().iter('tripinfo')]
        tl = [float(t.get('timeLoss')) for t in ti]
        arr = len(ti)
        hours = (END) / 3600
        rows.append(dict(scenario=name, seed=seed, vehicles_completed=arr, still_in_net=left,
                         mean_delay_s=st.mean(tl), max_queue_veh=mq, throughput_veh_h=arr / hours))
with open('results_by_seed.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, rows[0].keys()); w.writeheader(); w.writerows(rows)
base = None
out = []
for name in ('baseline', 'split_prop', 'cycle60'):
    rs = [r for r in rows if r['scenario'] == name]
    m = lambda k: st.mean(r[k] for r in rs)
    sd = lambda k: st.stdev(r[k] for r in rs)
    d = m('mean_delay_s')
    if base is None: base = d
    out.append(dict(scenario=name, seeds=len(rs), mean_delay_s=round(d, 1), delay_sd=round(sd('mean_delay_s'), 1),
                    max_queue_veh=round(m('max_queue_veh'), 1), queue_sd=round(sd('max_queue_veh'), 1),
                    throughput_veh_h=round(m('throughput_veh_h')), tp_sd=round(sd('throughput_veh_h'), 1),
                    still_in_net=round(m('still_in_net'), 1), vs_baseline_pct=round((d - base) / base * 100, 1)))
with open('results.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, out[0].keys()); w.writeheader(); w.writerows(out)
for o in out: print(o)
