#!/usr/bin/env python3
"""Run the bit-test semantics guest test and report what the dynamic core did.

The test program (`bit_test_semantics.asm`) is a real-mode DOS COM program.
It executes 19 cases: 16 of the bit-test instruction family (`BT`, `BTS`,
`BTR`, `BTC` with a register and with an immediate bit index) on a 32-bit
operand, plus 3 cases of the x87 comparison idiom (`fnstsw ax; sahf`) that
the Quake compiler emits. Every carry flag and operand result is compared
against expectations computed from the Intel SDM, and the raw results are
written to `BTOUT.BIN` through `int 21h`.

The program is executed twice:

  * ``cpu core = normal``   - the interpreter (``core_normal.cpp``)
  * ``cpu core = dynamic``  - the dynamic core (dynrec)

Both runs must produce byte-identical output files, and the in-program
self-check must report zero deviations from the expected values.

When a trace-enabled build is supplied, the run additionally reports

  * how much of the test program the dynamic core actually translated, and
    which guest byte ranges stayed in the interpreter, and
  * the fallback profile of the run (the instrumented build counts every
    runtime fallback into the interpreter, keyed by guest address).

That coverage report is what makes the comparison meaningful: the dynamic
core cannot translate the bit-test opcodes, so those instructions are
expected to be the uncovered ranges.
"""
from __future__ import annotations

import argparse
import csv
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

# one-byte opcodes that the dynamic core does not translate
ONE_BYTE_UNSUPPORTED = {0x9E: 'SAHF', 0x9F: 'LAHF'}


def assemble(asm: Path, com: Path) -> None:
    subprocess.run(['nasm', '-f', 'bin', str(asm), '-o', str(com)],
                   check=True, capture_output=True, text=True)


def run_once(dosbox: Path, workdir: Path, core: str, program: str,
             trace_path: Path | None, counters_path: Path | None,
             timeout: int) -> dict:
    out_file = workdir / 'BTOUT.BIN'
    if out_file.exists():
        out_file.unlink()
    conf = workdir / f'dosbox-{core}.conf'
    conf.write_text(CONF.format(core=core, workdir=workdir, program=program))
    env = dict(os.environ, SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy')
    if trace_path is not None:
        env['DOSBOX_JIT_BLOCK_TRACE'] = str(trace_path)
    if counters_path is not None:
        env['DOSBOX_JIT_FALLBACK_COUNTS'] = str(counters_path)
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


def parse_output(data: bytes) -> dict:
    if len(data) < 4:
        return {'parsed': False}
    n_cases = data[0]
    mismatches = data[1]
    cf = list(data[4:4 + n_cases])
    values = [int.from_bytes(data[23 + 4 * i:27 + 4 * i], 'little')
              for i in range(max(0, n_cases - 3))] if len(data) >= 27 else []
    return {'parsed': True, 'n_cases': n_cases, 'mismatches': mismatches,
            'results': cf,
            'values': [f'0x{v:08x}' for v in values]}


def trace_coverage(trace_path: Path, program_size: int) -> dict:
    """How much of the test program did the dynamic core translate?"""
    rows = list(csv.DictReader(trace_path.open()))
    creates = [r for r in rows if r['event'] == 'C']
    if not creates:
        return {'available': False}
    # The program segment is the one that owns most of the blocks starting at
    # EIP 0x100; the DOS kernel has its own segment with a block at 0x100 too.
    candidates = {r['guest_cs'] for r in creates if int(r['guest_eip'], 16) == 0x100}
    if not candidates:
        return {'available': False}
    counts = {c: sum(1 for r in creates if r['guest_cs'] == c) for c in candidates}
    cs = max(counts, key=lambda c: counts[c])
    blocks = [r for r in creates if r['guest_cs'] == cs]
    base = int(blocks[0]['guest_linear_begin'], 16) - 0x100
    ranges = sorted((int(r['guest_linear_begin'], 16), int(r['guest_linear_end'], 16))
                    for r in blocks)
    merged: list[list[int]] = []
    for lo, hi in ranges:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    covered = sum(hi - lo for lo, hi in merged)
    # guest byte ranges that no block covered (offsets inside the image)
    gaps = []
    prev = base
    for lo, hi in merged:
        if lo > prev:
            gaps.append((prev - base, lo - base))
        prev = max(prev, hi)
    if prev < base + program_size:
        gaps.append((prev - base, base + program_size - base))
    return {
        'available': True,
        'guest_cs': cs,
        'candidate_segments': {c: counts[c] for c in sorted(candidates)},
        'blocks': len(blocks),
        'image_bytes': program_size,
        'translated_bytes': covered,
        'translated_pct': round(100 * covered / program_size, 1),
        'uncovered_ranges': [{'from': f'0x{a:04x}', 'to': f'0x{b:04x}',
                              'bytes': b - a} for a, b in gaps],
    }


def fallback_profile(counters_path: Path) -> dict:
    """Fallback counts recorded by the instrumented build."""
    if not counters_path.exists():
        return {'available': False}
    total = 0
    by_addr: list[tuple[int, int]] = []
    for line in counters_path.read_text().splitlines():
        kind, _, rest = line.partition(',')
        if kind == 'total':
            total = int(rest)
        elif kind == 'addr':
            key, _, cnt = rest.partition(',')
            value = int(key, 16)
            by_addr.append((((value >> 32) & 0xffff), value & 0xffffffff, int(cnt)))
    return {'available': True, 'total': total,
            'by_address': [{'cs': f'0x{cs:04x}', 'eip': f'0x{eip:05x}', 'count': n}
                           for cs, eip, n in sorted(by_addr, key=lambda x: -x[2])]}


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
                         'trace and fallback counters are summarised')
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
                                 None, None, a.timeout)
        (a.out / f'BTOUT-{core}.bin').write_bytes(bytes.fromhex(results[core]['output']))

    coverage = {'available': False}
    fallbacks = {'available': False}
    if a.trace_dosbox is not None:
        trace_file = a.out / 'jit-block-trace.csv'
        counters_file = a.out / 'fallback-counters.csv'
        traced = run_once(a.trace_dosbox.resolve(), workdir, 'dynamic', 'BITSEM.COM',
                          trace_file, counters_file, a.timeout)
        results['dynamic_traced'] = traced
        coverage = trace_coverage(trace_file, com.stat().st_size)
        fallbacks = fallback_profile(counters_file)

    normal, dynamic = results['normal'], results['dynamic']
    identical = (normal['output_present'] and dynamic['output_present']
                 and normal['output_sha256'] == dynamic['output_sha256'])
    summary = {
        'test_program': 'bit_test_semantics.asm',
        'normal': {k: v for k, v in normal.items() if k != 'output'},
        'dynamic': {k: v for k, v in dynamic.items() if k != 'output'},
        'byte_identical': identical,
        'normal_parsed': parse_output(bytes.fromhex(normal['output'])),
        'dynamic_parsed': parse_output(bytes.fromhex(dynamic['output'])),
        'dynamic_core_coverage': coverage,
        'fallback_profile': fallbacks,
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
