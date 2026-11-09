import argparse,collections,json,struct,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from analyze_trace import events_from_csv,LiveMap
p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--run',required=True);p.add_argument('--probe',required=True);p.add_argument('--eip',default='0x10572');a=p.parse_args()
binary=str(Path(a.binary).resolve())
syms=[]
for line in subprocess.check_output(['nm','-n','-S','-C',binary],text=True).splitlines():
 p=line.split(maxsplit=3)
 if len(p)==4 and p[3].startswith('vtable for '):
  try:syms.append((int(p[0],16),int(p[1],16),p[3]))
  except ValueError:pass
for _ in [0]:
 r=Path(a.run); info=json.loads((r/'result.json').read_text()); rows=(r/'maps.txt').read_text().splitlines(); base=int(next(x for x in rows if binary in x and x.split()[2]=='00000000').split('-')[0],16)
 events=events_from_csv(r/'jit-blocks.csv');live=LiveMap();i=0;cnt=collections.Counter();addr=collections.Counter();data=Path(a.probe).read_bytes()
 for ts,ret,seq,address,kind,vptr in struct.iter_unpack('<QQQIIQ',data):
  if not info['demo_start_observed_ns']<=ts<=info['demo_end_observed_ns']:continue
  while i<len(events) and events[i].timestamp_ns<=ts:live.apply(events[i]);i+=1
  if kind!=2:continue
  b=live.locate(ret-1)
  if not b or b.guest_eip!=int(a.eip,0):continue
  offset=vptr-base; names=[name for a,size,name in syms if a<=offset<a+size];cnt[names[0] if len(names)==1 else 'unresolved']+=1
  addr['linear_A0000_AFFFF' if 0xa0000<=address<0xb0000 else 'other']+=1
 print(json.dumps({'guest_eip':a.eip,'samples_by_handler':cnt,'guest_linear_ranges':addr},indent=2))
