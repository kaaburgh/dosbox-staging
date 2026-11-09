#!/usr/bin/env python3
"""Run headless heavy-debugger callback test and trace protected #PF helper path."""
import argparse,hashlib,json,os,re,shutil,struct,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--runtime-cwd',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--nasm',default='nasm');a=p.parse_args();root=Path(__file__).resolve().parent;out=a.out.resolve()
if out.exists():p.error('output directory must not exist')
out.mkdir(parents=True)
for name,path in [('FPUARITH',root.parent/'x87-inline-exp'/'fpuarith.asm'),('PFTEST',root/'pagefault.asm')]:subprocess.run([a.nasm,'-f','bin',str(path),'-o',str(out/(name+'.COM'))],check=True)
rows=[];values={}
for test in ('FPUARITH','PFTEST'):
 for label,binary in [('baseline',a.baseline),('candidate',a.candidate)]:
  d=out/test/label;d.mkdir(parents=True);shutil.copy(out/(test+'.COM'),d/(test+'.COM'));conf=d/'dosbox.conf';conf.write_text('[dosbox]\nmemsize=32\n[cpu]\ncore=dynamic\ncputype=pentium\ncpu_cycles=3000\ncpu_cycles_protected=max\n[sdl]\noutput=texture\ntexture_renderer=software\n')
  env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',SDL_RENDER_DRIVER='software',DOSBOX_JIT_BLOCK_TRACE=str(d/'trace.csv'),DOSBOX_JIT_HELPER_PROBE=str(d/'probe.bin'),DOSBOX_JIT_HELPER_STRIDE='1')
  if test=='FPUARITH':env['DOSBOX_X87_DEBUG_TEST']='1'
  else:env.pop('DOSBOX_X87_DEBUG_TEST',None)
  args=[str(binary.resolve()),'--noprimaryconf','--nolocalconf','--noautoexec','--conf',str(conf),'-c',f'mount c "{d}"','-c','c:','-c',test+'.COM','-c','exit']
  proc=subprocess.run(args,cwd=a.runtime_cwd.resolve(),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60);(d/'dosbox.log').write_bytes(proc.stdout)
  filename='ARITH.BIN' if test=='FPUARITH' else 'PF.BIN';f=d/filename
  if proc.returncode or not f.exists():raise RuntimeError(f'failed {test}/{label}')
  data=f.read_bytes();values[test,label]=data;probe=(d/'probe.bin').read_bytes()
  if len(probe)%40:raise RuntimeError('truncated probe')
  records=list(struct.iter_unpack('<QQQIIQ',probe));row={'test':test,'build':label,'sha256':hashlib.sha256(data).hexdigest(),'probe_records':len(records)}
  if test=='FPUARITH':
   m=re.search(rb'X87_DEBUG_TEST match=(\d+) unrelated=(\d+) stop=(\d+)',proc.stdout); row['watchpoint']=list(map(int,m.groups())) if m else None
   if row['watchpoint']!=[1,0,1] or len(data)!=768:raise RuntimeError(f'breakpoint test failed: {row}')
  else:
   addrs=[hex(addr) for _,_,_,addr,kind,_ in records if kind==0 and addr in (0x800000,0x800ffe)];row['fpu_probe_addresses']=addrs
   if 0x800ffe not in [int(x,16) for x in addrs] or struct.unpack('<8I',data)!=(0x3f800000,0x3f800000,2,0x800000,0x801000,0,0,0):raise RuntimeError(f'protected x87 fault test failed: {row}')
  rows.append(row);print(json.dumps(row),flush=True)
for test in ('FPUARITH','PFTEST'):
 if values[test,'baseline']!=values[test,'candidate']:raise RuntimeError(f'baseline/candidate mismatch: {test}')
(out/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
