#!/usr/bin/env python3
"""Local A/B experiment: randomized ABBA/BAAB quartets; no inferential FPS claim."""
import argparse, csv, importlib.util, json, math, os, random, statistics
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--runner',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--game',type=Path,required=True);p.add_argument('--runtime-cwd',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--private',type=Path,required=True);p.add_argument('--quartets',type=int,default=8);p.add_argument('--seed',type=int,default=20261109);a=p.parse_args()
spec=importlib.util.spec_from_file_location('runner',a.runner);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
if a.out.exists() or a.private.exists():p.error('output paths must not exist')
a.out.mkdir(parents=True);a.private.mkdir(parents=True,mode=0o700);a.conf=a.private/'dosbox.conf';a.conf.write_text('[dosbox]\nmemsize=32\n[cpu]\ncore=dynamic\ncputype=pentium\ncpu_cycles=3000\ncpu_cycles_protected=max\n[sdl]\noutput=texture\ntexture_renderer=software\n');a.display='';a.timeout=90
assert mod.sha256(a.game/'QUAKE.EXE')=='7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb'
rng=random.Random(a.seed);orders=[rng.choice(('ABBA','BAAB')) for _ in range(a.quartets)];sequence='ABBA'+''.join(orders)
manifest={'design':'randomized ABBA/BAAB quartets; A baseline B candidate','warmups':4,'quartets':a.quartets,'seed':a.seed,'orders':orders,'dosbox_baseline_sha256':mod.sha256(a.baseline),'dosbox_candidate_sha256':mod.sha256(a.candidate),'quake_sha256':mod.sha256(a.game/'QUAKE.EXE'),'config':a.conf.read_text(),'video_backend':'SDL dummy software texture','cgroup_cpu_stat_available':Path('/sys/fs/cgroup/cpu.stat').exists(),'cgroup_cpu_max':Path('/sys/fs/cgroup/cpu.max').read_text().strip() if Path('/sys/fs/cgroup/cpu.max').exists() else None}
(a.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');rows=[]
for i,label in enumerate(sequence,1):
 a.dosbox=a.baseline if label=='A' else a.candidate
 row=mod.run_one(a,i,label,i<=4)
 if not manifest['cgroup_cpu_stat_available']:row['cgroup_throttled_events']='';row['cgroup_throttled_ms']=''
 rows.append(row);mod.write_rows(a.out/'timedemos.csv',rows)
meas=rows[4:];effects=[]
for i in range(0,len(meas),4):
 q=meas[i:i+4];aa=[math.log(float(x['fps'])) for x in q if x['label']=='A'];bb=[math.log(float(x['fps'])) for x in q if x['label']=='B'];effects.append(statistics.mean(bb)-statistics.mean(aa))
boot=[]
for _ in range(10000):boot.append((math.exp(statistics.mean(rng.choices(effects,k=len(effects))))-1)*100)
boot.sort();stats={'quartets':len(effects),'A_median':statistics.median(float(x['fps']) for x in meas if x['label']=='A'),'B_median':statistics.median(float(x['fps']) for x in meas if x['label']=='B'),'geometric_B_vs_A_percent':(math.exp(statistics.mean(effects))-1)*100,'quartet_bootstrap_95pct_descriptive':[boot[249],boot[9749]],'quartet_effects_pct':[(math.exp(e)-1)*100 for e in effects], 'caveat':'Shared host; exploratory only. One experiment and bootstrap independence assumption do not establish an improvement; validate on stable host.'}
(a.out/'summary.json').write_text(json.dumps(stats,indent=2)+'\n');print(json.dumps(stats,indent=2),flush=True)
