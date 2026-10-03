import csv, collections, statistics as st
def load(f):
    rows=list(csv.DictReader(open(f,newline='')))
    return rows
for f in ['counts_current.csv','counts_baseline.csv']:
    r=load(f); print(f,len(r))
    keys=collections.Counter((x['interval_start'],x['approach']) for x in r)
    print(' dups',[k for k,v in keys.items() if v>1])
    ts=sorted(set(x['interval_start'] for x in r)); print(' intervals',len(ts),ts[0],ts[-1])
    print(' approaches',collections.Counter(x['approach'] for x in r))
    bad=[x for x in r if not x['count'].strip().lstrip('-').isdigit()]; print(' nonnumeric',bad)
    # missing
    for a in 'NSEW':
        have={x['interval_start'] for x in r if x['approach']==a}
        print(' missing',a,[t for t in ts if t not in have])
    vals=[int(x['count']) for x in r if x['count'].strip().lstrip('-').isdigit()]
    print(' min/max',min(vals),max(vals))
    for a in 'NSEW':
        v=sorted((x['interval_start'],int(x['count'])) for x in r if x['approach']==a and x['count'].strip().lstrip('-').isdigit())
        m=st.median(c for _,c in v)
        print(' ',a,'total',sum(c for _,c in v),'median',m,'top',sorted(v,key=lambda z:-z[1])[:3])
