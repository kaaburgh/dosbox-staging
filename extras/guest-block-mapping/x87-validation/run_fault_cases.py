#!/usr/bin/env python3
"""Run protected page-fault and unmasked-x87 probes on baseline/candidate."""
import argparse, hashlib, json, os, shutil, struct, subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--runtime-cwd',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--nasm',default='nasm');p.add_argument('--timeout',type=int,default=40);a=p.parse_args()
root=Path(__file__).resolve().parent;out=a.out.resolve()
if out.exists():p.error('output directory must not exist')
out.mkdir(parents=True)
cases={'PFTEST':('pagefault.asm','PF.BIN',32),'PMMF':('pmmf.asm','PMMF.BIN',44),'UNMASK':('unmasked.asm','UNMASK.BIN',8),'MUNMASK':('multiunmask.asm','MUNMASK.BIN',32)}
for name,(asm,_,_) in cases.items():subprocess.run([a.nasm,'-f','bin',str(root/asm),'-o',str(out/(name+'.COM'))],check=True)
rows=[];data_by_case={}
for test,(_,filename,size) in cases.items():
 for label,binary in [('baseline',a.baseline),('candidate',a.candidate)]:
  for core in ('normal','dynamic'):
   d=out/test/label/core;d.mkdir(parents=True);shutil.copy(out/(test+'.COM'),d/(test+'.COM'))
   conf=d/'dosbox.conf';conf.write_text('[dosbox]\nmemsize=32\n[cpu]\ncore='+core+'\ncputype=pentium\ncpu_cycles=3000\ncpu_cycles_protected=max\n[sdl]\noutput=texture\ntexture_renderer=software\n')
   env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',SDL_RENDER_DRIVER='software')
   for k in ('DOSBOX_JIT_BLOCK_TRACE','DOSBOX_JIT_HELPER_PROBE','DOSBOX_JIT_HELPER_STRIDE','DOSBOX_X87_DEBUG_TEST'):env.pop(k,None)
   args=[str(binary.resolve()),'--noprimaryconf','--nolocalconf','--noautoexec','--conf',str(conf),'-c',f'mount c "{d}"','-c','c:','-c',test+'.COM','-c','exit']
   try:proc=subprocess.run(args,cwd=a.runtime_cwd.resolve(),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=a.timeout)
   except subprocess.TimeoutExpired as exc:
    (d/'dosbox.log').write_bytes(exc.stdout or b'');raise RuntimeError(f'{test} {label} {core}: timeout') from exc
   (d/'dosbox.log').write_bytes(proc.stdout);f=d/filename
   if proc.returncode or not f.exists() or f.stat().st_size!=size:raise RuntimeError(f'{test} {label} {core}: missing or incomplete result')
   data=f.read_bytes();data_by_case[test,label,core]=data
   row={'test':test,'label':label,'core':core,'exit':proc.returncode,'bytes':len(data),'output_hex':data.hex(),'sha256':hashlib.sha256(data).hexdigest()}
   if test in ('PFTEST','PMMF'):
    values=struct.unpack('<8I',data[:32]);names=('value1','value2','count','cr2_1','cr2_2','err1','err2','bad');row['page_fault']={k:hex(v) for k,v in zip(names,values)}
    if values!=(0x3f800000,0x3f800000,2,0x800000,0x801000,0,0,0):raise RuntimeError(f'wrong page-fault recovery {row}')
   if test=='PMMF':
    count,status,cw,cr0=struct.unpack('<IHHI',data[32:]);row['unmasked']={'mf_count':count,'status':hex(status),'cw':hex(cw),'cr0':hex(cr0)}
    if not(cr0&0x20) or cw!=0x037b:raise RuntimeError(f'unmasked preconditions failed {row}')
   if test=='MUNMASK':row['unmasked_cases']=[{'status':hex(s),'cw':hex(c),'int10':i,'irq13':q} for s,c,i,q in struct.iter_unpack('<4H',data)]
   rows.append(row);print(json.dumps(row),flush=True)
for test in cases:
 for core in ('normal','dynamic'):
  if data_by_case[test,'baseline',core]!=data_by_case[test,'candidate',core]:raise RuntimeError(f'baseline/candidate difference in {test} {core}')
(out/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
