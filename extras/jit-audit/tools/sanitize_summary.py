#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reduce an analyze_jit.py summary to publishable derived data.

Keeps counts, guest CS:EIP labels, symbol names and code-size statistics;
drops local paths (DSOs reduced to basenames) and anything address-like.
"""
import json
import sys
from pathlib import Path

d = json.loads(Path(sys.argv[1]).read_text())
n = d['main_tid_samples']
out = {
    'main_thread_samples': n,
    'regions_pct': {k: round(100 * v / n, 2) for k, v in d['regions'].items()},
    'top_symbols': [dict(dso=Path(s['dso']).name, symbol=s['symbol'], samples=s['samples'],
                         pct=round(100 * s['samples'] / n, 2)) for s in d['top_symbols']],
    'jit_self_instruction_class_at_ip': d['jit_self_instruction_class_at_ip'],
    'jit_self_instruction_class_before_ip': d['jit_self_instruction_class_before_ip'],
    'helper_samples_attributed_to_caller_block': d['helper_samples_attributed_to_caller_block'],
    'helper_to_caller_block': d['helper_to_caller'],
    'top_blocks': [{k: v for k, v in b.items() if k != 'generation'} for b in d['top_blocks']],
    'static_code': d['static'],
}
for b in out['top_blocks']:
    b['pct_total'] = round(100 * b['samples_total'] / n, 2)
print(json.dumps(out, indent=1))
