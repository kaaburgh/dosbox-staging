#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cost-injection experiment for the dynamic core's guest-flag save/restore.

Usage: apply_flag_injection.py <checkout> popf|pushf <K>

popf K : after every POPFQ emitted by gen_needflags, add K x (PUSHFQ; POPFQ).
         Flags are pushed and restored unchanged -> guest semantics unchanged.
pushf K: after every PUSHFQ; LEA RSP,-8 emitted by gen_protectflags, add
         K x (PUSHFQ; LEA RSP,[RSP+8]) -> no state change.

The slowdown divided by (K x executions counted by apply_jit_counters.py)
estimates the in-situ cost of one POPFQ (or PUSHFQ) in Quake's real code,
which is what candidate C4 would remove. Timing builds only.
"""
import sys
from pathlib import Path

root, kind, k = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
p = root / 'src/cpu/core_dyn_x86/risc_x64.h'
s = p.read_text()
if kind == 'popf':
    old = "\t\tcache_addb(0x9d);\t\t//POPFQ\n"
    new = old + "".join("\t\tcache_addb(0x9c); cache_addb(0x9d); // injected PUSHFQ;POPFQ\n" for _ in range(k))
elif kind == 'pushf':
    old = "\t\topcode(4).set64().setea(4,-1,0,-(CALLSTACK)).Emit8(0x8D); // lea rsp, [rsp-8/40]\n"
    new = old + "".join("\t\tcache_addb(0x9c); opcode(4).set64().setea(4,-1,0,8).Emit8(0x8D); // injected PUSHFQ;LEA RSP,+8\n" for _ in range(k))
else:
    raise SystemExit('kind must be popf or pushf')
assert s.count(old) == 1, s.count(old)
p.write_text(s.replace(old, new))
print(f'injected {kind} x{k}')
