# Bit-test semantics regression — local Linux execution (2026-11-09)

Executed on the assistant's own **Linux x86-64 container** with DOSBox
Staging **0.84.0-alpha (91774)** built from fork commit
`91774d582069d52f4d3bf04952cb6047dbdc073f`.

| Artifact | SHA-256 |
|---|---|
| DOSBox, Release, `OPT_JIT_BLOCK_TRACE=OFF` | `1b12b66eb4691578d0cc7abd5a678562e6e68078a64509f809e62c6941a9e481` |
| DOSBox, Release, `OPT_JIT_BLOCK_TRACE=ON` | `ffd4e25086813cd54299eb3ce9a416c64a4cb72067a2b911d07107caa8903b2d` |
| `BITSEM.COM` (assembled from `bit_test_semantics.asm`) | `87c1656ab7a9e192c8ff6864ecb559abd59b19759bcc11f12c7249cb53cd7bc4` |

Both builds are the unmodified fork sources; the only local build
adjustments are two unrelated SDK shims for the keyboard mapper and MIDI
libraries, which do not touch the CPU or the dynamic core.

## What the test does

`bit_test_semantics.asm` is a real-mode DOS COM program. It executes 16
cases of the bit-test instruction family on a 32-bit operand, captures the
carry flag with `setc` and the resulting operand, compares both against
expectations taken from the Intel SDM, and writes the raw results to
`BTOUT.BIN` through `int 21h`. Byte 1 of that file is the number of
deviations the program counted itself.

Coverage: `BT`, `BTS`, `BTR`, `BTC` with a register bit index and with an
immediate bit index, an operand with bits 0 and 31 set (so set / clear /
toggle are distinguishable), and a register index that must be masked to
five bits.

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
| 1 | `normal` (interpreter) | `640dffd87f299bc437c43c9ae07825deabd04057f295fc3a65ae13fa1580fd37` | 0 / 16 |
| 2 | `dynamic` (dynrec) | `640dffd87f299bc437c43c9ae07825deabd04057f295fc3a65ae13fa1580fd37` | 0 / 16 |
| 3 | `dynamic`, trace-enabled | `640dffd87f299bc437c43c9ae07825deabd04057f295fc3a65ae13fa1580fd37` | 0 / 16 |

All three outputs are **byte-identical**, and the carry-flag sequence
produced by the program is

```
CF:  1 1 0 1 0 1 1 1 0 0 1 0 1 1 0 0
EAX: 80000001 80000001 80000001 80000001 80000003 00000001
      80000000 80000000 80000003 80000001 80000001 80000021
      80000000 00000001 80000001 80000003
```

which matches the expected values exactly.

## Dynamic-core block trace

The trace-enabled run recorded 65 block lifecycle rows in total. Exactly
**three** of them belong to the test program's own segment (`CS = 0x01E4`):

| Generation | Guest EIP | Guest range | Native size |
|---|---|---|---:|
| 8 | `0x00000100` | `[0x1F40, 0x1F4A)` | 183 B |
| 10 | `0x0000010A` | `[0x1F4A, 0x1F4E)` | 110 B |
| 15 | `0x0000010E` | `[0x1F4E, 0x1F53)` | 161 B |

The last block ends at guest offset `0x113`, which is exactly where the
first bit-test instruction starts. **No further block is created for the
rest of the program**, although the program runs to completion and writes a
correct output file: everything after the first bit-test instruction is
executed by the interpreter through the `BR_Opcode` path.

This is the same mechanism that was observed in the Quake 1.06 guest, where
the block starting at guest `0x2F5B3` ends immediately after the bit-test
instruction and a separate 94-byte single-instruction block is created for
the bit-test address itself.

## Scope of validation

- The test establishes that the interpreter and the dynamic core agree on
  these 16 cases, and that the dynamic core currently delegates them.
- It does **not** cover the 16-bit operand forms (`66 0F A3` with a 16-bit
  operand), memory operands, or a bit index that exceeds the operand width
  (undefined by the SDM). Those cases need their own expectations before an
  implementation is proposed.
- It does not measure performance; it is a correctness gate.
- No proprietary game files, guest byte snapshots, native code blobs or raw
  sample addresses are stored in the repository.
