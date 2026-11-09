#!/usr/bin/env python3
"""Analyze repeated Quake 1.06 A/A timedemos; no benchmark assets needed.

A and B are the SAME executable with ABBA labels per quartet.
Quartet-bootstrap intervals are descriptive only: cross-quartet host load,
autocorrelation, and runner-to-runner variation can invalidate formal CIs.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import random
from statistics import mean, median, pstdev


def pct(values: list[float], q: float) -> float:
    if not values or not 0 <= q <= 1:
        raise ValueError('invalid percentile')
    a = sorted(values)
    p = (len(a) - 1) * q
    low = int(p)
    hi = min(low + 1, len(a) - 1)
    return a[low] * (hi - p) + a[hi] * (p - low)


def pearson_lag1(values: list[float]) -> float | None:
    x, y = values[:-1], values[1:]
    if len(x) < 3:
        return None
    mx, my = mean(x), mean(y)
    v1 = sum((v-mx)**2 for v in x)
    v2 = sum((v-my)**2 for v in y)
    return sum((a-mx)*(b-my) for a,b in zip(x,y)) / math.sqrt(v1*v2) if v1*v2 > 0 else None


def analyze(rows: list[dict], manifest: dict, *, bootstrap_reps: int = 10000) -> dict:
    warmups = int(manifest['warmups'])
    target = int(manifest['measured_repetitions'])
    if manifest.get('A_equals_B') is not True or manifest.get('profiled') is not False or manifest.get('trace_compiled_out') is not True:
        raise ValueError('not an uninstrumented identical-binary A/A experiment')
    if target < 8 or target % 4 or warmups < 2 or len(rows) != warmups + target:
        raise ValueError('run count inconsistent with A/A quartets')
    samples = []
    for i, row in enumerate(rows):
        if int(row['number']) != i + 1 or row['status'] != 'ok' or int(row['frames']) != 969:
            raise ValueError(f'invalid or missing timedemo row {i+1}')
        expected_label = 'W' if i < warmups else ['A','B','B','A'][(i-warmups)%4]
        if row['label'] != expected_label or int(row['warmup']) != int(i < warmups):
            raise ValueError(f'broken warmup/quartet order at {i+1}')
        fps = float(row['fps'])
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('invalid FPS')
        samples.append(fps)
    measured = samples[warmups:]
    logs = [math.log(x) for x in measured]
    fmed = median(measured)
    quartets = [logs[i:i+4] for i in range(0, len(logs), 4)]
    differences = [(q[0] + q[3] - q[1] - q[2]) / 2 for q in quartets]
    observed = math.expm1(mean(differences)) * 100
    r = random.Random(20261109)
    boot = []
    for _ in range(bootstrap_reps):
        subset = [differences[r.randrange(len(differences))] for _ in differences]
        boot.append(math.expm1(mean(subset)) * 100)
    low, high = pct(boot, 0.025), pct(boot, 0.975)
    half1 = median(measured[:len(measured)//2])
    half2 = median(measured[len(measured)//2:])
    mad = median([abs(x-fmed) for x in measured])
    fastest, slowest = max(measured), min(measured)
    a = [measured[i] for i in range(0,len(measured)) if i % 4 in (0,3)]
    b = [measured[i] for i in range(0,len(measured)) if i % 4 in (1,2)]
    throttling = [int(row['cgroup_throttled_events']) for row in rows[warmups:] if row['cgroup_throttled_events'] != '']
    throttle_ms = [float(row['cgroup_throttled_ms']) for row in rows[warmups:] if row['cgroup_throttled_ms'] != '']
    p10, p90 = pct(measured, .1), pct(measured, .9)
    spread = 100 * (p90-p10) / fmed
    drift = 100 * (half2/half1-1)
    aa_interval_halfwidth = max(abs(low-observed),abs(high-observed))
    # Exploratory gates, not a guarantee that a future A/B is significant.
    return {
        'experiment': 'same-binary uninstrumented Quake 1.06 A/A ABBA',
        'source_sha': manifest.get('source_sha'),
        'dosbox_sha256': manifest['dosbox_sha256'],
        'warmups_excluded': warmups,
        'measured_runs': target,
        'completed_frames_per_run': 969,
        'fps': {
            'median': round(fmed,3), 'mean': round(mean(measured),3),
            'min': slowest, 'max': fastest,
            'p10': round(p10,3), 'p90': round(p90,3),
            'p10_p90_spread_pct_of_median': round(spread,3),
            'population_stdev': round(pstdev(measured),3),
            'coefficient_of_variation_pct': round(100*pstdev(measured)/mean(measured),3),
            'median_absolute_deviation_pct': round(100*mad/fmed,3),
            'first_half_median': round(half1,3),
            'second_half_median': round(half2,3),
            'second_vs_first_half_pct': round(drift,3),
            'lag1_autocorrelation_log_fps': None if pearson_lag1(logs) is None else round(pearson_lag1(logs),3),
            'equivalent_timedemo_seconds_at_median_fps': round(969/fmed,3),
        },
        'placebo_ABBA': {
            'labels_are_identical_binary': True,
            'A_runs': len(a), 'B_runs': len(b),
            'A_median_fps': round(median(a),3),
            'B_median_fps': round(median(b),3),
            'geometric_quartet_A_over_B_pct': round(observed,3),
            'quartet_bootstrap_95pct_interval_pct': [round(low,3),round(high,3)],
            'quartet_bootstrap_resamples': bootstrap_reps,
            'bootstrap_caveat': 'descriptive: assumes independent quartets; correlated host noise may widen true uncertainty',
        },
        'host_interference': {
            'runs_with_cgroup_throttling_events': sum(1 for x in throttling if x > 0) if throttling else None,
            'total_cgroup_throttled_events': sum(throttling) if throttling else None,
            'total_cgroup_throttled_ms': round(sum(throttle_ms),3) if throttle_ms else None,
        },
        'exploratory_precision_gates': {
            'p10_p90_within_6pct': spread <= 6,
            'placebo_bootstrap_halfwidth_within_2pct': aa_interval_halfwidth <= 2,
            'half_to_half_drift_within_3pct': abs(drift) <= 3,
            'all_three': spread <= 6 and aa_interval_halfwidth <= 2 and abs(drift) <= 3,
            'note': 'heuristic only: future A/B needs separate interleaved replicated comparison on a dedicated host',
        },
        'limitations': [
            'Only one GitHub Actions host/run; does not measure runner-to-runner variance.',
            'Shared CI CPU and rendering can cause load-correlated changes.',
            'Quake logs 1 decimal FPS, which limits sensitivity.',
            'FPS excludes emulator startup but includes rendering/guest execution.',
            'A/A pseudo-groups cannot establish an optimization effect.',
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    with a.csv.open(newline='') as f:
        rows = list(csv.DictReader(f))
    result = analyze(rows, json.loads(a.manifest.read_text()))
    a.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
