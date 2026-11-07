#!/usr/bin/env python3
"""Correlate DOSBox JIT block lifecycle CSV with host RIP samples.

JIT CSV: export from DOSBox Staging built with OPT_JIT_BLOCK_TRACE=ON.
Samples: SIGPROF sampler binary records <QQII> (monotonic_ns, RIP, TID, pad).
All JIT addresses are per-process; never mix traces across PIDs / runs.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import csv
import json
from dataclasses import dataclass
from pathlib import Path
import struct

SAMPLE = struct.Struct('<QQII')


@dataclass(frozen=True)
class Block:
    generation: int
    guest_cs: int
    guest_eip: int
    guest_linear_begin: int
    guest_linear_end: int
    host_begin: int
    host_end: int
    created_ns: int


@dataclass(frozen=True)
class Event:
    event: str
    timestamp_ns: int
    pid: int
    generation: int
    block: Block | None = None


def events_from_csv(path: Path) -> list[Event]:
    events: list[Event] = []
    known_ids: set[int] = set()
    pids: set[int] = set()
    with path.open(newline='') as stream:
        for line_no, row in enumerate(csv.DictReader(stream), 2):
            try:
                kind = row['event']
                if kind not in ('C', 'D'):
                    raise ValueError(f'unsupported event {kind!r}')
                at = int(row['monotonic_ns'])
                pid = int(row['pid'])
                generation = int(row['generation'])
                pids.add(pid)
                if len(pids) > 1:
                    raise ValueError('mixed process IDs in one trace')
                if at <= 0 or generation < 1:
                    raise ValueError('invalid timestamp or generation ID')
                if events and at < events[-1].timestamp_ns:
                    raise ValueError('non-monotonic lifecycle timestamps')
                block = None
                if kind == 'C':
                    if generation in known_ids:
                        raise ValueError('duplicate generation ID')
                    known_ids.add(generation)
                    parse = lambda key: int(row[key], 0)
                    block = Block(generation=generation,
                                  guest_cs=parse('guest_cs'),
                                  guest_eip=parse('guest_eip'),
                                  guest_linear_begin=parse('guest_linear_begin'),
                                  guest_linear_end=parse('guest_linear_end'),
                                  host_begin=parse('host_begin'),
                                  host_end=parse('host_end'),
                                  created_ns=at)
                    if not block.host_begin < block.host_end:
                        raise ValueError('empty or reversed host interval')
                events.append(Event(kind, at, pid, generation, block))
            except (ValueError, KeyError, TypeError) as ex:
                raise ValueError(f'{path}:{line_no}: {ex}') from ex
    return events


class LiveMap:
    """Sorted native JIT address ranges, with generation-aware eviction."""
    def __init__(self) -> None:
        self.starts: list[int] = []
        self.blocks: dict[int, Block] = {}
        self.by_generation: dict[int, int] = {}
        self.creates = 0
        self.clears = 0
        self.high_water = 0
        self.overlaps = 0

    def apply(self, event: Event) -> None:
        if event.event == 'D':
            if event.generation not in self.by_generation:
                raise ValueError(f'unknown or already cleared generation {event.generation}')
            begin = self.by_generation.pop(event.generation)
            self.blocks.pop(begin)
            self.starts.pop(bisect.bisect_left(self.starts, begin))
            self.clears += 1
            return
        assert event.block is not None
        block = event.block
        index = bisect.bisect_left(self.starts, block.host_begin)
        if ((index > 0 and self.blocks[self.starts[index-1]].host_end > block.host_begin)
                or (index < len(self.starts) and self.starts[index] < block.host_end)):
            self.overlaps += 1
            raise ValueError(f'overlapping live native ranges at generation {block.generation}')
        self.starts.insert(index, block.host_begin)
        self.blocks[block.host_begin] = block
        self.by_generation[block.generation] = block.host_begin
        self.creates += 1
        self.high_water = max(self.high_water, len(self.blocks))

    def locate(self, rip: int) -> Block | None:
        index = bisect.bisect_right(self.starts, rip) - 1
        if index < 0:
            return None
        block = self.blocks[self.starts[index]]
        return block if rip < block.host_end else None


def classify(events: list[Event], samples: list[tuple[int, int]]) -> tuple[dict, list[tuple[Block, int]]]:
    """Samples are (monotonic_ns, host RIP); caller supplies one process."""
    state = LiveMap()
    order = 0
    unmatched = 0
    pretrace = 0
    hit_by_generation = collections.Counter()
    all_blocks: dict[int, Block] = {}
    last_ns = -1
    for timestamp_ns, rip in samples:
        if timestamp_ns < last_ns:
            raise ValueError('samples must be sorted by monotonic timestamp')
        last_ns = timestamp_ns
        while order < len(events) and events[order].timestamp_ns <= timestamp_ns:
            event = events[order]
            state.apply(event)
            if event.block:
                all_blocks[event.generation] = event.block
            order += 1
        if order == 0:
            pretrace += 1
        block = state.locate(rip)
        if block is None:
            unmatched += 1
        else:
            hit_by_generation[block.generation] += 1
    while order < len(events):
        state.apply(events[order])
        order += 1
    counts = [(all_blocks[k], n) for k, n in hit_by_generation.most_common()]
    summary = {'events': len(events), 'pid': events[0].pid if events else None,
               'creates': state.creates, 'invalidations': state.clears,
               'open_generations': len(state.by_generation),
               'max_simultaneously_live': state.high_water,
               'samples': len(samples), 'jit_samples': len(samples) - unmatched,
               'non_jit_samples': unmatched, 'samples_before_first_event': pretrace,
               'matched_generations': len(counts)}
    return summary, counts


def read_samples(path: Path, *, tid: int | None = None,
                 start_ns: int | None = None,
                 end_ns: int | None = None) -> list[tuple[int, int]]:
    """Load main-thread samples from the SIGPROF binary stream.

    Other threads (notably Mesa workers) must never be attributed to the
    DOSBox x86 dynamic-core JIT. The main Linux TID equals the trace PID.
    """
    data = path.read_bytes()
    if len(data) % SAMPLE.size:
        raise ValueError(f'{path}: incomplete {SAMPLE.size}-byte sample record')
    samples = [(t, rip) for t, rip, sample_tid, pad in SAMPLE.iter_unpack(data)
               if (tid is None or sample_tid == tid)
               and (start_ns is None or t >= start_ns)
               and (end_ns is None or t <= end_ns)]
    return sorted(samples)


def aggregate_by_guest(ranked: list[tuple[Block, int]]) -> list[tuple[tuple[int, int, int], int]]:
    """Combine separately compiled generations of the same guest block."""
    count: collections.Counter[tuple[int, int, int]] = collections.Counter()
    for block, samples in ranked:
        count[(block.guest_cs, block.guest_eip, block.guest_linear_begin)] += samples
    return count.most_common()


def write_guest_csv(path: Path, guest_counts: list[tuple[tuple[int, int, int], int]]) -> None:
    with path.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(('samples', 'guest_cs', 'guest_eip', 'guest_linear_begin'))
        for (cs, eip, linear), samples in guest_counts:
            w.writerow((samples, hex(cs), hex(eip), hex(linear)))


def write_ranked_csv(path: Path, ranked: list[tuple[Block, int]]) -> None:
    columns = ('samples', 'generation', 'guest_cs', 'guest_eip',
               'guest_linear_begin', 'guest_linear_end', 'host_begin',
               'host_end', 'created_ns')
    with path.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(columns)
        for block, hits in ranked:
            w.writerow((hits, block.generation, hex(block.guest_cs),
                        hex(block.guest_eip), hex(block.guest_linear_begin),
                        hex(block.guest_linear_end), hex(block.host_begin),
                        hex(block.host_end), block.created_ns))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--samples', type=Path, help='optional 24-byte SIGPROF sample stream')
    parser.add_argument('--output', type=Path, help='write matched generation samples CSV')
    parser.add_argument('--output-guest', type=Path, help='write aggregated guest block samples CSV')
    parser.add_argument('--start-ns', type=int, help='beginning of demo (monotonic ns)')
    parser.add_argument('--end-ns', type=int, help='end of demo (monotonic ns)')
    args = parser.parse_args()
    events = events_from_csv(args.trace)
    trace_pid = events[0].pid if events else None
    samples = read_samples(args.samples, tid=trace_pid,
                           start_ns=args.start_ns, end_ns=args.end_ns) if args.samples else []
    summary, counts = classify(events, samples)
    if args.output:
        write_ranked_csv(args.output, counts)
    guest_counts = aggregate_by_guest(counts)
    if args.output_guest:
        write_guest_csv(args.output_guest, guest_counts)
    print(json.dumps(summary, indent=2))
    print('Top guest blocks across generations:')
    for (cs, eip, linear), samples in guest_counts[:20]:
        print(f'{samples:6d} samples guest {cs:04x}:{eip:08x} '
              f'linear 0x{linear:08x}')


if __name__ == '__main__':
    main()
