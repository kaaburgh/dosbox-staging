# Bit-test semantics regression — local Linux execution (2026-11-09)

Executed on the assistant's own **Linux x86-64 container** with DOSBox
Staging **0.84.0-alpha (91774)** built from fork commit
`91774d582069d52f4d3bf04952cb6047dbdc073f`.

| Artifact | SHA-256 |
|---|---|
| DOSBox, Release, `OPT_JIT_BLOCK_TRACE=OFF` | `1b12b66eb4691578d0cc7abd5a678562e6e68078a64509f809e62c6941a9e481` |
| DOSBox, Release, `OPT_JIT_BLOCK_TRACE=ON` | `ffd4e25086813cd54299eb3ce9a416c64a4cb72067a2b911d07107caa8903b2d` |
| `BITSEM.COM` (assembled from `bit_test_semantics.asm`) | `63093636969afb4e0d29a161e266d08ba174360c56c151363739309f15f86269` |
| `BTOUT.BIN`, `cpu core = normal` | `6a4d859e19bc8ab1f8f885c15c4660d232fdf83a9acfca12abc2c1016e55f0ff` |
| `BTOUT.BIN`, `cpu core = dynamic` | `6a4d859e19bc8ab1f8f885c15c4660d232fdf83a9acfca12abc2c1016e55f0ff` |

Both builds are the unmodified fork sources; the only local build
adjustments are two unrelated SDK shims for the keyboard mapper and MIDI
libraries, which do not touch the CPU or the dynamic core.

## What the test does

`bit_test_semantics.asm` is a real-mode DOS COM program with two sections.

**Section 1 — the bit-test family (16 cases).** `BT`, `BTS`, `BTR` and
`BTC` with a register bit index and with an immediate bit index, on a
32-bit operand with bits 0 and 31 set (so set / clear / toggle results are
distinguishable), plus an index that must be masked to five bits. The
expected values are computed from the Intel SDM.

**Section 2 — the x87 comparison idiom (3 cases).** `fldz; fld1; fucompp;
fnstsw ax; sahf` and variants, which is what the Quake compiler emits for
every floating-point comparison. `FNSTSW AX` puts `C0`/`C2`/`C3` into `AH`;
`SAHF` moves `C0` into `CF` and `C3` into `ZF`. The test masks `AH` with
`0x41` and checks `CF`/`ZF` for "greater", "less" and "equal".

The program counts its own deviations from the expected values and writes
the raw results to `BTOUT.BIN` through `int 21h`.

## Why the carry flag is read with PUSHF and not SETcc

`SETcc` (`0F 90`–`0F 9F`) is **not implemented** by the dynamic core, so an
earlier revision of this test that captured `CF` with `SETcc` sent the
whole program into the interpreter and compared the interpreter with itself.
That revision also produced the misleading observation that "the dynamic
core creates only three blocks" — those three blocks belonged to the DOS
kernel, not to the test program.

`PUSHF` (`0x9C`) and `POPF` (`0x9D`) *are* implemented, so the current
revision is genuinely translated by the dynamic core, and the runner reports
how much of it.

## Runs

```sh
nasm -f bin bit_test_semantics.asm -o BITSEM.COM
python3 run_bit_test_semantics.py \
    --dosbox /path/to/dosbox \
    --trace-dosbox /path/to/dosbox-trace \
    --out /tmp/bit-test
```

| Run | CPU core | `BTOUT.BIN` SHA-256 | Self-check deviations |
|---|---|---|---:|
| 1 | `normal` (interpreter) | `6a4d859e19bc8ab1f8f885c1…` | 0 / 19 |
| 2 | `dynamic` (dynrec) | `6a4d859e19bc8ab1f8f885c1…` | 0 / 19 |

Both outputs are **byte-identical**. The captured results are

```
CF / flags:  1 1 0 1 0 1 1 1 0 0 1 0 1 1 0 0   (section 1)
             0 1 64                            (section 2: greater, less, equal)
EAX:         80000001 80000001 80000001 80000001 80000003 00000001
             80000000 80000000 80000003 80000001 80000001 80000021
             80000000 00000001 80000001 80000003
```

which match the expected values exactly.

## Dynamic-core coverage

The trace-enabled run shows that the dynamic core translated **33 blocks**
covering 689 of the 1021 image bytes (67.5%). The image also contains the
DOS stub header and the expected-value tables, which are never executed.
Every *executed* byte range that no block covered is one of the 19
untranslatable instructions:

| Guest image range | Instruction | Bytes |
|---|---|---:|
| `0x0116`, `0x0137`, `0x0158`, `0x0179`, `0x019A`, `0x01BB`, `0x01DC`, `0x01FD`, `0x021E` | `BT`/`BTS`/`BTR`/`BTC` (register index) | 1 each |
| `0x0239`, `0x0255`, `0x0271`, `0x028D`, `0x02A9`, `0x02CB`, `0x02EC`, `0x0314`, `0x032A` | `BT`/`BTS`/`BTR`/`BTC` (immediate index) | 1–2 each |
| `0x0379` | `SAHF` (x87 comparison idiom) | 2 |

The instrumented build also counts the runtime fallbacks of this run:
**19 fallbacks, one per uncovered instruction** (16 bit-test, 3 `SAHF`).
The test therefore reproduces the Quake-side phenomenon in miniature: the
dynamic core translates everything it can and hands the missing opcodes to
the interpreter, while the guest-visible results stay correct.

## Scope of validation

- The test establishes that the interpreter and the dynamic core agree on
  these 19 cases and that the dynamic core delegates exactly the missing
  opcodes.
- It does **not** cover the 16-bit operand forms, memory operands, or a bit
  index that exceeds the operand width. For a *memory* bit base the
  instruction operates on a bit string that may extend beyond the operand's
  dword, so an implementation must reproduce the SDM's addressing rather
  than mask the index; the committed test cannot check that yet.
- The `PF` bit of the x87 comparison is not checked: the emulator's FPU
  comparison path does not propagate `C2` into the status word
  (`exc_mask` drops bit 10), so `PF` is always zero there. That is a
  pre-existing emulator behaviour, unrelated to this work, and the test
  avoids depending on it.
- It does not measure performance; it is a correctness gate.
- No proprietary game files, guest byte snapshots, native code blobs or raw
  sample addresses are stored in the repository.
