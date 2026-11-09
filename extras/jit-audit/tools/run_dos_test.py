#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run one DOS .COM test headlessly in DOSBox Staging and print its stdout.

The program's DOS stdout is redirected to OUT.TXT inside a fresh mounted
directory; the file is printed verbatim. Exit status is non-zero when DOSBox
times out or OUT.TXT is missing.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONF = """[dosbox]
memsize = 32
[cpu]
core = {core}
cputype = {cputype}
cpu_cycles = {cycles}
cpu_cycles_protected = {cycles}
[sdl]
output = texture
texture_renderer = software
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dosbox', required=True, type=Path)
    p.add_argument('--com', required=True, type=Path)
    p.add_argument('--core', default='dynamic')
    p.add_argument('--cputype', default='pentium')
    p.add_argument('--cycles', default='fixed 50000')
    p.add_argument('--runtime-cwd', required=True, type=Path)
    p.add_argument('--timeout', type=int, default=60)
    p.add_argument('--keep', type=Path, help='copy DOSBox log here')
    a = p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        drive = tmp / 'c'
        drive.mkdir()
        name = a.com.name.upper()
        shutil.copy(a.com, drive / name)
        conf = tmp / 'test.conf'
        conf.write_text(CONF.format(core=a.core, cputype=a.cputype, cycles=a.cycles))
        cmd = [str(a.dosbox.resolve()), '--noprimaryconf', '--nolocalconf',
               '--noautoexec', '--conf', str(conf), '-c', f'mount c "{drive}"',
               '-c', 'c:', '-c', f'{name} > OUT.TXT', '-c', 'exit']
        env = dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy',
                   SDL_RENDER_DRIVER='software')
        try:
            proc = subprocess.run(cmd, cwd=a.runtime_cwd, env=env, timeout=a.timeout,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except subprocess.TimeoutExpired:
            print('TIMEOUT')
            return 2
        if a.keep:
            a.keep.write_bytes(proc.stdout)
        out = drive / 'OUT.TXT'
        if not out.exists():
            print('NO_OUTPUT')
            return 3
        sys.stdout.write(out.read_bytes().decode('latin1').replace('\r', ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
