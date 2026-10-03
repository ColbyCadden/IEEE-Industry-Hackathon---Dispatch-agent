import csv, os, sys, subprocess, json, statistics as st
import xml.etree.ElementTree as ET
from collections import defaultdict

W = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'agent-tests', 'counts_current.csv')
BIN = os.path.join(sys.prefix, 'Scripts')
T0, T1 = '16:00', '18:00'   # PM peak window simulated
TURN = {'s': 0.70, 'l': 0.15, 'r': 0.15}  # PLACEHOLDER turn split
os.chdir(W)

# ---------- demand from CSV (drop the invalid -5 duplicate row) ----------
counts = {}
dropped = []
for x in csv.DictReader(open(CSV)):
    n = int(x['count']); k = (x['interval_start'][11:], x['approach'])
    if n < 0: dropped.append(x); continue
    counts[k] = n
times = sorted({k[0] for k in counts if T0 <= k[0] < T1})
print('dropped invalid rows:', dropped)

# ---------- network (PLACEHOLDER geometry) ----------
open('nodes.nod.xml', 'w').write('''<nodes>
<node id="c" x="0" y="0" type="traffic_light"/>
<node id="N" x="0" y="300"/><node id="S" x="0" y="-300"/>
<node id="E" x="300" y="0"/><node id="W" x="-300" y="0"/></nodes>''')
ed = ''
for a in 'NSEW':
    ed += f'<edge id="{a}in" from="{a}" to="c" numLanes="1" speed="13.89"/>\n<edge id="{a}out" from="c" to="{a}" numLanes="1" speed="13.89"/>\n'
open('edges.edg.xml', 'w').write(f'<edges>{ed}</edges>')
subprocess.run([os.path.join(BIN, 'netconvert.exe'), '-n', 'nodes.nod.xml', '-e', 'edges.edg.xml',
                '-o', 'net.net.xml', '--no-turnarounds', 'true'], check=True, capture_output=True)

# link order -> signal states by approach group
net = ET.parse('net.net.xml').getroot()
links = {}
for c in net.iter('connection'):
    if c.get('tl') == 'c':
        links[int(c.get('linkIndex'))] = (c.get('from')[0], c.get('dir'))
nl = len(links)
def state(green, tag):
    s = ''
    for i in range(nl):
        a, d = links[i]
        if a in green: s += ('g' if d == 'l' else 'G') if tag == 'g' else 'y'
        else: s += 'r'
    return s
def tl(name, gNS, gEW, Y=4, AR=2):
    ph = [(gNS, state('NS', 'g')), (Y, state('NS', 'y')), (AR, 'r' * nl),
          (gEW, state('EW', 'g')), (Y, state('EW', 'y')), (AR, 'r' * nl)]
    body = ''.join(f'<phase duration="{d}" state="{s}"/>' for d, s in ph)
    open(f'tls_{name}.add.xml', 'w').write(
        f'<additional><tlLogic id="c" type="static" programID="{name}" offset="0">{body}</tlLogic></additional>')
# baseline PLACEHOLDER plan: 90 s cycle, equal 36/36 green
tl('baseline', 36, 36)
# alt A: same cycle, split proportional to PM-peak flows (E-W heavier)
sumNS = sum(counts[(t, a)] for t in times for a in 'NS'); sumEW = sum(counts[(t, a)] for t in times for a in 'EW')
usable = 90 - 2 * 6
gEW = round(usable * sumEW / (sumEW + sumNS)); gNS = usable - gEW
print('flow shares NS/EW', sumNS, sumEW, '-> alt splits', gNS, gEW)
tl('split_prop', gNS, gEW)
# alt B: shorter 60 s cycle, equal split
tl('cycle60', 24, 24)

# ---------- routes ----------
opp = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}
left = {'N': 'E', 'E': 'S', 'S': 'W', 'W': 'N'}   # driving south from N turns left to E
right = {v: k for k, v in left.items()}
def sec(t): h, m = map(int, t.split(':')); return (h * 60 + m - int(T0[:2]) * 60) * 60
r = '<routes><vType id="car" accel="2.6" decel="4.5" sigma="0.5" tau="1.0" minGap="2.5" length="5"/>\n'
fid = 0
for t in times:
    for a in 'NSEW':
        n = counts[(t, a)]
        for turn, dest in (('s', opp[a]), ('l', left[a]), ('r', right[a])):
            p = n * TURN[turn] / 900.0
            r += f'<flow id="f{fid}" type="car" begin="{sec(t)}" end="{sec(t)+900}" probability="{p:.5f}" from="{a}in" to="{dest}out" departSpeed="max" departLane="best"/>\n'
            fid += 1
r += '</routes>'
open('routes.rou.xml', 'w').write(r)
END = sec(times[-1]) + 900
for name in ('baseline', 'split_prop', 'cycle60'):
    open(f'{name}.sumocfg', 'w').write(f'''<configuration><input><net-file value="net.net.xml"/><route-files value="routes.rou.xml"/>
<additional-files value="tls_{name}.add.xml"/></input><time><begin value="0"/><end value="{END+900}"/></time>
<processing><time-to-teleport value="-1"/></processing></configuration>''')
json.dump({'END': END, 'nl': nl, 'times': times, 'dropped': len(dropped)}, open('meta.json', 'w'))
print('built; links', nl, 'demand end s', END, 'veh/h total', sum(counts[(t, a)] for t in times for a in 'NSEW') / (len(times) / 4))
