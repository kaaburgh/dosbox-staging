#!/usr/bin/env python3
"""Verify real x86 dynamic-core cache reuse using WRAP.COM lifecycle events.

Run the guest checksum probe built from cache_wrap_stress.asm first.
Do not confuse a guest block being *executed again* with a native cache
region being *overwritten* by a different generation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import json

from analyze_trace import events_from_csv, classify

EXPECTED_EIPS = {0x107 + 3 * i for i in range(16000)}


def verify(trace: Path, markers: list[Path], expect_reuse: bool) -> dict:
    for marker in markers:
        if marker.read_bytes() != b'OK\r\n':
            raise ValueError(f'{marker}: no correct guest checksum marker')

    events = events_from_csv(trace)
    summary, _ = classify(events, [])

    creates = [e.block for e in events if e.block]
    # Other guest CS selectors (DOS services, BIOS) also appear in a trace.
    candidates = Counter(b.guest_cs for b in creates
                         if b.guest_eip in EXPECTED_EIPS)
    if not candidates:
        raise ValueError('no WRAP.COM code section in JIT lifecycle')
    guest_cs, _ = candidates.most_common(1)[0]
    guest_blocks = [b for b in creates if b.guest_cs == guest_cs]
    observed = {b.guest_eip for b in guest_blocks}
    missing = EXPECTED_EIPS - observed
    if missing:
        raise ValueError(f'{len(missing)} of 16000 guest basic blocks missing: '
                         f'{sorted(missing)[:10]}')
    wrong = [b for b in guest_blocks
             if b.guest_linear_begin != 16 * guest_cs + b.guest_eip]
    if wrong:
        raise ValueError(f'{len(wrong)} incorrect real-mode linear addresses')

    by_host = Counter(b.host_begin for b in creates)
    reused = sum(1 for count in by_host.values() if count >= 2)
    if expect_reuse and reused == 0:
        raise ValueError('no native JIT start address reused; cache never wrapped')

    # classify() already rejects overlapping simultaneously-live host ranges,
    # unknown clear events, generation collisions and out-of-order timestamps.
    # Check that reused native starts were explicitly invalidated first.
    live = {}
    by_generation = {}
    seen_native_starts = set()
    recycled = 0
    for event in events:
        if event.event == 'D':
            old_start = by_generation.pop(event.generation)
            del live[old_start]
            continue
        block = event.block
        assert block is not None
        if block.host_begin in seen_native_starts:
            recycled += 1
        seen_native_starts.add(block.host_begin)
        if block.host_begin in live:
            raise ValueError('host JIT start reused before previous generation clear')
        live[block.host_begin] = block.generation
        by_generation[block.generation] = block.host_begin

    return {**summary, 'guest_passes': len(markers),
            'guest_cs': f'0x{guest_cs:04x}',
            'known_guest_basic_blocks_checked': len(EXPECTED_EIPS),
            'wrong_real_mode_linear_addresses': len(wrong),
            'reused_host_starts': reused,
            'reused_host_start_creation_events': recycled,
            'cache_reuse_required': expect_reuse,
            'guest_semantics_valid': True}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trace', type=Path, required=True)
    p.add_argument('--marker', type=Path, action='append', required=True,
                   help='repeat for each independent guest execution')
    p.add_argument('--require-reuse', action='store_true')
    p.add_argument('--output-json', type=Path)
    a = p.parse_args()
    result = verify(a.trace, a.marker, a.require_reuse)
    if a.output_json:
        a.output_json.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
