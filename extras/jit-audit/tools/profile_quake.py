#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Profile only the Quake 1.06 timedemo window with Linux perf.

DOSBox is started directly; `perf record -p <pid>` is attached when Quake logs
"Playing demo from" and stopped (SIGINT) when the timedemo result appears, so
loading/startup is excluded. Use a DOSBox build with DOSBOX_JIT_PERF_MAP so
`perf report` can name JIT blocks via /tmp/perf-<pid>.map.

All outputs go to --private (perf data contains raw addresses; keep private).
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

FPS_RE = re.compile(rb'(\d+)\s+frames\s+([\d.]+)\s+seconds\s+([\d.]+)\s+fps', re.I)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dosbox', type=Path, required=True)
    p.add_argument('--game', type=Path, required=True)
    p.add_argument('--runtime-cwd', type=Path, required=True)
    p.add_argument('--private', type=Path, required=True)
    p.add_argument('--cycles', default='max')
    p.add_argument('--freq', type=int, default=1999)
    p.add_argument('--callgraph', default='dwarf,2048')
    p.add_argument('--dump', action='store_true', help='also write JIT block dump')
    a = p.parse_args()
    a.private.mkdir(parents=True, exist_ok=False)
    game = a.private / 'game'
    shutil.copytree(a.game, game)
    conf = a.private / 'dosbox.conf'
    conf.write_text('[dosbox]\nmemsize = 32\n[cpu]\ncore = dynamic\ncputype = pentium\n'
                    f'cpu_cycles = 3000\ncpu_cycles_protected = {a.cycles}\n'
                    '[sdl]\noutput = texture\ntexture_renderer = software\n')
    env = dict(os.environ, SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy',
               SDL_RENDER_DRIVER='software', DOSBOX_JIT_PERF_MAP='1')
    if a.dump:
        env['DOSBOX_JIT_DUMP'] = str(a.private / 'jit.dump')
    cmd = [str(a.dosbox), '--noprimaryconf', '--nolocalconf', '--noautoexec',
           '--conf', str(conf), '-c', f'mount c "{game}"', '-c', 'c:',
           '-c', 'quake.exe -nosound -nocdaudio -condebug -width 320 -height 200 +timedemo demo1']
    dosbox = subprocess.Popen(cmd, cwd=a.runtime_cwd, env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, start_new_session=True)
    qlog = game / 'ID1' / 'QCONSOLE.LOG'
    perf = None
    info = {'pid': dosbox.pid}
    try:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            data = qlog.read_bytes() if qlog.is_file() else b''
            if perf is None and b'Playing demo from' in data:
                info['demo_start_ns'] = time.monotonic_ns()
                perf = subprocess.Popen(['perf', 'record', '-q', '-F', str(a.freq),
                                         '--clockid', 'monotonic', '-g',
                                         '--call-graph', a.callgraph,
                                         '-o', str(a.private / 'perf.data'),
                                         '-p', str(dosbox.pid)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            m = FPS_RE.findall(data)
            if m:
                info['demo_end_ns'] = time.monotonic_ns()
                info['frames'], info['guest_seconds'], info['fps'] = (
                    int(m[-1][0]), float(m[-1][1]), float(m[-1][2]))
                break
            if dosbox.poll() is not None:
                info['early_exit'] = dosbox.returncode
                break
            time.sleep(0.005)
    finally:
        if perf is not None:
            perf.send_signal(signal.SIGINT)
            perf.wait(timeout=120)
        if dosbox.poll() is None:
            os.killpg(dosbox.pid, signal.SIGKILL)
        dosbox.wait()
    map_path = Path(f'/tmp/perf-{dosbox.pid}.map')
    info['perf_map'] = str(map_path)
    (a.private / 'info.json').write_text(json.dumps(info, indent=2) + '\n')
    shutil.rmtree(game)
    print(json.dumps(info))


if __name__ == '__main__':
    main()
