#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact event counts for the Quake 1.06 timedemo window (JitCounters build).

Runs a DOSBox built with instrumentation/apply_jit_counters.py, sends SIGUSR1
when Quake logs "Playing demo from" and again when the timedemo result
appears, and prints the per-counter difference. Use fixed cycles above the
sustainable rate (lag mode) so the guest work in the window is deterministic.
"""
import argparse
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import time
from pathlib import Path

NAMES = ['pushfq_exec', 'popfq_exec', 'push_helper_calls', 'pop_helper_calls',
         'fpu_fld32_calls', 'fpu_fst32_calls', 'fpu_fld64_calls', 'dispatch_loop',
         'makecodepage_calls', 'run_block_from_dispatcher', 'normal_core_runs',
         'blocks_created', 'read_word_slow_path', 'write_word_slow_path']
FPS_RE = re.compile(rb'(\d+)\s+frames\s+([\d.]+)\s+seconds\s+([\d.]+)\s+fps', re.I)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dosbox', type=Path, required=True)
    p.add_argument('--game', type=Path, required=True)
    p.add_argument('--runtime-cwd', type=Path, required=True)
    p.add_argument('--cycles', default='1000000')
    a = p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        game = tmp / 'game'
        shutil.copytree(a.game, game)
        conf = tmp / 'd.conf'
        conf.write_text('[dosbox]\nmemsize = 32\n[cpu]\ncore = dynamic\ncputype = pentium\n'
                        f'cpu_cycles = 3000\ncpu_cycles_protected = {a.cycles}\n'
                        '[sdl]\noutput = texture\ntexture_renderer = software\n')
        counters = tmp / 'counters.bin'
        env = dict(os.environ, SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy',
                   SDL_RENDER_DRIVER='software', DOSBOX_JIT_COUNTERS=str(counters))
        cmd = [str(a.dosbox), '--noprimaryconf', '--nolocalconf', '--noautoexec',
               '--conf', str(conf), '-c', f'mount c "{game}"', '-c', 'c:',
               '-c', 'quake.exe -nosound -nocdaudio -condebug -width 320 -height 200 +timedemo demo1']
        proc = subprocess.Popen(cmd, cwd=a.runtime_cwd, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        qlog = game / 'ID1' / 'QCONSOLE.LOG'
        started = False
        result = None
        try:
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline and proc.poll() is None:
                data = qlog.read_bytes() if qlog.is_file() else b''
                if not started and b'Playing demo from' in data:
                    os.kill(proc.pid, signal.SIGUSR1)
                    started = True
                m = FPS_RE.findall(data)
                if m:
                    os.kill(proc.pid, signal.SIGUSR1)
                    result = m[-1]
                    break
                time.sleep(0.002)
            time.sleep(0.2)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        raw = counters.read_bytes()
    size = 8 * len(NAMES)
    snaps = [struct.unpack_from(f'<{len(NAMES)}Q', raw, i) for i in range(0, len(raw) - size + 1, size)]
    if len(snaps) < 2 or result is None:
        raise SystemExit(f'incomplete: {len(snaps)} snapshots, result={result}')
    delta = {n: snaps[1][i] - snaps[0][i] for i, n in enumerate(NAMES)}
    out = {'frames': int(result[0]), 'guest_seconds': float(result[1]), 'fps': float(result[2]),
           'window_counts': delta,
           'per_frame': {n: round(v / int(result[0]), 1) for n, v in delta.items()}}
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
