import argparse,collections,json,re,struct,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--probe',required=True);p.add_argument('--record-format',choices=['legacy32','v2'],default='v2');a=p.parse_args()
r=Path(a.run);info=json.loads((r/'result.json').read_text()); start=info['demo_start_observed_ns'];end=info['demo_end_observed_ns']
snaps=[json.loads(p.read_text()) for p in (r/'snapshots').glob('*.json')]; times={}
import csv
with (r/'jit-blocks.csv').open() as f:
 for row in csv.DictReader(f):
  g=int(row['generation']);times.setdefault(g,{})[row['event']]=int(row['monotonic_ns'])
cnt=collections.Counter()
data=Path(a.probe).read_bytes();fmt=struct.Struct('<QQQII' if a.record_format=='legacy32' else '<QQQIIQ');assert len(data)%fmt.size==0
for rec in fmt.iter_unpack(data):
 ts,ret,seq,addr,kind=rec[:5]
 if not start<=ts<=end:continue
 for m in snaps:
  b=int(m['host_begin'],16);e=int(m['host_end'],16);t=times[m['generation']]
  if b<=ret-1<e and t['C']<=ts<t.get('D',10**30):cnt[(m['generation'],m['guest_eip'],kind,ret-b)]+=1
result=[]
for m in snaps:
 f=r/'snapshots'/m['host_filename'];output=subprocess.check_output(['objdump','-D','-b','binary','-m','i386:x86-64','-M','intel','--no-show-raw-insn',str(f)],text=True)
 ins=[]
 for line in output.splitlines():
  match=re.match(r'^\s*([0-9a-f]+):\s+([a-z][a-z0-9]*)\s*(.*)$',line)
  if match:ins.append((int(match[1],16),match[2]))
 for (g,eip,k,ret),n in cnt.items():
  if g!=m['generation']:continue
  previous=[x for x in ins if x[0]<ret]
  prev=previous[-1] if previous else None
  next_starts=any(addr==ret for addr,_ in ins)
  result.append({'eip':eip,'generation':g,'kind':k,'return_offset':ret,'previous_mnemonic':prev[1] if prev else None,'return_on_instruction_start':next_starts,'records':n})
print(json.dumps(result,indent=2));assert all(x['previous_mnemonic']=='call' and x['return_on_instruction_start'] for x in result), 'call-site validation failed'
