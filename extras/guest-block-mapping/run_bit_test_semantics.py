#!/usr/bin/env python3
"""Run the BT/BTS/BTR/BTC guest semantics test on both CPU cores.

The test program (`bit_test_semantics.asm`) is a real-mode DOS COM program.
It executes 16 cases of the bit-test instruction family on a 32-bit operand,
compares every result against expectations computed from the Intel SDM, and
writes the raw CF and operand values to `BTOUT.BIN` through int 21h.

The program is executed twice:

  * ``cpu core = normal``   - the interpreter (``core_normal.cpp``)
  * ``cpu core = dynamic``  - the dynamic core (dynrec)

Both runs must produce byte-identical output files, and the in-program
self-check must report zero deviations from the expected values. The
interpreter run is the reference: the dynamic core currently cannot
translate the bit-test opcodes and hands them to the interpreter through
the ``BR_Opcode`` path.

Optionally a trace-enabled build is run as well; the resulting JIT block
trace shows that the dynamic core stops its blocks exactly at the
bit-test instructions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

CONF = """[dosbox]
memsize = 32
[cpu]
core = {core}
cycles = 100000
[sdl]
output = surface
autolock = false
[mixer]
nosound = true
[autoexec]
mount c "{workdir}"
c:
{program}
exit
"""


def assemble(asm: Path, com: Path) -> None:
    subprocess.run(['nasm', '-f', 'bin', str(asm), '-o', str(com)],
                   check=True, capture_output=True, text=True)


def run_once(dosbox: Path, workdir: Path, core: str, program: str,
             trace_path: Path | None, timeout: int) -> dict:
    out_file = workdir / 'BTOUT.BIN'
    if out_file.exists():
        out_file.unlink()
    conf = workdir / f'dosbox-{core}.conf'
    conf.write_text(CONF.format(core=core, workdir=workdir, program=program))
    env = dict(os.environ, SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy')
    if trace_path is not None:
        env['DOSBOX_JIT_BLOCK_TRACE'] = str(trace_path)
    proc = subprocess.run(
        [str(dosbox), '--noprimaryconf', '--nolocalconf', '--conf', str(conf)],
        env=env, capture_output=True, text=True, timeout=timeout)
    data = out_file.read_bytes() if out_file.exists() else b''
    return {
        'core': core,
        'returncode': proc.returncode,
        'output_present': bool(data),
        'output_bytes': len(data),
        'output_sha256': hashlib.sha256(data).hexdigest() if data else None,
        'output': data.hex(),
        'stderr_tail': proc.stderr[-400:],
    }


def parse(data: bytes) -> dict:
    if len(data) < 4:
        return {'parsed': False}
    n_cases = data[0]
    mismatches = data[1]
    cf = list(data[4:4 + n_cases])
    values = [int.from_bytes(data[20 + 4 * i:24 + 4 * i], 'little')
              for i in range(n_cases)] if len(data) >= 20 + 4 * n_cases else []
    return {'parsed': True, 'n_cases': n_cases, 'mismatches': mismatches,
            'cf': cf, 'values': [f'0x{v:08x}' for v in values]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dosbox', type=Path, required=True,
                    help='DOSBox Staging binary to execute')
    ap.add_argument('--asm', type=Path,
                    default=Path(__file__).resolve().parent / 'bit_test_semantics.asm')
    ap.add_argument('--out', type=Path, required=True,
                    help='directory for the summary and the raw outputs')
    ap.add_argument('--workdir', type=Path, default=None,
                    help='DOS-visible working directory (default: <out>/work)')
    ap.add_argument('--trace-dosbox', type=Path, default=None,
                    help='optional trace-enabled DOSBox build; its JIT block '
                         'trace is written next to the summary')
    ap.add_argument('--timeout', type=int, default=180)
    a = ap.parse_args()

    a.out.mkdir(parents=True, exist_ok=True)
    workdir = a.workdir or (a.out / 'work')
    workdir.mkdir(parents=True, exist_ok=True)

    com = workdir / 'BITSEM.COM'
    assemble(a.asm, com)

    results = {}
    for core in ('normal', 'dynamic'):
        results[core] = run_once(a.dosbox.resolve(), workdir, core, 'BITSEM.COM',
                                 None, a.timeout)
        (a.out / f'BTOUT-{core}.bin').write_bytes(bytes.fromhex(results[core]['output']))

    trace_file = None
    if a.trace_dosbox is not None:
        trace_file = a.out / 'jit-block-trace.csv'
        traced = run_once(a.trace_dosbox.resolve(), workdir, 'dynamic', 'BITSEM.COM',
                          trace_file, a.timeout)
        results['dynamic_traced'] = traced

    normal, dynamic = results['normal'], results['dynamic']
    identical = (normal['output_present'] and dynamic['output_present']
                 and normal['output_sha256'] == dynamic['output_sha256'])
    summary = {
        'test_program': 'bit_test_semantics.asm',
        'normal': {k: v for k, v in normal.items() if k != 'output'},
        'dynamic': {k: v for k, v in dynamic.items() if k != 'output'},
        'byte_identical': identical,
        'normal_parsed': parse(bytes.fromhex(normal['output'])),
        'dynamic_parsed': parse(bytes.fromhex(dynamic['output'])),
    }
    if 'dynamic_traced' in results:
        summary['dynamic_traced'] = {
            k: v for k, v in results['dynamic_traced'].items() if k != 'output'}
    summary['verdict'] = (
        'PASS: both cores produce byte-identical results and the in-program '
        'self-check reports zero deviations from the Intel SDM expectations'
        if identical and summary['normal_parsed'].get('mismatches') == 0
        else 'FAIL: cores disagree or the self-check found deviations')
    (a.out / 'bit-test-summary.json').write_text(
        json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
