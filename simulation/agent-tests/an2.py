import csv, statistics as st, math, json
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
D='./'
def load(f, drop_bad=False):
    d={}
    for i,r in enumerate(csv.DictReader(open(D+f,newline=''))):
        k=(r['interval_start'][11:],r['approach']); c=int(r['count'])
        if k in d:
            continue  # keep first occurrence; later duplicate (-5) discarded
        d[k]=c
    return d
cur=load('counts_current.csv'); base=load('counts_baseline.csv')
ts=sorted({k[0] for k in base}); A='NSEW'
def s(d,a,lo,hi): return sum(d[(t,a)] for t in ts if lo<=t<hi)
out={}
def tot(d,lo,hi,aps=A): return sum(s(d,a,lo,hi) for a in aps)
res={}
for name,(lo,hi) in {'Day 06:00-20:00':('06:00','20:00'),'Off-peak 06:00-16:00':('06:00','16:00'),'PM peak 16:00-18:00':('16:00','18:00'),'Evening 18:00-20:00':('18:00','20:00')}.items():
    for a in list(A)+['ALL']:
        aps=A if a=='ALL' else a
        c=tot(cur,lo,hi,aps); b=tot(base,lo,hi,aps)
        res[(name,a)]=(c,b,100*(c-b)/b)
print('VOLUMES')
for k,v in res.items(): print(k,v[0],v[1],round(v[2],1))
# peak hour and PHF per approach and total
def peak(d,aps):
    best=None
    for i in range(len(ts)-3):
        v=sum(d[(t,a)] for t in ts[i:i+4] for a in aps)
        if best is None or v>best[0]: best=(v,ts[i])
    q=max(sum(d[(t,a)] for a in aps) for t in ts)
    return best,q
print('PEAK')
for lab,aps in [('ALL',A)]+[(a,a) for a in A]:
    for nm,d in [('cur',cur),('base',base)]:
        (v,t),q=peak(d,aps); print(lab,nm,'peakhr start',t,'vol',v,'PHF',round(v/(4*q),3) if True else 0)
# 15-min diffs in PM peak per approach: paired
print('PAIRED PM peak')
for a in A:
    diffs=[cur[(t,a)]-base[(t,a)] for t in ts if '16:00'<=t<'18:00']
    m=st.mean(diffs); sd=st.stdev(diffs); n=len(diffs); se=sd/math.sqrt(n)
    # t crit df7 =2.365
    print(a,'mean diff',round(m,1),'sd',round(sd,1),'n',n,'95%CI',round(m-2.365*se,1),round(m+2.365*se,1))
# off-peak paired
print('PAIRED off-peak')
for a in A:
    diffs=[cur[(t,a)]-base[(t,a)] for t in ts if t<'16:00']
    n=len(diffs);m=st.mean(diffs);sd=st.stdev(diffs);se=sd/math.sqrt(n)
    print(a,'mean diff',round(m,2),'sd',round(sd,2),'n',n,'95%CI',round(m-2.0*se,2),round(m+2.0*se,2))
# directional split PM peak
for nm,d in [('cur',cur),('base',base)]:
    ns=tot(d,'16:00','18:00','NS'); ew=tot(d,'16:00','18:00','EW'); print(nm,'NS',ns,'EW',ew,'EW share',round(ew/(ns+ew),3))
    print(nm,'E share of EW',round(tot(d,'16:00','18:00','E')/ew,3), 'N share of NS',round(tot(d,'16:00','18:00','N')/ns,3))
# onset
for nm,d in [('cur',cur),('base',base)]:
    print(nm,'E 15:45/16:00',d[('15:45','E')],d[('16:00','E')],'ALL 15:45',sum(d[('15:45',a)] for a in A),'16:00',sum(d[('16:00',a)] for a in A))
print('E 17:45..19:45')
for t in ['17:45','18:00','18:15','18:30','19:00','19:45']:
    print(t,[ (cur[(t,a)],base[(t,a)]) for a in A])
# peak 15-min E each
print('E peak 15min cur',max(cur[(t,'E')] for t in ts),'base',max(base[(t,'E')] for t in ts))
# plots
fig,ax=plt.subplots(2,2,figsize=(12,7),sharex=True)
for a,x in zip(A,ax.flat):
    x.plot(ts,[base[(t,a)] for t in ts],label='Baseline 09-15',color='gray')
    x.plot(ts,[cur[(t,a)] for t in ts],label='Current 09-22',color='tab:red')
    x.set_title(a); x.set_xticks(range(0,len(ts),8)); x.set_xticklabels(ts[::8]); x.set_ylabel('veh/15min'); x.legend()
fig.suptitle('15-min counts by approach: current vs baseline'); fig.tight_layout(); fig.savefig(D+'analyst_counts_by_approach.png',dpi=110)
fig,x=plt.subplots(figsize=(7,4))
lo,hi='16:00','18:00'
b=[tot(base,lo,hi,a) for a in A]; c=[tot(cur,lo,hi,a) for a in A]
import numpy as np
i=np.arange(4); x.bar(i-.2,b,.4,label='Baseline',color='gray'); x.bar(i+.2,c,.4,label='Current',color='tab:red')
x.set_xticks(i); x.set_xticklabels(A); x.set_ylabel('veh in 16:00-18:00'); x.set_title('PM peak (2 h) volume by approach'); x.legend(); fig.tight_layout(); fig.savefig(D+'analyst_pm_peak_volume.png',dpi=110)
