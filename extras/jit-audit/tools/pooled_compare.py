#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pool several bench_quake.py series and compare labels per block.

Each block's reference is the geometric mean of the reference labels in that
block (e.g. two identical-binary labels base and base2). Reports, per label,
the geometric-mean speedup over blocks with a percentile bootstrap, for each
series separately and pooled. Metric is lower-is-better (CPU seconds).
"""
import argparse
import csv
import json
import math
import random
import statistics
import sys
from pathlib import Path


def blocks_of(path):
    rows = [r for r in csv.DictReader(open(path)) if r['warmup'] == '0']
    labels = sorted({r['label'] for r in rows})
    blocks = [rows[i:i + len(labels)] for i in range(0, len(rows), len(labels))]
    # The comparison assumes identical guest work per label. Drop blocks in
    # which a run's guest result differs from that label's usual result.
    usual = {}
    for lab in labels:
        results = [(r['frames'], r['guest_seconds'], r['fps']) for r in rows
                   if r['label'] == lab]
        usual[lab] = max(set(results), key=results.count)
    kept = []
    for b in blocks:
        odd = [r for r in b if (r['frames'], r['guest_seconds'], r['fps']) != usual[r['label']]]
        if odd:
            for r in odd:
                print(f"{path}: dropping block with run {r['number']} ({r['label']}: "
                      f"{r['frames']} frames, {r['guest_seconds']} s, {r['fps']} fps)",
                      file=sys.stderr)
        else:
            kept.append(b)
    return kept, labels


def analyse(blocks, labels, refs, metric, rng, boot=20000):
    out = {}
    for lab in labels:
        logs = []
        for b in blocks:
            ref = [math.log(float(r[metric])) for r in b if r['label'] in refs]
            cur = [math.log(float(r[metric])) for r in b if r['label'] == lab]
            if ref and cur and len(ref) == len(refs):
                logs.append(statistics.fmean(ref) - cur[0])
        est = math.exp(statistics.fmean(logs)) - 1
        bs = sorted(math.exp(statistics.fmean([rng.choice(logs) for _ in logs])) - 1
                    for _ in range(boot))
        out[lab] = {'speedup_pct': round(100 * est, 2),
                    'boot95_pct': [round(100 * bs[int(0.025 * boot)], 2),
                                   round(100 * bs[int(0.975 * boot)], 2)],
                    'blocks': len(logs)}
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('csv', nargs='+')
    p.add_argument('--ref', nargs='+', default=['base', 'base2'])
    p.add_argument('--metric', default='demo_cpu_s')
    a = p.parse_args()
    rng = random.Random(7)
    result, pooled = {}, []
    labels = None
    for path in a.csv:
        b, labels = blocks_of(path)
        pooled += b
        result[Path(path).name] = analyse(b, labels, a.ref, a.metric, rng)
    result['pooled'] = analyse(pooled, labels, a.ref, a.metric, rng)
    print(json.dumps(result, indent=1))


if __name__ == '__main__':
    main()
