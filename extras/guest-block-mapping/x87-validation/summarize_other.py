#!/usr/bin/env python3
"""Report per-quartet B/A elapsed-time contrasts without discarding outliers."""
import argparse,csv,json,math,statistics
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('csv',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args();rows=list(csv.DictReader(a.csv.open()));out={}
for workload in ('x87bench','doom'):
 out[workload]={}
 for phase in ('AA','AB'):
  r=[x for x in rows if x['workload']==workload and x['phase']==phase];diffs=[]
  if not r or len(r)%4:raise ValueError('incomplete quartets')
  for i in range(0,len(r),4):
   q=r[i:i+4]
   if any(x['status']!='ok' for x in q) or [x['label'] for x in q].count('A')!=2 or [x['label'] for x in q].count('B')!=2:raise ValueError('invalid quartet')
   ma=statistics.mean(math.log(float(x['wall_s'])) for x in q if x['label']=='A');mb=statistics.mean(math.log(float(x['wall_s'])) for x in q if x['label']=='B');diffs.append(100*math.expm1(mb-ma))
  out[workload][phase]={'quartet_B_over_A_wall_pct':diffs,'geometric_paired_B_over_A_wall_pct':100*math.expm1(statistics.mean(math.log1p(x/100) for x in diffs))}
a.out.write_text(json.dumps(out,indent=2)+'\n')
