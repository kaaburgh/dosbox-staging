#!/usr/bin/env python3
"""Run A/A Quake 1.06 timedemo on one host using the SAME uninstrumented DOSBox.

A/B are intentionally labels only, assigned ABBA per quartet. This measures
host-run variation and false-positive A/B effects, not code changes.
Private game assets and raw DOSBox logs are never placed in the results tree.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import time

FPS_RE = re.compile(r'(\d+)\s+frames\s+([\d.]+)\s+seconds\s+([\d.]+)\s+fps', re.I)
FIELDS = ['number', 'label', 'warmup', 'frames', 'fps', 'quake_seconds',
          'wall_seconds', 'emulator_user_cpu_seconds', 'emulator_sys_cpu_seconds',
          'host_load1_before', 'host_load1_after', 'cgroup_throttled_events',
          'cgroup_throttled_ms', 'status']


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def cgroup_cpu() -> dict[str, int]:
    path = Path('/sys/fs/cgroup/cpu.stat')
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text().splitlines():
        name, value = line.split()
        if value.isdigit():
            values[name] = int(value)
    return values


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    else:
        proc.wait()


def run_one(a: argparse.Namespace, number: int, label: str, warmup: bool) -> dict:
    game = a.private / f'game-{number:03d}'
    # Fresh game state each time; QCONSOLE and autoexec/config cannot carry over.
    shutil.copytree(a.game, game)
    for p in game.rglob('*'):
        if p.is_file() and p.name.lower() == 'qconsole.log':
            p.unlink()
    cmd = [str(a.dosbox), '--noprimaryconf', '--nolocalconf', '--noautoexec',
           '--conf', str(a.conf), '-c', f'mount c "{game}"', '-c', 'c:',
           '-c', 'quake.exe -nosound -nocdaudio -condebug -width 320 '
                 '-height 200 +timedemo demo1']
    env = os.environ.copy()
    for k in tuple(env):
        if (k.startswith('DOSBOX_JIT_') or k.startswith('DOSBOX_PROFILE_') or
                k == 'LD_PRELOAD'):
            env.pop(k)
    env.update(SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy',
               SDL_RENDER_DRIVER='software', DISPLAY=a.display)
    start_cg = cgroup_cpu()
    before_rusage = resource.getrusage(resource.RUSAGE_CHILDREN)
    load_before = os.getloadavg()[0]
    t0 = time.monotonic()
    result = None
    status = 'timed_out'
    log_path = a.private / f'dosbox-{number:03d}.log'
    with log_path.open('w') as log:
        proc = subprocess.Popen(cmd, cwd=a.runtime_cwd, env=env,
                                stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            while time.monotonic() - t0 < a.timeout:
                path = game / 'ID1' / 'QCONSOLE.LOG'
                if path.is_file():
                    matches = FPS_RE.findall(path.read_bytes().decode('latin1', 'replace'))
                    if matches:
                        f, s, fps = matches[-1]
                        result = (int(f), float(s), float(fps))
                        status = 'ok'
                        break
                if proc.poll() is not None:
                    status = f'early_exit_{proc.returncode}'
                    break
                time.sleep(0.08)
        finally:
            stop(proc)
    wall = time.monotonic() - t0
    after_rusage = resource.getrusage(resource.RUSAGE_CHILDREN)
    end_cg = cgroup_cpu()
    row = {
        'number': number, 'label': label, 'warmup': int(warmup),
        'frames': result[0] if result else '',
        'fps': result[2] if result else '',
        'quake_seconds': result[1] if result else '',
        'wall_seconds': round(wall, 4),
        'emulator_user_cpu_seconds': round(after_rusage.ru_utime - before_rusage.ru_utime, 4),
        'emulator_sys_cpu_seconds': round(after_rusage.ru_stime - before_rusage.ru_stime, 4),
        'host_load1_before': round(load_before, 3),
        'host_load1_after': round(os.getloadavg()[0], 3),
        'cgroup_throttled_events': end_cg.get('nr_throttled', 0) - start_cg.get('nr_throttled', 0),
        'cgroup_throttled_ms': round((end_cg.get('throttled_usec', 0) - start_cg.get('throttled_usec', 0)) / 1000, 3),
        'status': status,
    }
    print(json.dumps(row), flush=True)
    if status == 'ok' and row['frames'] == 969:
        shutil.rmtree(game)
        log_path.unlink()
    else:
        # Keep source log in a PRIVATE directory for CI diagnosis only.
        raise RuntimeError(f'incomplete Quake timedemo {number}: {row}; private log {log_path}')
    return row


def write_rows(p: Path, rows: list[dict]) -> None:
    with p.open('w', newline='') as f:
        writer = csv.DictWriter(f, FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dosbox', type=Path, required=True)
    p.add_argument('--game', type=Path, required=True)
    p.add_argument('--runtime-cwd', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--private', type=Path, required=True)
    p.add_argument('--warmups', type=int, default=4)
    p.add_argument('--repetitions', type=int, default=48)
    p.add_argument('--timeout', type=int, default=90)
    p.add_argument('--display', default=':172')
    a = p.parse_args()
    if a.warmups < 2 or a.repetitions < 8 or a.repetitions % 4:
        p.error('require >=2 warmups and >=8 measured repetitions divisible by 4')
    if a.timeout < 30:
        p.error('timeout must be >= 30 seconds')
    a.dosbox, a.game, a.runtime_cwd = (x.resolve() for x in
                                      (a.dosbox, a.game, a.runtime_cwd))
    if not a.dosbox.is_file():
        p.error('DOSBox executable does not exist')
    if not (a.runtime_cwd / 'resources/mapping').is_dir():
        p.error('runtime cwd does not contain resources/mapping')
    if not (a.game / 'ID1/PAK0.PAK').is_file():
        p.error('ID1/PAK0.PAK not found')
    quake_sha = sha256(a.game / 'QUAKE.EXE')
    if quake_sha != '7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb':
        p.error('unexpected Quake 1.06 executable SHA-256')
    if a.out.exists() or a.private.exists():
        p.error('output and private directories must not exist')
    a.out.mkdir(parents=True, mode=0o755)
    a.private.mkdir(parents=True, mode=0o700)
    a.conf = a.private / 'dosbox.conf'
    a.conf.write_text('[dosbox]\nmemsize = 32\n[cpu]\ncore = dynamic\n'
                      'cputype = pentium\ncpu_cycles = 3000\n'
                      'cpu_cycles_protected = max\n[sdl]\noutput = texture\n'
                      'texture_renderer = software\n')
    cpu_model = next((line.partition(':')[2].strip() for line in
                      Path('/proc/cpuinfo').read_text().splitlines()
                      if line.startswith('model name')), 'unknown')
    def try_read(path: str) -> str | None:
        pth = Path(path)
        return pth.read_text().strip() if pth.exists() else None
    manifest = {
        'date_utc': datetime.now(timezone.utc).isoformat(),
        'source_sha': os.environ.get('GITHUB_SHA'),
        'dosbox_sha256': sha256(a.dosbox),
        'quake_sha256': quake_sha,
        'pak0_sha256': sha256(a.game / 'ID1/PAK0.PAK'),
        'host': os.uname()._asdict() if hasattr(os.uname(), '_asdict') else list(os.uname()),
        'cpu_model': cpu_model, 'visible_cpu_count': os.cpu_count(),
        'affinity_cpus': len(os.sched_getaffinity(0)),
        'cgroup_cpu_max': try_read('/sys/fs/cgroup/cpu.max'),
        'cgroup_cpuset': try_read('/sys/fs/cgroup/cpuset.cpus.effective'),
        'warmups': a.warmups, 'measured_repetitions': a.repetitions,
        'quartet_label_order': ['A','B','B','A'],
        'A_equals_B': True, 'profiled': False, 'trace_compiled_out': True,
        'config': a.conf.read_text(), 'fps_precision': 'one decimal as logged by Quake',
        'timedemo': 'demo1', 'expected_frames': 969,
    }
    (a.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    manifest['video_backend'] = 'SDL dummy / software texture; no X server'
    manifest['cgroup_cpu_stat_available'] = Path('/sys/fs/cgroup/cpu.stat').exists()
    (a.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    rows = []
    sequence = ['W'] * a.warmups + (['A', 'B', 'B', 'A'] * (a.repetitions // 4))
    for i, label in enumerate(sequence, 1):
        row = run_one(a, i, label, i <= a.warmups)
        if not manifest['cgroup_cpu_stat_available']:
            row['cgroup_throttled_events'] = ''
            row['cgroup_throttled_ms'] = ''
        rows.append(row)
        write_rows(a.out / 'timedemos.csv', rows)
    print(f'completed {a.repetitions} measured + {a.warmups} warmup Quake demo1 runs', flush=True)



if __name__ == '__main__':
    main()
