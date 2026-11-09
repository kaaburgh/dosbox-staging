#!/usr/bin/env python3
"""Compare x87 outputs across normal/dynamic cores and diagnostic modes."""
import argparse,hashlib,json,os,shutil,struct,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--candidate-traced',type=Path,required=True);p.add_argument('--runtime-cwd',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--nasm',default='nasm');a=p.parse_args()
root=Path(__file__).resolve().parent;out=a.out.resolve()
if out.exists():p.error('--out must not already exist')
out.mkdir(parents=True)
fixtures=[('FPUARITH','fpuarith.asm','ARITH.BIN',768),('FPUMMIO','fpummio.asm','MMIO.OK',2)]
for name,asm,_,_ in fixtures:subprocess.run([a.nasm,'-f','bin',str(root/asm),'-o',str(out/(name+'.COM'))],check=True)
cases=[('baseline-normal',a.baseline,'normal',False),('baseline-dynamic',a.baseline,'dynamic',False),('candidate-traced-inactive',a.candidate_traced,'dynamic',False),('candidate-traced-active',a.candidate_traced,'dynamic',True),('candidate-normal',a.candidate,'normal',False),('candidate-dynamic',a.candidate,'dynamic',False)]
rows=[];raw={}
for test,_,filename,size in fixtures:
 for name,binary,core,active in cases:
  d=out/test/name;d.mkdir(parents=True);shutil.copy(out/(test+'.COM'),d/(test+'.COM'))
  conf=d/'dosbox.conf';conf.write_text('[dosbox]\nmemsize=32\n[cpu]\ncore='+core+'\ncputype=pentium\ncpu_cycles=3000\ncpu_cycles_protected=max\n[sdl]\noutput=texture\ntexture_renderer=software\n')
  env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',SDL_RENDER_DRIVER='software')
  for k in ('DOSBOX_JIT_BLOCK_TRACE','DOSBOX_JIT_HELPER_PROBE','DOSBOX_JIT_HELPER_STRIDE'):env.pop(k,None)
  if active:env.update(DOSBOX_JIT_BLOCK_TRACE=str(d/'trace.csv'),DOSBOX_JIT_HELPER_PROBE=str(d/'probe.bin'),DOSBOX_JIT_HELPER_STRIDE='1')
  cmd=[str(binary.resolve()),'--noprimaryconf','--nolocalconf','--noautoexec','--conf',str(conf),'-c',f'mount c "{d}"','-c','c:','-c',test+'.COM','-c','exit']
  proc=subprocess.run(cmd,cwd=a.runtime_cwd.resolve(),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60);(d/'dosbox.log').write_bytes(proc.stdout)
  path=d/filename
  if proc.returncode or not path.exists() or path.stat().st_size!=size or (d/'MMIO.BAD').exists():raise RuntimeError(f'{test}/{name}: incomplete test')
  value=path.read_bytes();raw[test,name]=value;row={'test':test,'case':name,'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()}
  if active:
   data=(d/'probe.bin').read_bytes();assert len(data)%40==0
   records=list(struct.iter_unpack('<QQQIIQ',data));row['probe_records']=len(records)
   assert records
   if test=='FPUMMIO':
    reads=[address for _,_,_,address,kind,_ in records if kind==0];row['mmio_crosspage_reads']={hex(addr):reads.count(addr) for addr in (0xa0ffd,0xa0ffe,0xa0fff)}
    assert all(v>=4 for v in row['mmio_crosspage_reads'].values())
  rows.append(row);print(json.dumps(row),flush=True)
for row in rows:
 row['equal_baseline_dynamic']=raw[row['test'],row['case']]==raw[row['test'],'baseline-dynamic']
 if not row['equal_baseline_dynamic']:raise RuntimeError(f"Mismatch {row['test']}/{row['case']}")
(out/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
