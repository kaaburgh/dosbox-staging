import sys,pathlib,argparse,json,random,csv,hashlib
sys.path.insert(0,'/workspace/dosbox-staging/extras/guest-block-mapping/headless-study')
from run_quake_headless_stability import run_one,FIELDS
root=pathlib.Path('/workspace/quake-study/fpu-oracle');private=root/'compare-private';private.mkdir(mode=0o700,exist_ok=False);out=root/'comparison.csv'
conf=private/'dosbox.conf';conf.write_text('[dosbox]\nmemsize = 32\n[cpu]\ncore = dynamic\ncputype = pentium\ncpu_cycles = 3000\ncpu_cycles_protected = max\n[sdl]\noutput = texture\ntexture_renderer = software\n')
a=argparse.Namespace(private=private,game=pathlib.Path('/workspace/quake-study/private/game'),dosbox=None,conf=conf,display=':172',runtime_cwd=pathlib.Path('/workspace/dosbox-staging'),timeout=90)
binaries={'A':root/'reference','B':root/'candidate'}
quartets=['ABBA']*4+['BAAB']*4;random.Random(20261109).shuffle(quartets)
manifest={'quartets':quartets,'warmups':'ABBA','A':'reference','B':'candidate','sha256':{k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in binaries.items()},'preplanned':'32 runs, descriptive only; A/A already demonstrated insufficient precision for 1–3%'}
(root/'comparison-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
rows=[]
for i,label in enumerate('ABBA'+''.join(quartets),1):
 a.dosbox=binaries[label];row=run_one(a,i,label,i<=4);rows.append(row)
 with out.open('w',newline='') as f:
  w=csv.DictWriter(f,FIELDS);w.writeheader();w.writerows(rows)
print('finished',len(rows))
