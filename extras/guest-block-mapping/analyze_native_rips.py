#!/usr/bin/env python3
"""Generation-safe byte-offset-level JIT RIP sampling for Quake diagnostics.

Consumes private raw snapshots, main-thread SIGPROF samples, and an instrumented
DOSBox lifecycle trace. Only derived counts and hashes are written. A sample in
an emitted block is NOT proof that a deferred call/slow path was executed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

from analyze_trace import (LiveMap, events_from_csv, read_samples,
                           reject_recorder_io_errors)


def parse_addr(value: object) -> int:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError(f'invalid address: {value!r}')
    return int(value, 0) if isinstance(value, str) else value


def safe_blob(root: Path, value: object, extension: str) -> Path:
    if not isinstance(value, str) or Path(value).name != value or not value.endswith(extension):
        raise ValueError(f'unsafe snapshot filename: {value!r}')
    return root / value


def load_snapshots(directory: Path, events: list) -> dict[int, dict]:
    creates = {e.generation: e.block for e in events if e.event == 'C'}
    pid = events[0].pid
    snapshots = {}
    metadata_files = sorted(directory.glob('jit-*.json'))
    if not metadata_files:
        raise ValueError('no snapshot metadata files found')
    for path in metadata_files:
        meta = json.loads(path.read_text())
        if meta.get('schema') != 1 or meta.get('pid') != pid:
            raise ValueError(f'{path.name}: schema or PID mismatch')
        gen = meta.get('generation')
        if not isinstance(gen, int) or gen not in creates or gen in snapshots:
            raise ValueError(f'{path.name}: missing/duplicate CREATE generation')
        block = creates[gen]
        assert block is not None
        for attr in ('guest_cs', 'guest_eip', 'guest_linear_begin',
                     'guest_linear_end', 'host_begin', 'host_end'):
            if parse_addr(meta.get(attr)) != getattr(block, attr):
                raise ValueError(f'{path.name}: {attr} differs from lifecycle CREATE')
        host_file = safe_blob(directory, meta.get('host_filename'), '.host.bin')
        guest_file = safe_blob(directory, meta.get('guest_filename'), '.guest.bin')
        if host_file.is_symlink() or guest_file.is_symlink():
            raise ValueError(f'{path.name}: symlink blob not allowed')
        host_data = host_file.read_bytes()
        guest_size = guest_file.stat().st_size
        native_len = block.host_end - block.host_begin
        if (meta.get('host_bytes') != native_len or len(host_data) != native_len or
                meta.get('guest_captured_bytes') != guest_size or
                meta.get('guest_expected_bytes') !=
                block.guest_linear_end - block.guest_linear_begin or
                not isinstance(meta.get('guest_complete'), bool)):
            raise ValueError(f'{path.name}: blob size or completeness mismatch')
        if not meta.get('native_includes_deferred_slow_paths'):
            raise ValueError(f'{path.name}: unexpected native code coverage')
        snapshots[gen] = {'block': block, 'sha256': hashlib.sha256(host_data).hexdigest(),
                          'host_size': native_len, 'guest_complete': meta['guest_complete']}
    return snapshots


def analyze(events: list, samples: list[tuple[int, int]], snapshots: dict[int, dict],
            bucket_size: int = 16) -> tuple[dict, list[dict], list[dict]]:
    if bucket_size <= 0 or bucket_size & (bucket_size - 1):
        raise ValueError('bucket_size must be a positive power of two')
    live = LiveMap()
    cursor = 0
    last_time = -1
    counts = Counter()
    histogram = Counter()
    offsets = Counter()
    for at, rip in samples:
        if at < last_time:
            raise ValueError('samples must be sorted by time')
        last_time = at
        while cursor < len(events) and events[cursor].timestamp_ns <= at:
            live.apply(events[cursor])
            cursor += 1
        block = live.locate(rip)
        counts['all_main'] += 1
        if block is None:
            counts['non_jit'] += 1
            continue
        counts['jit'] += 1
        if block.generation not in snapshots:
            counts['jit_without_snapshot'] += 1
            continue
        counts['selected'] += 1
        offset = rip - block.host_begin
        counts[('gen', block.generation)] += 1
        histogram[(block.generation, offset // bucket_size * bucket_size)] += 1
        offsets[(block.generation, offset)] += 1
    while cursor < len(events):
        live.apply(events[cursor])
        cursor += 1
    summary = {
        'main_thread_samples': counts['all_main'],
        'all_jit_samples': counts['jit'],
        'other_jit_samples': counts['jit_without_snapshot'],
        'non_jit_samples_unattributed': counts['non_jit'],
        'selected_snapshot_samples': counts['selected'],
        'selected_generations': len(snapshots),
        'bucket_bytes': bucket_size,
        'interpretation': 'sample counts, not executed call counts; no helper attribution outside JIT',
        'warning': ('Too few selected samples for hotspot selection; obtain longer/more runs'
                    if counts['selected'] < 100 else None),
    }
    blocks = []
    for gen, snapshot in sorted(snapshots.items()):
        block = snapshot['block']
        blocks.append({
            'generation': gen, 'guest_cs': hex(block.guest_cs),
            'guest_eip': hex(block.guest_eip),
            'native_bytes': snapshot['host_size'], 'host_sha256': snapshot['sha256'],
            'guest_complete': snapshot['guest_complete'],
            'samples': counts[('gen', gen)],
        })
    bins = [{'generation': gen, 'guest_eip': hex(snapshots[gen]['block'].guest_eip),
             'offset_start': start,
             'offset_end_exclusive': min(start + bucket_size, snapshots[gen]['host_size']),
             'samples': count} for (gen, start), count in sorted(histogram.items())]
    exact = [{'generation': gen, 'offset': offset, 'samples': count}
             for (gen, offset), count in sorted(offsets.items())]
    return {**summary, 'blocks': blocks}, bins, exact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--snapshots', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='new directory for derived output only')
    parser.add_argument('--start-ns', type=int, required=True)
    parser.add_argument('--end-ns', type=int, required=True)
    parser.add_argument('--dosbox-log', type=Path)
    parser.add_argument('--bucket-bytes', type=int, default=16)
    args = parser.parse_args()
    if args.start_ns <= 0 or args.end_ns <= args.start_ns:
        parser.error('must provide a positive demo window (start < end)')
    if args.out.exists():
        parser.error('output directory must not already exist (avoid mixing runs)')
    if args.dosbox_log:
        reject_recorder_io_errors(args.dosbox_log)
        log = args.dosbox_log.read_text(errors='replace')
        if 'THREAD_SIGPROF_PROFILE_STARTED' not in log:
            raise ValueError('SIGPROF start marker not found in log')
    events = events_from_csv(args.trace)
    if not events:
        raise ValueError('empty JIT trace')
    snapshots = load_snapshots(args.snapshots, events)
    samples = read_samples(args.samples, tid=events[0].pid,
                           start_ns=args.start_ns, end_ns=args.end_ns)
    summary, bins, exact = analyze(events, samples, snapshots, args.bucket_bytes)
    args.out.mkdir(mode=0o700, parents=True)
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    for filename, rows, columns in (
        ('native-bins.csv', bins, ('generation', 'guest_eip', 'offset_start', 'offset_end_exclusive', 'samples')),
        ('native-rips.csv', exact, ('generation', 'offset', 'samples')),
    ):
        with (args.out / filename).open('w', newline='') as out:
            writer = csv.DictWriter(out, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
