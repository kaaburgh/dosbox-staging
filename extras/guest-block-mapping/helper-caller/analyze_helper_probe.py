#!/usr/bin/env python3
"""Join private sampled helper return addresses with same-process JIT lifetimes."""
import argparse, collections, json, struct, sys
from pathlib import Path
# Supplied explicitly to avoid using a different checkout's trace parser.
p=argparse.ArgumentParser();p.add_argument('--parser-dir',default=str(Path(__file__).resolve().parent.parent));p.add_argument('--run',required=True);p.add_argument('--probe',required=True);p.add_argument('--out',required=True);p.add_argument('--stride',type=int,default=257);a=p.parse_args()
sys.path.insert(0,a.parser_dir)
from analyze_trace import events_from_csv,LiveMap,reject_recorder_io_errors
run=Path(a.run); info=json.loads((run/'result.json').read_text()); assert info['result']['frames']==969
reject_recorder_io_errors(run/'dosbox.log')
log=(run/'dosbox.log').read_text(errors='replace'); assert 'JIT helper probe: open failed' not in log and 'JIT helper probe: write failed' not in log and 'JIT helper probe: invalid stride' not in log
records=Path(a.probe).read_bytes(); fmt=struct.Struct('<QQQIIQ'); assert len(records)%fmt.size==0
samples=[]; start=info['demo_start_observed_ns']; end=info['demo_end_observed_ns']; assert start and end and start<end
last={}; names=['fpu_load32','fpu_store32','dword_store_handler','dword_load_handler','checked_stack_push']
for timestamp,ret,seq,address,kind,vptr in fmt.iter_unpack(records):
 assert kind<len(names) and (seq-1)%a.stride==0 and seq>last.get(kind,0) and ret and timestamp
 last[kind]=seq
 if start<=timestamp<=end: samples.append((timestamp,ret,seq,address,kind))
samples.sort(); ev=events_from_csv(run/'jit-blocks.csv'); assert ev and ev[0].pid==info['pid']; live=LiveMap(); i=0; totals=collections.Counter(); unmatched=collections.Counter(); groups=collections.Counter(); crossing=collections.Counter(); gens=collections.defaultdict(set)
for ts,ret,seq,address,kind in samples:
 while i<len(ev) and ev[i].timestamp_ns<=ts:live.apply(ev[i]); i+=1
 k=names[kind];totals[k]+=1
 b=live.locate(ret-1)
 if not b:unmatched[k]+=1;continue
 key=(k,b.guest_cs,b.guest_eip);groups[key]+=1;gens[key].add(b.generation)
 if address & 4095 >=4093: crossing[key]+=1
out={'pid':info['pid'],'sampling_every_calls':a.stride,'last_sequence_all_run':dict(zip(names,[last.get(i,0) for i in range(5)])),'window_sampled':dict(totals),'unmatched':dict(unmatched),'blocks':[{'helper':k,'guest_cs':f'0x{cs:04x}','guest_eip':f'0x{eip:08x}','samples':n,'generations':len(gens[(k,cs,eip)]),'cross_page_sampled':crossing[(k,cs,eip)]} for (k,cs,eip),n in groups.most_common()]}
Path(a.out).write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({k:v for k,v in out.items() if k!='blocks'},indent=2)); print(json.dumps(out['blocks'][:20],indent=2))
