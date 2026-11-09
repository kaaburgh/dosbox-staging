#!/usr/bin/env python3
"""Run public DOS fixtures with baseline, inactive, and active diagnostic binaries."""
import argparse, json, os, shutil, struct, subprocess
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--traced',type=Path,required=True);p.add_argument('--runtime-cwd',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--nasm',default='nasm');a=p.parse_args()
root=Path(__file__).resolve().parent; extras=root.parent
out=a.out.resolve()
if out.exists():p.error('--out must not already exist')
out.mkdir(parents=True)
cases={'FPUTEST':(root/'fputest.asm','FPU.OK'),'VGATEST':(root/'vgatest.asm','VGA.OK'),'SMC':(extras/'smc_invalidations.asm','SMC.OK'),'XPG':(extras/'crosspage_invalidations.asm','XPG.OK')}
for name,(asm,_) in cases.items():subprocess.run([a.nasm,'-f','bin',str(asm),'-o',str(out/(name+'.COM'))],check=True)
rows=[]
for binary,label in [(a.baseline,'off'),(a.traced,'inactive'),(a.traced,'active')]:
 for mode in ['normal','dynamic']:
  for name,(_,markername) in cases.items():
   d=out/label/mode/name;d.mkdir(parents=True);shutil.copy(out/(name+'.COM'),d/(name+'.COM'))
   conf=d/'dosbox.conf';conf.write_text('[dosbox]\nmemsize=32\n[cpu]\ncore='+mode+'\ncputype=pentium\ncpu_cycles=3000\ncpu_cycles_protected=max\n[sdl]\noutput=texture\ntexture_renderer=software\n')
   env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',SDL_RENDER_DRIVER='software')
   for k in ('DOSBOX_JIT_BLOCK_TRACE','DOSBOX_JIT_HELPER_PROBE','DOSBOX_JIT_HELPER_STRIDE'):env.pop(k,None)
   if label=='active':env.update(DOSBOX_JIT_BLOCK_TRACE=str(d/'trace.csv'),DOSBOX_JIT_HELPER_PROBE=str(d/'probe.bin'))
   cmd=[str(binary.resolve()),'--noprimaryconf','--nolocalconf','--noautoexec','--conf',str(conf),'-c',f'mount c "{d}"','-c','c:','-c',name+'.COM','-c','exit']
   proc=subprocess.run(cmd,cwd=a.runtime_cwd.resolve(),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
   (d/'dosbox.log').write_bytes(proc.stdout);marker=d/markername;bad=d/('VGA.BAD' if name=='VGATEST' else 'FPU.BAD')
   success=proc.returncode==0 and marker.exists() and marker.read_bytes() in (b'OK',b'OK\r\n') and not bad.exists()
   probe=d/'probe.bin';counts={}
   if probe.exists():
    data=probe.read_bytes();assert len(data)%40==0
    for ts,ret,seq,addr,kind,vptr in struct.iter_unpack('<QQQIIQ',data):counts[str(kind)]=counts.get(str(kind),0)+1
   if label=='active' and mode=='dynamic' and name=='FPUTEST':success &= bool(counts.get('0')) and bool(counts.get('1'))
   if label=='active' and mode=='dynamic' and name=='VGATEST':success &= bool(counts.get('2'))
   row={'build':label,'core':mode,'case':name,'passed':bool(success),'probe_records':counts,'trace_exists':(d/'trace.csv').exists()};rows.append(row);print(json.dumps(row),flush=True)
   (out/'matrix-results.json').write_text(json.dumps(rows,indent=2)+'\n')
   if not success:raise SystemExit(1)
# An independent run captures every helper entry, proving all three cross-page
# offsets for both load and store were exercised by the dynamic core.
d=out/'active'/'dynamic'/'FPU-ALL';d.mkdir(parents=True);shutil.copy(out/'FPUTEST.COM',d/'FPUTEST.COM')
conf=d/'dosbox.conf';conf.write_text('[dosbox]\nmemsize=32\n[cpu]\ncore=dynamic\ncputype=pentium\ncpu_cycles=3000\ncpu_cycles_protected=max\n[sdl]\noutput=texture\ntexture_renderer=software\n')
env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',SDL_RENDER_DRIVER='software',DOSBOX_JIT_BLOCK_TRACE=str(d/'trace.csv'),DOSBOX_JIT_HELPER_PROBE=str(d/'probe.bin'),DOSBOX_JIT_HELPER_STRIDE='1')
cmd=[str(a.traced.resolve()),'--noprimaryconf','--nolocalconf','--noautoexec','--conf',str(conf),'-c',f'mount c "{d}"','-c','c:','-c','FPUTEST.COM','-c','exit']
proc=subprocess.run(cmd,cwd=a.runtime_cwd.resolve(),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60);(d/'dosbox.log').write_bytes(proc.stdout)
data=(d/'probe.bin').read_bytes();assert len(data)%40==0
counts={}
for ts,ret,seq,addr,kind,vptr in struct.iter_unpack('<QQQIIQ',data):
 if kind in (0,1):
  key=f'{kind}:{addr&4095:03x}';counts[key]=counts.get(key,0)+1
ok=proc.returncode==0 and (d/'FPU.OK').exists() and not (d/'FPU.BAD').exists() and all(counts.get(f'{kind}:{offset:03x}',0)>=5 for kind in (0,1) for offset in (0xffd,0xffe,0xfff))
row={'case':'FPU-all-calls-cross-page','passed':ok,'counts':counts};print(json.dumps(row),flush=True);(out/'fpu-all-call-results.json').write_text(json.dumps(row,indent=2)+'\n')
if not ok:raise SystemExit(1)
