#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Interleaved Quake 1.06 timedemo runner for DOSBox Staging A/A and A/B work.

Records, per run, Quake's own FPS and guest seconds plus host-side CPU time of
the emulator's *main thread* between "Playing demo from" and the timedemo
result line (polled every --poll-ms). Linux reads
/proc/<pid>/task/<pid>/schedstat; Windows uses GetThreadTimes on the earliest
created thread and also records QueryThreadCycleTime (see hostplat.py).

Two cycle modes are useful:
  --cycles max          DOSBox auto-adjusts cycles; FPS reflects throughput but
                        includes controller noise (the prior protocol).
  --cycles N            fixed cycles (e.g. 1000000) above what the host can
                        sustain: guest work per demo is constant and the
                        guest-reported FPS should be identical across runs;
                        host main-thread CPU time to reach the result is then
                        the throughput metric.

Variants are given as LABEL=PATH. The schedule is a sequence of balanced
blocks (each block contains every label once) in an order shuffled with a
recorded seed. Game files are copied fresh per run and never written to --out.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostplat  # noqa: E402

START_MARK = b'Playing demo from'
FPS_RE = re.compile(rb'(\d+)\s+frames\s+([\d.]+)\s+seconds\s+([\d.]+)\s+fps', re.I)
QUAKE_SHA256 = '7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb'


def find_file(directory: Path, name: str) -> Path:
    """Case-insensitive lookup of a relative path such as 'ID1/PAK0.PAK'."""
    cur = directory
    for part in name.split('/'):
        match = next((p for p in cur.iterdir() if p.name.lower() == part.lower()), None)
        if match is None:
            raise FileNotFoundError(f'{name} not found in {directory}')
        cur = match
    return cur


def run_one(a, label: str, dosbox: Path, number: int) -> dict:
    tmp_kw = {'ignore_cleanup_errors': True} if sys.version_info >= (3, 10) else {}
    with tempfile.TemporaryDirectory(dir=a.tmp, **tmp_kw) as tmp:
        game = Path(tmp) / 'game'
        shutil.copytree(a.game, game)
        conf = Path(tmp) / 'dosbox.conf'
        conf.write_text('[dosbox]\nmemsize = 32\n[cpu]\ncore = dynamic\n'
                        'cputype = pentium\ncpu_cycles = 3000\n'
                        f'cpu_cycles_protected = {a.cycles}\n[sdl]\n'
                        'output = texture\ntexture_renderer = software\n')
        cmd = [str(dosbox), '--noprimaryconf', '--nolocalconf', '--noautoexec',
               '--conf', str(conf), '-c', f'mount c "{game}"', '-c', 'c:',
               '-c', 'quake.exe -nosound -nocdaudio -condebug -width 320 '
                     '-height 200 +timedemo demo1']
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('DOSBOX_JIT') and k != 'LD_PRELOAD'}
        env.update(SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy',
                   SDL_RENDER_DRIVER='software')
        if a.env:
            for item in a.env:
                k, _, v = item.partition('=')
                env[k] = v
        dlog = Path(tmp) / 'dosbox-output.txt'
        steal0 = hostplat.steal_ticks()
        sens0 = hostplat.sensor_sample(a.affinity_cpus) if a.sensors else {}
        t0 = time.monotonic_ns()
        with dlog.open('wb') as dout:
            proc = subprocess.Popen(cmd, cwd=a.runtime_cwd, env=env,
                                    stdout=dout, stderr=subprocess.STDOUT,
                                    **hostplat.popen_kwargs())
        pinned = hostplat.set_affinity(proc.pid, a.affinity_cpus) if a.affinity_cpus else None
        clock = hostplat.ProcessClock(proc.pid)
        result = None
        status = 'timeout'
        run_ns = wait_ns = proc_ns = 0
        start_run_ns = start_cycles = end_cycles = None
        try:
            deadline = time.monotonic() + a.timeout
            qlog = game / 'ID1' / 'QCONSOLE.LOG'
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    status = f'exit_{proc.returncode}'
                    break
                if qlog.is_file():
                    data = qlog.read_bytes()
                    if start_run_ns is None and START_MARK in data:
                        start_run_ns = clock.main_ns()
                        start_cycles = clock.cycles()
                    m = FPS_RE.findall(data)
                    if m:
                        run_ns = clock.main_ns()
                        wait_ns = clock.wait_ns()
                        end_cycles = clock.cycles()
                        proc_ns = clock.process_ns()
                        result = m[-1]
                        status = 'ok'
                        break
                time.sleep(a.poll_ms / 1000)
        except OSError as e:
            status = f'error_{type(e).__name__}'
        finally:
            wall_ns = time.monotonic_ns() - t0
            steal1 = hostplat.steal_ticks()
            hostplat.kill(proc)
            proc.wait()
            sens1 = hostplat.sensor_sample(a.affinity_cpus) if a.sensors else {}
            if a.run_logs:
                rdir = a.run_logs / f'run-{number:04d}-{label}'
                rdir.mkdir(parents=True, exist_ok=True)
                shutil.copy(conf, rdir / 'dosbox.conf')
                shutil.copy(dlog, rdir / 'dosbox-output.txt')
                qlog = game / 'ID1' / 'QCONSOLE.LOG'
                if qlog.is_file():
                    shutil.copy(qlog, rdir / 'qconsole.log')
    ok = result is not None
    row = {'number': number, 'label': label, 'status': status,
           'frames': int(result[0]) if ok else '',
           'guest_seconds': float(result[1]) if ok else '',
           'fps': float(result[2]) if ok else '',
           'main_thread_cpu_s': round(run_ns / 1e9, 6) if ok else '',
           'demo_cpu_s': round((run_ns - start_run_ns) / 1e9, 6) if ok and start_run_ns is not None else '',
           'main_thread_runqueue_wait_s': round(wait_ns / 1e9, 6) if ok and wait_ns is not None else '',
           'wall_to_result_s': round(wall_ns / 1e9, 6),
           'steal_ticks': (steal1 - steal0) if steal0 is not None and steal1 is not None else '',
           'load1': hostplat.load1() if hostplat.load1() is not None else '',
           'process_cpu_s': round(proc_ns / 1e9, 6) if ok and proc_ns else '',
           'demo_thread_cycles': (end_cycles - start_cycles) if ok and start_cycles is not None and end_cycles is not None else '',
           'affinity': ','.join(map(str, a.affinity_cpus)) if a.affinity_cpus else '',
           'affinity_applied': '' if pinned is None else int(pinned),
           'temp_c_end': sens1.get('temp_c_max', ''),
           'cpu_mhz_end': sens1.get('cpu_mhz', ''),
           'energy_j': hostplat.rapl_delta_j(sens0, sens1) or '',
           'ac_power': sens1.get('ac_power', '')}
    print(json.dumps(row), flush=True)
    return row


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--variant', action='append', required=True, help='LABEL=PATH')
    p.add_argument('--game', type=Path, required=True)
    p.add_argument('--runtime-cwd', type=Path, required=True)
    p.add_argument('--cycles', default='max')
    p.add_argument('--blocks', type=int, default=8)
    p.add_argument('--warmups', type=int, default=2)
    p.add_argument('--seed', type=int, default=None)
    p.add_argument('--timeout', type=int, default=180)
    p.add_argument('--poll-ms', type=float, default=5)
    p.add_argument('--tmp', type=Path, default=None)
    p.add_argument('--env', action='append', help='extra KEY=VALUE for DOSBox')
    p.add_argument('--affinity', default='', help='CPU list for DOSBox, e.g. 2,3')
    p.add_argument('--run-logs', type=Path, default=None,
                   help='keep dosbox.conf, DOSBox output and QCONSOLE.LOG of every run here')
    p.add_argument('--sensors', action='store_true',
                   help='record temperature/frequency/energy per run where readable')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.affinity_cpus = hostplat.parse_cpu_list(a.affinity) if a.affinity else []
    quake = find_file(a.game, 'QUAKE.EXE')
    if hashlib.sha256(quake.read_bytes()).hexdigest() != QUAKE_SHA256:
        p.error('unexpected QUAKE.EXE')
    variants = []
    for v in a.variant:
        label, _, path = v.partition('=')
        variants.append((label, Path(path).resolve()))
    seed = a.seed if a.seed is not None else random.SystemRandom().randrange(2**31)
    rng = random.Random(seed)
    schedule = [variants[i % len(variants)] for i in range(a.warmups)]
    for _ in range(a.blocks):
        block = variants[:]
        rng.shuffle(block)
        schedule += block
    meta = {'seed': seed, 'cycles': a.cycles, 'blocks': a.blocks, 'warmups': a.warmups,
            'variants': {l: {'path': str(pth), 'sha256': hashlib.sha256(pth.read_bytes()).hexdigest()}
                         for l, pth in variants},
            'cpu': hostplat.cpu_model(),
            'nproc': os.cpu_count(), 'poll_ms': a.poll_ms,
            'affinity': a.affinity_cpus, 'platform': sys.platform,
            'cpu_time_source': 'GetThreadTimes(main thread)' if hostplat.IS_WINDOWS
                               else '/proc/<pid>/task/<pid>/schedstat',
            'schedule': [l for l, _ in schedule]}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix('.json').write_text(json.dumps(meta, indent=2) + '\n')
    with a.out.open('w', newline='') as f:
        w = None
        for i, (label, path) in enumerate(schedule, 1):
            row = run_one(a, label, path, i)
            row['warmup'] = int(i <= a.warmups)
            if w is None:
                w = csv.DictWriter(f, list(row))
                w.writeheader()
            w.writerow(row)
            f.flush()


if __name__ == '__main__':
    main()
