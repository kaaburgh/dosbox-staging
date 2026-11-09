#!/usr/bin/env python3
"""Run original DOS Quake 1.06 with traced DOSBox and an optional SIGPROF sampler.

All generated files stay under --out; game binaries are not exported.
For selected native RIP analysis, provide --profiler, --snapshot-eips, and
--runtime-cwd (DOSBox Staging 0.84 resources/mapping parent).
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

FPS_RE = re.compile(r'(\d+)\s+frames\s+([\d.]+)\s+seconds\s+([\d.]+)\s+fps', re.I)


def stop(p):
    if p.poll() is not None:
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        p.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=2)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dosbox', type=Path, required=True)
    p.add_argument('--game', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--profiler', type=Path)
    p.add_argument('--profile-period-us', type=int, default=5000,
                   help='per-thread CPU-time sample period, 1000..1000000 microseconds')
    p.add_argument('--no-trace', action='store_true', help='control run without JIT lifecycle records')
    p.add_argument('--sdk-lib-dir', type=Path)
    p.add_argument('--renderer', choices=['texture', 'opengl'], default='texture')
    p.add_argument('--timeout', type=int, default=100)
    p.add_argument('--snapshot-eips', help='explicit comma-separated guest EIPs (requires JIT snapshot build)')
    p.add_argument('--snapshot-max', type=int, default=16)
    p.add_argument('--runtime-cwd', type=Path,
                   help='DOSBox source/runtime directory with resources/mapping (needed for 0.84)')
    args = p.parse_args()
    if args.snapshot_eips and args.no_trace:
        p.error('selected snapshots require an enabled JIT lifecycle trace')
    if args.snapshot_eips and not args.profiler:
        p.error('selected RIP profiles require --profiler')
    if not 1000 <= args.profile_period_us <= 1000000:
        p.error('--profile-period-us must be in 1000..1000000')
    if not 1 <= args.snapshot_max <= 256:
        p.error('--snapshot-max must be in 1..256')
    cwd = args.runtime_cwd.resolve() if args.runtime_cwd else None
    if cwd and not (cwd / 'resources' / 'mapping').is_dir():
        p.error('--runtime-cwd must contain resources/mapping')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    game = out / 'game'
    shutil.copytree(args.game.resolve(), game, dirs_exist_ok=True)
    for f in game.rglob('*'):
        if f.name.lower() == 'qconsole.log' and f.is_file():
            f.unlink()
    config = out / 'dosbox.conf'
    config.write_text('[dosbox]\nmemsize = 32\n[cpu]\ncore = dynamic\n'
                      'cputype = pentium\ncpu_cycles = 3000\n'
                      'cpu_cycles_protected = max\n[sdl]\noutput = ' + args.renderer + '\n' +
                      ('texture_renderer = software\n' if args.renderer == 'texture' else ''))
    env = dict(os.environ, SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy',
               DISPLAY=':159', DOSBOX_JIT_BLOCK_TRACE=str(out / 'jit-blocks.csv'))
    if args.no_trace:
        env.pop('DOSBOX_JIT_BLOCK_TRACE', None)
    if args.renderer == 'texture':
        env['SDL_RENDER_DRIVER'] = 'software'
    if args.sdk_lib_dir:
        env['LD_LIBRARY_PATH'] = str(args.sdk_lib_dir.resolve()) + ':' + env.get('LD_LIBRARY_PATH', '')
    if args.profiler:
        env.update(DOSBOX_PROFILE_OUTPUT=str(out / 'samples.bin'),
                   DOSBOX_PROFILE_PERIOD_US=str(args.profile_period_us),
                   LD_PRELOAD=str(args.profiler.resolve()))
    if args.snapshot_eips:
        snapshot_dir = out / 'snapshots'
        snapshot_dir.mkdir(mode=0o700, exist_ok=False)
        env.update(DOSBOX_JIT_SNAPSHOT_DIR=str(snapshot_dir),
                   DOSBOX_JIT_SNAPSHOT_EIPS=args.snapshot_eips,
                   DOSBOX_JIT_SNAPSHOT_MAX=str(args.snapshot_max))
    try:
        command = [str(args.dosbox.resolve()), '--noprimaryconf', '--nolocalconf',
                   '--noautoexec', '--conf', str(config), '-c', f'mount c "{game}"',
                   '-c', 'c:',
                   '-c', 'quake.exe -nosound -nocdaudio -condebug -width 320 '
                         '-height 200 +timedemo demo1']
        started = time.monotonic()
        begin_ns = time.monotonic_ns()
        first_demo = None
        end_ns = None
        res = None
        with (out / 'dosbox.log').open('w') as f:
            proc = subprocess.Popen(command, env=env, stdout=f,
                                    stderr=subprocess.STDOUT, start_new_session=True,
                                    cwd=cwd)
            try:
                while time.monotonic() - started < args.timeout:
                    qlog = game / 'ID1' / 'QCONSOLE.LOG'
                    if qlog.is_file():
                        contents = qlog.read_bytes().decode('latin1', 'replace')
                        if first_demo is None and 'Playing demo from demo1.dem.' in contents:
                            first_demo = time.monotonic_ns()
                            (out / 'maps.txt').write_text(Path(f'/proc/{proc.pid}/maps').read_text())
                        ms = FPS_RE.findall(contents)
                        if ms:
                            match = ms[-1]
                            res = {'frames': int(match[0]),
                                   'seconds': float(match[1]),
                                   'fps': float(match[2])}
                            end_ns = time.monotonic_ns()
                            break
                    if proc.poll() is not None:
                        break
                    time.sleep(.08)
            finally:
                stop(proc)
        result = {'pid': proc.pid, 'result': res, 'start_ns': begin_ns,
                  'demo_start_observed_ns': first_demo, 'demo_end_observed_ns': end_ns,
                  'wall_sec': round(time.monotonic() - started, 3),
                  'renderer': args.renderer, 'profiler': bool(args.profiler),
                  'trace_enabled': not args.no_trace,
                  'selected_snapshots': bool(args.snapshot_eips),
                  'profile_period_us': args.profile_period_us if args.profiler else None}
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2), flush=True)
        return 0 if res else 1
    finally:
        pass


if __name__ == '__main__':
    raise SystemExit(main())
