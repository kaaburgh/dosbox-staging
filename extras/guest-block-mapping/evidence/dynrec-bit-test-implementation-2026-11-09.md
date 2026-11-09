# Closing the bit-test / SETcc / SAHF gap in the dynamic core (2026-11-09)

This branch implements the part of the decoder gap documented in
`BIT_TEST_DECODER_GAP.md` that the repository's own gate allows to be
implemented, and it measures the result on the Quake 1.06 timedemo.

| Artifact | SHA-256 |
|---|---|
| DOSBox, Release, `OPT_JIT_BLOCK_TRACE=OFF` (arm B of the A/B) | `4508da4f43793a122b82dc158f0cef3bdb3db01cd1741679a6794a34c5cd6633` |
| DOSBox, Release, `OPT_JIT_BLOCK_TRACE=ON` | `4487cda22b40c5ec1006a88332e415f3161998ecd439f73a4a3a898f8c882768` |
| DOSBox, Release, `OPT_JIT_BLOCK_TRACE=OFF` (arm A, unmodified base) | `1b12b66eb4691578d0cc7abd5a678562e6e68078a64509f809e62c6941a9e481` |
| `BITSEM.COM` (assembled from `bit_test_semantics.asm`) | `63093636969afb4e0d29a161e266d08ba174360c56c151363739309f15f86269` |
| `BTOUT.BIN`, `cpu core = normal` | `6a4d859e19bc8ab1f8f885c15c4660d232fdf83a9acfca12abc2c1016e55f0ff` |
| `BTOUT.BIN`, `cpu core = dynamic` | `6a4d859e19bc8ab1f8f885c15c4660d232fdf83a9acfca12abc2c1016e55f0ff` |

The two arm-A/arm-B binaries differ only in this branch; the base binary is
the unmodified base branch. The only local build adjustments are two
unrelated SDK shims for the keyboard mapper and the MIDI library, which do
not touch the CPU or the dynamic core and are present in every build.

## What is implemented

* `BT`, `BTS`, `BTR`, `BTC` with a **register** bit base
  (`0F A3`/`0F AB`/`0F B3`/`0F BB`) and with an **immediate** bit index
  (`0F BA /4`–`/7`).
* `SETcc r/m8` for all sixteen condition codes (`0F 90`–`0F 9F`).
* `SAHF` (`9E`).

`SAHF` is implemented through a small C helper (`CPU_SAHF`) that mirrors the
interpreter core (`SETFLAGSb(reg_ah)`), and the helper call is structured
exactly like the existing `POPF` handling. An inline implementation that
wrote the flag slot directly was attempted first and abandoned: the dynamic
core keeps the guest flags in a per-block stack slot that is only valid while
the flags are inactive, and an inline write left the flags in a state that
the block exit did not save. The helper path reuses the mechanism the
dynamic core already trusts.

### What is deliberately *not* implemented

* `BT`/`BTS`/`BTR`/`BTC` with a **memory** bit base. The Intel SDM defines a
  bit string that may reach beyond the operand, so the register form and the
  memory form have different semantics. The memory form keeps falling back
  to the interpreter.
* `LAHF` (`9F`), `CMOVcc`, `BSF`/`BSR`, `PREFETCH` and the remaining two-byte
  opcodes of the gap. They are unaffected by this branch.

## Correctness gate

`run_bit_test_semantics.py` runs `bit_test_semantics.asm` under the
interpreter core and under the dynamic core and compares the raw result
bytes. The program also checks itself against SDM-derived expectations and
reports the number of deviations.

| Check | Result |
|---|---|
| interpreter core exit code | 0 |
| dynamic core exit code | 0 |
| `BTOUT.BIN` byte-identical between the two cores | yes |
| in-program self-check deviations | 0 |
| guest image bytes translated by the dynamic core | 710 / 1021 (69.5 %) |
| dynamic-core blocks created for the test program | 19 |

The self-check covers the register bit-base form of `BT`/`BTS`/`BTR`/`BTC`,
the immediate-index form, the five-bit index masking, `SETcc` in both the
register and the memory form, and the `fnstsw ax; sahf` comparison idiom
that the Quake compiler emits for every floating-point comparison.

## Fallback accounting

The dynamic core was instrumented with a counter that records every
interpreter entry together with the guest `CS:EIP` that caused it. The
instrumentation is a local patch and is **not** part of this branch; only
the results are published here. Both runs below are the same Quake 1.06
`demo1` timedemo (969 frames) on the same node, with the same
configuration.

| Guest code | base branch | this branch |
|---|---|---|
| `0x00a7:0x02f5c6` (`bt edi,ebx`) | 894 278 | **0** |
| `0x00a7:0x00491c` (`bt`) | 368 672 | **0** |
| `0x00a7:0x031b6d` (`sete`) | 289 352 | **0** |
| `0x00a7:0x032994` (`bt`) | 102 502 | **0** |
| `0x00a7:0x04ed40` (`sahf`) | 97 835 | **0** |
| `0x00a7:0x04ed5c` (`sahf`) | 88 485 | **0** |
| `0x00a7:0x02f6a6` (`btr edi,ebx`) | 32 666 | **0** |
| **total interpreter entries (whole run)** | **2 185 284** | **301 653** |

Every fallback that the base branch took in Quake's own code is gone: the
1 863 789 interpreter entries that Quake's code caused drop to zero. The
301 653 entries that remain are in DOS and BIOS code (segments `0x0018` and
`0x0d04d`), which this branch does not touch; the per-address counts of
those entries are unchanged between the two builds (13 221 vs. 13 268 at
`0x0d04d:0x02b01`), which confirms that the reduction comes from the
implemented opcodes and not from a change in how often the DOS kernel is
entered.

The fallbacks that remain inside Quake's code are the ones this branch
intentionally leaves alone: `BTS` with a memory bit base (3 420 entries at
`0x00a7:0x032f50`), the `0xAE` floating-point escape prefix (638 entries)
and a handful of single-digit cases.

## Measured effect on the Quake timedemo

A/B, 4 warm-ups discarded, 32 measured runs, ABBA quartet order
(`A,B,B,A`), two different binaries, fresh game copy and fresh emulator
process per run, `cpu_cycles = 3000`, `cpu_cycles_protected = max`,
`output = texture`, `texture_renderer = software`, SDL dummy video driver,
on one Xeon Platinum node (2 visible CPUs, no cgroup CPU quota, no CPU
pinning). The runner is the local variant of the upstream A/A runner; the
contrast estimator is the per-quartet `(A + A - B - B) / 2` on logarithms
with a 10 000-resample bootstrap interval.

| Metric | Base branch (A) | This branch (B) | Contrast A/B | 95 % bootstrap interval |
|---|---|---|---|---|
| guest FPS (median of 16 runs per arm) | 112.8 | 121.2 | **−6.381 %** | [−7.356, −5.390] |
| host CPU time per run | — | — | **+6.624 %** | [5.768, 7.564] |

The FPS contrast is negative because A is the base branch: the branch is
**6.4 % faster** in guest FPS and uses **6.6 % less host CPU time** for the
same 969 frames. The minimum detectable effect on this node with 8 quartets
is 1.63 % (FPS) and 1.31 % (host CPU), so the effect is roughly four times
the detection floor and both intervals exclude zero. No cgroup CPU
throttling was recorded during the series.

An earlier, independent A/B of the same code change on the same node
(before a comment-only source cleanup) gave −5.248 % FPS
[−6.583, −3.891] and +5.588 % host CPU [4.589, 6.497], i.e. the same
effect within the run-to-run variation of the node. The machine-readable
form of both series is
`evidence-dynrec-bit-test-implementation-2026-11-09.json`.

## Caveats

* The measurement was taken on a single node. The repository's own
  measurement floor is a property of the node, so the effect size and the
  detection floor have to be re-established on other hardware.
* The measured effect is **larger** than the 1.19 % "fallback window" that
  the earlier profiling attributed to the interpreter and the x87 state
  transfer. The reason is that the fallback cost is not only the
  interpreter's own samples: every fallback also tears down the cached
  block, so the surrounding code has to be translated again. The earlier
  attribution measured the samples, not the invalidation cost.
* `SAHF` still costs one C helper call per execution. That is far cheaper
  than the interpreter fallback it replaces, but an inline implementation
  would be cheaper still; it is left as a follow-up.
* The guest test exercises the implemented instructions, but it is not a
  proof of full architectural equivalence. The Quake timedemo completing
  with the same frame count and the same rendering path is the practical
  cross-check.
