#!/usr/bin/env python3
"""Run DOS x87 and paging oracles against normal and dynamic cores.

Requires NASM, an executable fork DOSBox, its runtime resource directory,
and SDL dummy video support. Uses a new work directory; logs stay there.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--dosbox', type=Path, required=True)
p.add_argument('--runtime-cwd', type=Path, required=True)
p.add_argument('--work', type=Path, required=True)
p.add_argument('--nasm', default='nasm')
p.add_argument('--sdk-lib-dir', type=Path)
a = p.parse_args()
binary, runtime, work = a.dosbox.resolve(), a.runtime_cwd.resolve(), a.work.resolve()
assert binary.is_file() and (runtime/'resources/mapping').is_dir()
work.mkdir(parents=True, exist_ok=False)
tests = ['FPU_FLAGS', 'FPU_PF', 'FPU_PF_FIRST', 'FPU_PF_FADD',
         'FPU_PF_FILD', 'FPU_PF_FIADD', 'FPU_USER',
         'FPU_USER_FIRST', 'FPU_USER_FADD']
source = Path(__file__).resolve().parent
env = dict(os.environ, SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy',
           SDL_RENDER_DRIVER='software')
if a.sdk_lib_dir:
    env['LD_LIBRARY_PATH'] = str(a.sdk_lib_dir.resolve()) + ':' + env.get('LD_LIBRARY_PATH', '')
for key in tuple(env):
    if key.startswith(('DOSBOX_JIT_', 'DOSBOX_PROFILE_')) or key == 'LD_PRELOAD':
        env.pop(key)
for name in tests:
    program = work / (name + '.COM')
    subprocess.run([a.nasm, '-f', 'bin', str(source/(name + '.asm')), '-o', str(program)], check=True)
    for core in ('normal', 'dynamic'):
        folder = work / (name + '-' + core)
        folder.mkdir()
        shutil.copy(program, folder/'FPU.COM')
        conf = folder/'dosbox.conf'
        conf.write_text('[cpu]\ncore = '+core+'\ncputype = pentium\ncpu_cycles = 3000\n'
                        'cpu_cycles_protected = max\n[sdl]\noutput = texture\n'
                        'texture_renderer = software\n')
        command = [str(binary), '--noprimaryconf', '--nolocalconf', '--noautoexec',
                   '--conf', str(conf), '-c', f'mount c "{folder}"', '-c', 'c:',
                   '-c', 'FPU.COM > OUT.TXT', '-c', 'exit']
        try:
            result = subprocess.run(command, cwd=runtime, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=60)
        except subprocess.TimeoutExpired as error:
            (folder/'dosbox.log').write_bytes(error.stdout or b'')
            raise RuntimeError(f'{name} {core} timed out; see {folder}') from error
        (folder/'dosbox.log').write_bytes(result.stdout)
        output = (folder/'OUT.TXT').read_text().strip() if (folder/'OUT.TXT').exists() else ''
        print(name, core, 'exit', result.returncode, 'result', repr(output), flush=True)
        if result.returncode or output != 'FPU_ORACLE_PASS':
            raise RuntimeError(f'{name} {core} failed; see {folder}')
