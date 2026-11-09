#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Summarise bench_quake.py output.

For each metric, reports per-label median and coefficient of variation, and
for each label vs --reference the geometric-mean within-block ratio with a
percentile bootstrap over blocks (descriptive, assumes independent blocks).
Lower-is-better metrics (CPU seconds) are reported as reference/label - 1 so
that positive numbers mean "label is faster" for every metric.
"""
import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

METRICS = {'demo_cpu_s': 'lower', 'main_thread_cpu_s': 'lower', 'fps': 'higher',
           'wall_to_result_s': 'lower'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('csv', type=Path)
    p.add_argument('--reference', default='base')
    p.add_argument('--boot', type=int, default=20000)
    a = p.parse_args()
    rows = [r for r in csv.DictReader(a.csv.open()) if r['warmup'] == '0']
    bad = [r for r in rows if r['status'] != 'ok' or r['frames'] != '969']
    labels = sorted({r['label'] for r in rows})
    nlab = len(labels)
    # rows are written in schedule order; consecutive groups of nlab form a block
    blocks = [rows[i:i + nlab] for i in range(0, len(rows), nlab)]
    out = {'runs': len(rows), 'incomplete': len(bad), 'labels': labels, 'metrics': {}}
    guest = defaultdict(set)
    for r in rows:
        guest[r['label']].add(r['guest_seconds'])
    out['distinct_guest_seconds_per_label'] = {k: sorted(v) for k, v in guest.items()}
    rng = random.Random(1)
    for m, direction in METRICS.items():
        per = defaultdict(list)
        for r in rows:
            if r[m] not in ('', None):
                per[r['label']].append(float(r[m]))
        if not per:
            continue
        stats = {}
        for l, v in per.items():
            mean = statistics.fmean(v)
            stats[l] = {'n': len(v), 'median': statistics.median(v),
                        'cv_pct': 100 * statistics.pstdev(v) / mean if mean else None,
                        'min': min(v), 'max': max(v)}
        comps = {}
        for l in labels:
            if l == a.reference:
                continue
            logs = []
            for b in blocks:
                ref = [float(r[m]) for r in b if r['label'] == a.reference and r[m]]
                cur = [float(r[m]) for r in b if r['label'] == l and r[m]]
                if ref and cur:
                    ratio = ref[0] / cur[0] if direction == 'lower' else cur[0] / ref[0]
                    logs.append(math.log(ratio))
            if not logs:
                continue
            est = math.exp(statistics.fmean(logs)) - 1
            boots = []
            for _ in range(a.boot):
                s = [rng.choice(logs) for _ in logs]
                boots.append(math.exp(statistics.fmean(s)) - 1)
            boots.sort()
            comps[l] = {'speedup_pct': 100 * est,
                        'boot95_pct': [100 * boots[int(0.025 * a.boot)], 100 * boots[int(0.975 * a.boot)]],
                        'blocks': len(logs),
                        'per_block_pct': [round(100 * (math.exp(x) - 1), 2) for x in logs]}
        out['metrics'][m] = {'direction': direction, 'per_label': stats, f'vs_{a.reference}': comps}
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
