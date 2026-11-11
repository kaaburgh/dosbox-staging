#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check FLAGRST.COM output against expectations derived from the Intel SDM.

Reads the program's stdout on stdin. Words 20 (IMUL) and 21 (SHLD) contain
architecturally undefined flags: only their defined bits are checked here and
the raw line hash is printed so builds can be compared with each other.
"""
import hashlib
import sys

CF, PF, AF, ZF, SF, OF = 0x001, 0x004, 0x010, 0x040, 0x080, 0x800
ARITH = CF | PF | AF | ZF | SF | OF


def jcc_mask(f: int) -> int:
    """SDM vol. 2, Jcc: condition codes 0..15."""
    cf, pf, zf, sf, of = (bool(f & x) for x in (CF, PF, ZF, SF, OF))
    conds = [of, not of, cf, not cf, zf, not zf, cf or zf, not (cf or zf),
             sf, not sf, pf, not pf, sf != of, sf == of,
             zf or (sf != of), (not zf) and (sf == of)]
    return sum(1 << i for i, c in enumerate(conds) if c)


def expected(f: int) -> dict[int, int]:
    e = {0: f, 1: f ^ CF, 2: f | CF, 3: f & ~CF,
         4: jcc_mask(f), 5: 0,
         6: f ^ CF, 7: jcc_mask(f), 8: 0,
         9: f,                                   # count 0: flags unaffected
         10: (f & ~(CF | OF)) | CF | OF,         # ROL 80000001h,1: CF=1, OF=MSB(3)^CF=1
         11: 0xFF if f & CF else 0x00, 12: f,    # SALC: AL=CF?FF:00, flags unaffected
         13: 1 if f & ZF else 0,                 # LOOPE: CX=2 -> 1 (nonzero) and ZF=1
         14: 0 if f & ZF else 1,                 # LOOPNE
         15: 0,
         16: f ^ CF, 17: 0,
         18: 1 if f & OF else 0, 19: 0}
    return e


def main() -> int:
    lines = [l.strip() for l in sys.stdin if l.strip()]
    if not lines or lines[-1] != 'FLAGRST_DONE':
        print('FLAGRST: incomplete output')
        return 2
    rows = [[int(x, 16) for x in l.split()] for l in lines[:-1]]
    bad = []
    seen = set()
    for r in rows:
        f, w = r[0], r[1:]
        seen.add(f)
        if len(w) != 22:
            bad.append((f, 'len', len(w), 22))
            continue
        for i, exp in expected(f).items():
            if w[i] != exp:
                bad.append((f, i, w[i], exp))
        # IMUL 3*5: no overflow -> CF=OF=0 (other flags undefined)
        if w[20] & (CF | OF):
            bad.append((f, 20, w[20], 'CF=OF=0'))
        # SHLD 80000000h,0,1: CF=1 (bit shifted out), result 0 -> ZF=1 SF=0 PF=1, OF=1 (sign change)
        if (w[21] & (CF | ZF | SF | PF | OF)) != (CF | ZF | PF | OF):
            bad.append((f, 21, w[21], 'CF ZF PF OF'))
    if len(seen) != 64:
        bad.append(('combos', len(seen), 64, ''))
    digest = hashlib.sha256('\n'.join(lines).encode()).hexdigest()[:16]
    print(f'FLAGRST combos={len(seen)} deviations={len(bad)} sha={digest}')
    for b in bad[:12]:
        print('  flags=%s word=%s got=%s expected=%s' % tuple(
            (f'{x:03x}' if isinstance(x, int) else x) for x in b))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
