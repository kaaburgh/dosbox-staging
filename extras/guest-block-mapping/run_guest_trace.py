#!/usr/bin/env python3
"""Run original DOS Quake 1.06 with traced DOSBox and an optional SIGPROF sampler.

All generated files stay under --out; game binaries are not exported.
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
    p.add_argument('--no-trace', action='store_true', help='control run without JIT lifecycle records')
    p.add_argument('--sdk-lib-dir', type=Path)
    p.add_argument('--renderer', choices=['texture', 'opengl'], default='texture')
    p.add_argument('--timeout', type=int, default=100)
    args = p.parse_args()
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
    env = dict(os.environ, SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='x11',
               DISPLAY=':159', DOSBOX_JIT_BLOCK_TRACE=str(out / 'jit-blocks.csv'))
    if args.no_trace:
        env.pop('DOSBOX_JIT_BLOCK_TRACE', None)
    if args.renderer == 'texture':
        env['SDL_RENDER_DRIVER'] = 'software'
    if args.sdk_lib_dir:
        env['LD_LIBRARY_PATH'] = str(args.sdk_lib_dir.resolve()) + ':' + env.get('LD_LIBRARY_PATH', '')
    if args.profiler:
        env.update(DOSBOX_PROFILE_OUTPUT=str(out / 'samples.bin'),
                   DOSBOX_PROFILE_PERIOD_US='5000',
                   LD_PRELOAD=str(args.profiler.resolve()))
    with (out / 'xvfb.log').open('w') as f:
        xvfb = subprocess.Popen(['Xvfb', ':159', '-screen', '0', '1280x720x24',
                                 '-nolisten', 'tcp'],
                                stdout=f, stderr=subprocess.STDOUT,
                                start_new_session=True)
    try:
        for _ in range(70):
            if subprocess.run(['xdpyinfo', '-display', ':159'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Xvfb failed')
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
                                    stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while time.monotonic() - started < args.timeout:
                    qlog = game / 'ID1' / 'QCONSOLE.LOG'
                    if qlog.is_file():
                        contents = qlog.read_bytes().decode('latin1', 'replace')
                        if first_demo is None and 'Playing demo from demo1.dem.' in contents:
                            first_demo = time.monotonic_ns()
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
                  'trace_enabled': not args.no_trace}
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2), flush=True)
        return 0 if res else 1
    finally:
        stop(xvfb)


if __name__ == '__main__':
    raise SystemExit(main())
