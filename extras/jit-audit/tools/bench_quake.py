#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Interleaved Quake 1.06 timedemo runner for DOSBox Staging A/A and A/B work.

Records, per run, Quake's own FPS and guest seconds plus host-side metrics of
the emulator's *main thread* read from /proc/<pid>/task/<pid>/schedstat at the
moment the timedemo result appears (polled every --poll-ms).

Two cycle modes are useful:
  --cycles max          DOSBox auto-adjusts cycles; FPS reflects throughput but
                        includes controller noise (the prior protocol).
  --cycles 'fixed N'    with N above what the host can sustain, guest work per
                        demo is (near) constant and the guest-reported FPS should
                        be identical across runs; host main-thread CPU time to
                        reach the result is then the throughput metric.

Variants are given as LABEL=PATH. The schedule is a sequence of balanced
blocks (each block contains every label once) in an order shuffled with a
recorded seed. Game files are copied fresh per run and never written to --out.
"""
import argparse
import csv
import json
import os
import random
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

START_MARK = b'Playing demo from'
FPS_RE = re.compile(rb'(\d+)\s+frames\s+([\d.]+)\s+seconds\s+([\d.]+)\s+fps', re.I)
QUAKE_SHA256 = '7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb'


def read_schedstat(pid: int) -> tuple[int, int]:
    run_ns, wait_ns, _ = (int(x) for x in
                          Path(f'/proc/{pid}/task/{pid}/schedstat').read_text().split())
    return run_ns, wait_ns


def read_steal() -> int:
    fields = Path('/proc/stat').read_text().splitlines()[0].split()
    return int(fields[8])  # USER_HZ ticks


def run_one(a, label: str, dosbox: Path, number: int) -> dict:
    with tempfile.TemporaryDirectory(dir=a.tmp) as tmp:
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
        log = Path(tmp) / 'qconsole'
        steal0 = read_steal()
        t0 = time.monotonic_ns()
        proc = subprocess.Popen(cmd, cwd=a.runtime_cwd, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        result = None
        status = 'timeout'
        run_ns = wait_ns = 0
        start_run_ns = None
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
                        start_run_ns, _ = read_schedstat(proc.pid)
                    m = FPS_RE.findall(data)
                    if m:
                        run_ns, wait_ns = read_schedstat(proc.pid)
                        result = m[-1]
                        status = 'ok'
                        break
                time.sleep(a.poll_ms / 1000)
        finally:
            wall_ns = time.monotonic_ns() - t0
            steal1 = read_steal()
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    row = {'number': number, 'label': label, 'status': status,
           'frames': int(result[0]) if result else '',
           'guest_seconds': float(result[1]) if result else '',
           'fps': float(result[2]) if result else '',
           'main_thread_cpu_s': round(run_ns / 1e9, 6) if result else '',
           'demo_cpu_s': round((run_ns - start_run_ns) / 1e9, 6) if result and start_run_ns is not None else '',
           'main_thread_runqueue_wait_s': round(wait_ns / 1e9, 6) if result else '',
           'wall_to_result_s': round(wall_ns / 1e9, 6),
           'steal_ticks': steal1 - steal0,
           'load1': round(os.getloadavg()[0], 2)}
    print(json.dumps(row), flush=True)
    return row


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
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
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    import hashlib
    if hashlib.sha256((a.game / 'QUAKE.EXE').read_bytes()).hexdigest() != QUAKE_SHA256:
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
            'cpu': next((l.split(':', 1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines()
                         if l.startswith('model name')), ''),
            'nproc': os.cpu_count(), 'poll_ms': a.poll_ms,
            'schedule': [l for l, _ in schedule]}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix('.json').write_text(json.dumps(meta, indent=2) + '\n')
    rows = []
    with a.out.open('w', newline='') as f:
        w = None
        for i, (label, path) in enumerate(schedule, 1):
            row = run_one(a, label, path, i)
            row['warmup'] = int(i <= a.warmups)
            rows.append(row)
            if w is None:
                w = csv.DictWriter(f, list(row))
                w.writeheader()
            w.writerow(row)
            f.flush()


if __name__ == '__main__':
    main()
