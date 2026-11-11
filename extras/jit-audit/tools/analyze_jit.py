#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Join a JitPerfMap block dump with `perf script` samples.

Inputs (all private, never commit): the binary dump written by a
DOSBOX_JIT_PERF_MAP build (DOSBOX_JIT_DUMP=<file>) and the text of
`perf script -F tid,time,ip,sym,dso` (with call chains if recorded).

Each sample is attributed by (time, ip) to the JIT block generation that was
live at that instant (created before, not yet cleared), never by address
alone. Samples inside DOSBox helpers are attributed to the first call-chain
frame that falls inside a live JIT generation ("caller block").

Outputs a JSON summary of *derived* counts: per-region shares, top blocks,
native instruction classes of sampled JIT instructions, helper->caller
pairs, and static code statistics (sizes, call sites, PUSHFQ/POPFQ counts).
"""
import argparse
import bisect
import collections
import json
import re
import struct
import sys
from pathlib import Path

import capstone

MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)


def read_dump(path: Path):
    data = path.read_bytes()
    pos = 0
    blocks, clears = [], []
    while pos < len(data):
        magic, = struct.unpack_from('<I', data, pos)
        if magic == 0x314b4c42:
            seq, t, glin, cs, eip, addr, nlen, glen = struct.unpack_from('<QQIHIQII', data, pos + 4)
            pos += 4 + 8 + 8 + 4 + 2 + 4 + 8 + 4 + 4
            native = data[pos:pos + nlen]
            pos += nlen
            guest = data[pos:pos + glen]
            pos += glen
            blocks.append(dict(seq=seq, t=t, glin=glin, cs=cs, eip=eip, addr=addr,
                               nlen=nlen, native=native, guest=guest, end_t=None))
        elif magic == 0x31564e49:
            seq, t, addr = struct.unpack_from('<QQQ', data, pos + 4)
            pos += 4 + 24
            clears.append((t, addr, seq))
        else:
            raise SystemExit(f'bad record at {pos}')
    # close generations: a clear at address A ends the most recent block at A
    by_addr = collections.defaultdict(list)
    for b in blocks:
        by_addr[b['addr']].append(b)
    for t, addr, seq in clears:
        for b in reversed(by_addr.get(addr, [])):
            if b['seq'] < seq and b['end_t'] is None:
                b['end_t'] = t
                break
    return blocks, clears


class Index:
    def __init__(self, blocks):
        self.blocks = sorted(blocks, key=lambda b: b['addr'])
        self.starts = [b['addr'] for b in self.blocks]
        self.max_len = max((b['nlen'] for b in blocks), default=0)

    def find(self, ip, t):
        i = bisect.bisect_right(self.starts, ip) - 1
        best = None
        while i >= 0 and ip - self.starts[i] <= self.max_len:
            b = self.blocks[i]
            if b['addr'] <= ip < b['addr'] + b['nlen'] and b['t'] <= t and (
                    b['end_t'] is None or t < b['end_t']):
                if best is None or b['seq'] > best['seq']:
                    best = b
            i -= 1
        return best


def insns(block):
    return list(MD.disasm(block['native'], block['addr']))


def klass(ins):
    m = ins.mnemonic
    if m in ('pushfq', 'pushf'):
        return 'pushfq'
    if m in ('popfq', 'popf'):
        return 'popfq'
    if m.startswith('call'):
        return 'call'
    if m.startswith('j'):
        return 'branch'
    if m.startswith('f'):
        return 'x87'
    if m in ('mov', 'movzx', 'movsx', 'lea') and '[' in ins.op_str:
        return 'mem-move'
    if m in ('mov', 'movzx', 'movsx', 'lea'):
        return 'reg-move'
    return 'alu/other'


HEADER_RE = re.compile(r'^\s*(\d+)(?:/(\d+))?\s+(\d+)\.(\d+):\s*$')
FRAME_RE = re.compile(r'^\s+([0-9a-f]+)\s+(.*)$')


def parse_perf(lines, tid_filter):
    """Parse `perf script -F tid,time,ip,sym,dso` output with call chains."""
    sample = None
    for line in lines:
        m = HEADER_RE.match(line)
        if m:
            if sample:
                yield sample
            tid = int(m.group(2) or m.group(1))
            t = int(m.group(3)) * 1_000_000_000 + int(m.group(4).ljust(9, '0')[:9])
            sample = dict(tid=tid, t=t, frames=[])
            continue
        fm = FRAME_RE.match(line)
        if fm and sample is not None:
            sample['frames'].append((int(fm.group(1), 16), fm.group(2)))
    if sample:
        yield sample


def region(desc):
    m = re.search(r'\(([^)]*)\)\s*$', desc)
    dso = m.group(1) if m else '?'
    sym = desc[:m.start()].strip() if m else desc
    sym = re.sub(r'\+0x[0-9a-f]+$', '', sym)
    return dso, sym


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dump', type=Path, required=True)
    p.add_argument('--perf-script', type=Path, required=True)
    p.add_argument('--tid', type=int, help='main thread id (default: most sampled)')
    p.add_argument('--top', type=int, default=25)
    p.add_argument('--private-detail', type=Path,
                   help='optional private JSON with guest/native disassembly of top blocks')
    a = p.parse_args()
    blocks, clears = read_dump(a.dump)
    idx = Index(blocks)
    samples = list(parse_perf(a.perf_script.read_text(errors='replace').splitlines(), None))
    tids = collections.Counter(s['tid'] for s in samples)
    tid = a.tid or tids.most_common(1)[0][0]
    samples = [s for s in samples if s['tid'] == tid and s['frames']]
    n = len(samples)
    by_region = collections.Counter()
    by_sym = collections.Counter()
    jit_self = collections.Counter()
    jit_ins = collections.Counter()
    helper_caller = collections.Counter()
    block_total = collections.Counter()
    unattributed_jit = 0
    for s in samples:
        ip, desc = s['frames'][0]
        b = idx.find(ip, s['t'])
        if b is not None:
            by_region['jit'] += 1
            key = (b['seq'])
            jit_self[key] += 1
            block_total[key] += 1
            jit_ins[(key, ip)] += 1
            continue
        dso, sym = region(desc)
        if dso.startswith('/tmp/perf-') or sym.startswith('jit_'):
            unattributed_jit += 1
            by_region['jit-unattributed'] += 1
            continue
        name = Path(dso).name
        by_region[name] += 1
        by_sym[(name, sym)] += 1
        for fip, fdesc in s['frames'][1:]:
            cb = idx.find(fip, s['t'])
            if cb is not None:
                helper_caller[(sym, cb['seq'])] += 1
                block_total[cb['seq']] += 1
                break
    seq_to_block = {b['seq']: b for b in blocks}
    # instruction classes over JIT self samples (IP = next instruction to run)
    cls_on = collections.Counter()
    cls_prev = collections.Counter()
    ins_cache = {}
    for (seq, ip), c in jit_ins.items():
        b = seq_to_block[seq]
        if seq not in ins_cache:
            ins_cache[seq] = insns(b)
        lst = ins_cache[seq]
        addrs = [i.address for i in lst]
        k = bisect.bisect_left(addrs, ip)
        if k < len(lst) and lst[k].address == ip:
            cls_on[klass(lst[k])] += c
            if k > 0:
                cls_prev[klass(lst[k - 1])] += c
        else:
            cls_on['not-an-instruction-start'] += c
    # static stats over all generations
    total_native = sum(b['nlen'] for b in blocks)
    static = collections.Counter()
    for b in blocks:
        for i in insns(b):
            static[klass(i)] += 1
    top = []
    for seq, c in block_total.most_common(a.top):
        b = seq_to_block[seq]
        lst = ins_cache.get(seq) or insns(b)
        cnt = collections.Counter(klass(i) for i in lst)
        top.append(dict(cs=f"{b['cs']:04x}", eip=f"{b['eip']:08x}", generation=seq,
                        samples_total=c, samples_self=jit_self.get(seq, 0),
                        samples_in_helpers=c - jit_self.get(seq, 0),
                        native_bytes=b['nlen'], guest_bytes=len(b['guest']),
                        native_calls=cnt['call'], pushfq=cnt['pushfq'], popfq=cnt['popfq']))
    helpers = [dict(helper=h, caller_eip=f"{seq_to_block[s]['eip']:08x}", samples=c)
               for (h, s), c in helper_caller.most_common(40)]
    out = dict(main_tid_samples=n, threads=len(tids),
               regions={k: v for k, v in by_region.most_common()},
               top_symbols=[dict(dso=d, symbol=s, samples=c) for (d, s), c in by_sym.most_common(40)],
               jit_self_instruction_class_at_ip=dict(cls_on.most_common()),
               jit_self_instruction_class_before_ip=dict(cls_prev.most_common()),
               helper_samples_attributed_to_caller_block=sum(helper_caller.values()),
               helper_to_caller=helpers, top_blocks=top,
               static=dict(generations=len(blocks), clears=len(clears),
                           total_native_bytes=total_native,
                           max_block_bytes=max(b['nlen'] for b in blocks),
                           instruction_classes=dict(static.most_common())))
    print(json.dumps(out, indent=1))
    if a.private_detail:
        detail = []
        for t in top[:12]:
            b = seq_to_block[t['generation']]
            lines = []
            for i in insns(b):
                c = jit_ins.get((b['seq'], i.address), 0)
                lines.append(f"{c:5d} {i.address - b['addr']:5d}: {i.mnemonic} {i.op_str}")
            detail.append(dict(block=t, guest_hex=b['guest'].hex(), native=lines))
        a.private_detail.write_text(json.dumps(detail, indent=1))


if __name__ == '__main__':
    main()
