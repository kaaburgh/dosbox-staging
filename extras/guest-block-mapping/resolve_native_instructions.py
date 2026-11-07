#!/usr/bin/env python3
"""Join generation-specific native RIP offsets to *linear* x86-64 disassembly.

Input is output of analyze_native_rips.py and the matching private snapshots.
Neither native executable bytes nor absolute host addresses appear in output.
This is a decoder cross-check, not proof that a linear-sweep interpretation is
reachable (JIT blocks may contain interleaved data and slow/exception paths).
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess

HEX_BYTES = re.compile(r'^\s*([0-9a-fA-F]+):\s+((?:[0-9a-fA-F]{2}\s+)+)(.*?)\s*$')
NUMBER = re.compile(r'(?<![A-Za-z_])(?:0x[0-9a-fA-F]+|\d+)(?![A-Za-z_0-9])')


def classify_mnemonic(mnemonic: str) -> str:
    m = mnemonic.lower()
    if m.startswith('call'):
        return 'call-site'
    if m.startswith('j') or m.startswith('loop'):
        return 'branch'
    if m in ('ret', 'retq', 'iret', 'iretd', 'iretq'):
        return 'return'
    if m.startswith(('mov', 'lea', 'cmov', 'push', 'pop')):
        return 'move-or-address'
    if m.startswith(('cmp', 'test', 'bt')):
        return 'predicate'
    if m.startswith(('add', 'sub', 'adc', 'sbb', 'shr', 'shl', 'sar', 'and', 'or', 'xor', 'inc', 'dec', 'imul', 'mul', 'div', 'idiv')):
        return 'arithmetic-or-bitwise'
    return 'other'


def load_disassembly(path: Path, objdump: str = 'objdump') -> dict[int, dict]:
    """Return a linear sweep, requiring each emitted byte to be represented.

    Non-start RIPs are deliberately not attributed to a preceding instruction:
    alternate decode entry points are possible and must be reviewed manually.
    """
    native = path.read_bytes()
    if not native:
        raise ValueError('empty native JIT blob')
    proc = subprocess.run(
        [objdump, '-D', '-z', '-b', 'binary', '-m', 'i386:x86-64',
         '-M', 'intel', '--insn-width=16', '--adjust-vma=0', str(path)],
        text=True, capture_output=True, check=True, timeout=15)
    instructions = {}
    coverage = bytearray(len(native))
    for line in proc.stdout.splitlines():
        match = HEX_BYTES.match(line)
        if match is None:
            continue
        address = int(match.group(1), 16)
        raw = bytes.fromhex(match.group(2))
        text = match.group(3).strip()
        if not text:
            raise ValueError(f'missing mnemonic at offset {address}')
        end = address + len(raw)
        if end > len(native) or any(coverage[address:end]):
            raise ValueError(f'disassembler emitted overlapping/out-of-range instruction at {address}')
        if native[address:end] != raw:
            raise ValueError(f'disassembler byte mismatch at {address}')
        coverage[address:end] = b'\x01' * len(raw)
        mnemonic, _, operands = text.partition(' ')
        mnemonic = mnemonic.strip()
        operands = operands.strip()
        # Obfuscate embedded process addresses, code targets and other immediates.
        operands = NUMBER.sub('IMM', operands)
        instructions[address] = {
            'offset': address, 'length': len(raw), 'mnemonic': mnemonic,
            'operand_shape': operands, 'family': classify_mnemonic(mnemonic),
        }
    if len(instructions) == 0 or not all(coverage):
        raise ValueError('native bytes not fully covered by linear disassembly')
    return instructions


def _safe_filename(directory: Path, name: object) -> Path:
    if not isinstance(name, str) or not name.startswith('jit-') or Path(name).name != name or not name.endswith('.host.bin'):
        raise ValueError(f'invalid native snapshot filename {name!r}')
    result = directory / name
    if result.is_symlink():
        raise ValueError('symlink native snapshot forbidden')
    return result


def resolve(summary: dict, samples: list[dict], snapshot_dir: Path,
            *, objdump: str = 'objdump') -> tuple[dict, list[dict]]:
    blocks = summary.get('blocks')
    if not isinstance(blocks, list) or len(blocks) != summary.get('selected_generations'):
        raise ValueError('selected block summary missing or inconsistent')
    expected = {}
    for item in blocks:
        gen = item.get('generation')
        if not isinstance(gen, int) or gen in expected:
            raise ValueError('duplicate or invalid generation in summary')
        expected[gen] = item
    files = sorted(snapshot_dir.glob('jit-*.json'))
    snapshot_meta = {}
    for file in files:
        data = json.loads(file.read_text())
        gen = data.get('generation')
        if not isinstance(gen, int) or gen in snapshot_meta:
            raise ValueError('duplicate or invalid generation in snapshots')
        snapshot_meta[gen] = data
    if set(snapshot_meta) != set(expected):
        raise ValueError('snapshot generations differ from analyzer summary')
    by_generation = {}
    for gen, block in expected.items():
        meta = snapshot_meta[gen]
        file = _safe_filename(snapshot_dir, meta.get('host_filename'))
        if (int(meta['guest_eip'], 0) != int(block['guest_eip'], 0) or
                meta['host_bytes'] != block['native_bytes'] or
                meta.get('schema') != 1):
            raise ValueError('snapshot metadata differs from selected block summary')
        native = file.read_bytes()
        if (len(native) != block['native_bytes'] or
                hashlib.sha256(native).hexdigest() != block['host_sha256']):
            raise ValueError('snapshot content hash differs from original analysis')
        by_generation[gen] = load_disassembly(file, objdump)
    hits = Counter()
    starts = Counter()
    unmatched = Counter()
    for row in samples:
        gen, offset, count = int(row['generation']), int(row['offset']), int(row['samples'])
        if gen not in by_generation or offset < 0 or count < 1 or offset >= expected[gen]['native_bytes']:
            raise ValueError(f'RIP row contains unknown generation or invalid sample offset {row!r}')
        hits[gen] += count
        if offset in by_generation[gen]:
            starts[(gen, offset)] += count
        else:
            unmatched[gen] += count
    if (sum(hits.values()) != summary.get('selected_snapshot_samples') or
            any(hits[gen] != block['samples'] for gen, block in expected.items())):
        raise ValueError('exact RIP counts do not match original analysis summary')
    result = []
    families = Counter()
    for (gen, offset), count in sorted(starts.items()):
        ins = by_generation[gen][offset]
        result.append({'generation': gen, 'guest_eip': expected[gen]['guest_eip'],
                       **ins, 'samples': count})
        families[ins['family']] += count
    metrics = {
        'interpretation': 'linear-disassembly start offsets ONLY; not verified execution paths or guest instructions',
        'total_selected_samples': sum(hits.values()),
        'at_linear_instruction_start': sum(starts.values()),
        'not_at_linear_instruction_start': sum(unmatched.values()),
        'by_family': dict(sorted(families.items())),
        'unmatched_by_generation': {str(g): n for g, n in sorted(unmatched.items())},
        'caution': 'Unmatched RIPs can be alternate entry points / embedded data / decode errors; do not silently assign them to a preceding instruction',
    }
    return metrics, result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summary', type=Path, required=True,
                   help='summary.json from the matching analyze_native_rips.py run')
    p.add_argument('--rips', type=Path, required=True,
                   help='native-rips.csv from the same run')
    p.add_argument('--snapshots', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True, help='new output directory')
    p.add_argument('--objdump', default='objdump')
    a = p.parse_args()
    if a.out.exists():
        p.error('output must be a new directory')
    with a.rips.open(newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ['generation', 'offset', 'samples']:
            raise ValueError('unexpected native-rips.csv schema')
        samples = list(reader)
    summary, rows = resolve(json.loads(a.summary.read_text()), samples, a.snapshots, objdump=a.objdump)
    a.out.mkdir(parents=True, mode=0o700)
    (a.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    with (a.out / 'sampled-instructions.csv').open('w', newline='') as f:
        columns = ['generation', 'guest_eip', 'offset', 'length', 'mnemonic', 'operand_shape', 'family', 'samples']
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
