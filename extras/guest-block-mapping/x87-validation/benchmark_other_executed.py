#!/usr/bin/env python3
"""Same-host exploratory Doom timedemo and x87 synthetic A/A then A/B."""
import csv,hashlib,json,os,random,re,resource,shutil,statistics,subprocess,time
from pathlib import Path
base=Path('/tmp/x87-review-baseline');cand=Path('/tmp/x87-review-candidate');root=Path('/tmp/x87-new/other-bench');assert not root.exists();root.mkdir(parents=True)
settings='[dosbox]\nmemsize=32\n[cpu]\ncore=dynamic\ncputype=pentium\ncpu_cycles=max\ncpu_cycles_protected=max\n[sdl]\noutput=texture\ntexture_renderer=software\n'
env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',SDL_RENDER_DRIVER='software',LD_LIBRARY_PATH='/tmp/apt-local/root/usr/lib/x86_64-linux-gnu')
for k in list(env):
 if k.startswith('DOSBOX_JIT_') or k.startswith('DOSBOX_PROFILE_') or k=='LD_PRELOAD':env.pop(k)
# Warmup AB, placebo one ABBA with identical baseline, then two ABBA/BAAB true quartets.
sequence=[('warmup','A'),('warmup','B')]+[('AA',x) for x in 'ABBA']+[('AB',x) for x in 'ABBABAAB']
rows=[]
for test in ('x87bench','doom'):
 for n,(phase,label) in enumerate(sequence,1):
  d=root/f'{test}-{n:02d}';d.mkdir();shutil.copytree('/tmp/doom-game',d,dirs_exist_ok=True) if test=='doom' else shutil.copy('/tmp/x87-new/X87BENCH.COM',d/'X87BENCH.COM')
  conf=d/'dosbox.conf';conf.write_text(settings)
  binary=base if (phase=='AA' or label=='A') else cand
  program='doom.exe -timedemo demo3 -nosound -nomusic > DRESULT.TXT' if test=='doom' else 'X87BENCH.COM'
  args=[str(binary),'--noprimaryconf','--nolocalconf','--noautoexec','--conf',str(conf),'-c',f'mount c "{d}"','-c','c:','-c',program,'-c','exit']
  before=resource.getrusage(resource.RUSAGE_CHILDREN);start=time.monotonic()
  p=subprocess.run(args,cwd='/workspace/scratch/a9fc6d2f50c7/dosbox-check',env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180);wall=time.monotonic()-start;after=resource.getrusage(resource.RUSAGE_CHILDREN)
  (d/'host.log').write_bytes(p.stdout);match=None
  if test=='doom':
   f=d/'DRESULT.TXT';match=re.search(r'timed\s+(\d+)\s+gametics\s+in\s+(\d+)\s+realtics',f.read_text('latin1'),re.I) if f.exists() else None;ok=bool(match) and int(match.group(1))==2134
  else:ok=(d/'X87.OK').exists() and (d/'X87.OK').read_bytes()==b'OK' and not (d/'X87.BAD').exists()
  row={'workload':test,'n':n,'phase':phase,'label':label,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'wall_s':round(wall,5),'user_s':round(after.ru_utime-before.ru_utime,5),'sys_s':round(after.ru_stime-before.ru_stime,5),'doom_gametics':int(match.group(1)) if match else '', 'doom_realtics':int(match.group(2)) if match else '', 'status':'ok' if ok and p.returncode==0 else 'failed'}
  rows.append(row);print(json.dumps(row),flush=True)
  with (root/'results.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
  if row['status']!='ok':raise RuntimeError(f'invalid run {test} {n}')
  shutil.rmtree(d)
summary={}
for test in ('x87bench','doom'):
 rr=[r for r in rows if r['workload']==test];entry={}
 for phase in ('AA','AB'):
  phase_rows=[r for r in rr if r['phase']==phase];a=[r['wall_s'] for r in phase_rows if r['label']=='A'];b=[r['wall_s'] for r in phase_rows if r['label']=='B'];entry[phase]={'A_median_wall_s':statistics.median(a),'B_median_wall_s':statistics.median(b),'B_vs_A_percent':(statistics.median(b)/statistics.median(a)-1)*100,'n_per_label':len(a)}
 summary[test]=entry
(root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
