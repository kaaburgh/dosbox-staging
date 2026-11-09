import argparse, bisect, collections, json, os, struct, subprocess
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--binary', required=True)
parser.add_argument('runs', nargs='+')
args = parser.parse_args()
binary = str(Path(args.binary).resolve())
syms=[]
for line in subprocess.check_output(['nm','-n','-S','-C','--defined-only',binary],text=True).splitlines():
 p=line.split(maxsplit=3)
 if len(p)==4 and p[2] in ('t','T','w','W'):
  try: syms.append((int(p[0],16),int(p[1],16),p[3]))
  except ValueError: pass
addresses=[x[0] for x in syms]
for folder in args.runs:
 d=Path(folder); result=json.loads((d/'result.json').read_text()); maps=[]
 for line in (d/'maps.txt').read_text().splitlines():
  p=line.split(maxsplit=5); lo,hi=(int(x,16) for x in p[0].split('-')); maps.append((lo,hi,int(p[2],16),p[1],p[5] if len(p)>5 else '[anonymous]'))
 bymap=collections.Counter(); bysym=collections.Counter(); selected=0
 data=(d/'samples.bin').read_bytes(); assert len(data)%24==0
 for ts,rip,tid,_ in struct.iter_unpack('<QQII',data):
  if tid != result['pid'] or not result['demo_start_observed_ns']<=ts<=result['demo_end_observed_ns']:continue
  selected+=1; entry=next((m for m in maps if m[0]<=rip<m[1]),None)
  if not entry: bymap['[not in snapshot maps]']+=1; continue
  lo,hi,off,perms,path=entry
  name=os.path.basename(path) if path.startswith('/') else path
  bymap[name]+=1
  if path==binary:
   addr=rip-lo+off; i=bisect.bisect_right(addresses,addr)-1
   if i>=0 and addr<syms[i][0]+syms[i][1]: bysym[syms[i][2]]+=1
   else: bysym['[unresolved in executable]']+=1
 print(json.dumps({'period_us':result['profile_period_us'],'samples_main_observed_window':selected,'regions':bymap.most_common(15),'executable_symbols':bysym.most_common(20)},indent=2))
