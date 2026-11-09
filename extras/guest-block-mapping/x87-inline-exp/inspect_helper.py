#!/usr/bin/env python3
"""Record compiler-specific helper shape; not a semantic proof."""
import argparse,json,re,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();out={}
for name,path in [('baseline',a.baseline),('candidate',a.candidate)]:
 nm=subprocess.check_output(['nm','-S','-C',str(path)],text=True); match=re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+\w\s+FPU_FLD_32\(unsigned int\)$',nm,re.M)
 if not match:raise ValueError('helper symbol missing')
 dis=subprocess.check_output(['objdump','-d','-C','--disassemble=FPU_FLD_32(unsigned int)',str(path)],text=True)
 out[name]={'helper_bytes':int(match.group(2),16),'has_call_to_mem_readd':bool(re.search(r'call.*mem_readd<',dis)),'has_unaligned_read_reference':'mem_unalignedreadd' in dis,'has_indirect_call':bool(re.search(r'call\s+\*',dis))}
if not out['baseline']['has_call_to_mem_readd'] or out['candidate']['has_call_to_mem_readd']:raise RuntimeError('expected compiler shape not observed')
a.out.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
