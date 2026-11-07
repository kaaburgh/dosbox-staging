#!/usr/bin/env python3
"""Report derived native RIP evidence only, never game or native code bytes."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

H1 = {0x11216, 0x112cc, 0x11343, 0x1143f}
H2 = {0x10d3b}
H3 = {0x3e233, 0x3e29a, 0x3e3ef, 0x3e455}


def report(root: Path) -> dict:
    entries = []
    for folder in sorted(root.glob('run-*')):
        if not (folder / 'derived/summary.json').exists():
            continue
        run = json.loads((folder / 'result.json').read_text())
        block = json.loads((folder / 'derived/summary.json').read_text())
        insn = json.loads((folder / 'instructions/summary.json').read_text())
        if run['result'] is None or run['result']['frames'] != 969:
            raise ValueError(f'{folder}: incomplete timedemo')
        by_group = Counter()
        for item in block['blocks']:
            eip = int(item['guest_eip'], 0)
            group = 'H1' if eip in H1 else 'H2' if eip in H2 else 'H3' if eip in H3 else 'other'
            by_group[group] += item['samples']
        if by_group['other']:
            raise ValueError(f'{folder}: unexpected captured guest EIP')
        if block['selected_generations'] != 9:
            raise ValueError(f'{folder}: expected exactly nine selected generations')
        if sum(by_group.values()) != block['selected_snapshot_samples']:
            raise ValueError(f'{folder}: inconsistent selected sample count')
        if insn['total_selected_samples'] != block['selected_snapshot_samples']:
            raise ValueError(f'{folder}: instruction count mismatch')
        if (insn['at_linear_instruction_start'] +
            insn['not_at_linear_instruction_start'] != insn['total_selected_samples']):
            raise ValueError(f'{folder}: incomplete native instruction classification')
        with (folder / 'derived/native-rips.csv').open(newline='') as f:
            rips = list(csv.DictReader(f))
        tops = sorted(rips, key=lambda row: int(row['samples']), reverse=True)[:12]
        entries.append({
            'period_us': run['profile_period_us'],
            'frames': run['result']['frames'],
            'fps_diagnostic_only': run['result']['fps'],
            'main_thread_samples': block['main_thread_samples'],
            'jit_samples': block['all_jit_samples'],
            'non_jit_samples_unattributed': block['non_jit_samples_unattributed'],
            'selected_samples': block['selected_snapshot_samples'],
            'by_hypothesis_block_entry': dict(by_group),
            'linear_instruction_start_matches': insn['at_linear_instruction_start'],
            'ambiguous_linear_disassembly_samples': insn['not_at_linear_instruction_start'],
            'sampled_instruction_families_syntactic_only': insn['by_family'],
            'top_native_offsets': [
                {'guest_eip': next(b['guest_eip'] for b in block['blocks']
                                   if b['generation'] == int(x['generation'])),
                 'generation': int(x['generation']),
                 'offset': int(x['offset']), 'samples': int(x['samples'])}
                for x in tops
            ],
        })
    if len(entries) < 2:
        raise ValueError('at least two independent Quake profiles required')
    return {
        'schema': 'quake106.native-rip-evidence/v1',
        'runs': entries,
        'qualifications': [
            'Sampling period and host machine differ from prior local measurements',
            'FPS is diagnostic only, not an A/B speedup measurement',
            'Sample groups count only selected entry-block native JIT RIPs; not entire function shares',
            'Static helper sites are not executed helper counts',
            'Non-JIT samples remain unattributed to guest functions',
            'Linear disassembly is not a verified reachable CFG',
            'Native addresses/code/game data excluded from report',
        ],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    result = report(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(f"Verified {len(result['runs'])} original Quake demo1 runs")


if __name__ == '__main__':
    main()
