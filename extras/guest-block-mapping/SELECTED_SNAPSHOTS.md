# Selected guest/native JIT snapshot research (stacked on PR #1)

Goal: collect evidence for [the H1–H5 hypotheses](https://github.com/kaaburgh/configs/blob/bench/quake-p0-p1-local-builds/benchmarks/quake-staging/research/hypotheses.md) using *unmodified* DOS Quake 1.06, before attempting a DOSBox optimization.

This is **block-level** instrumentation. A native block's bytes may include prologues, exits and deferred exception/slow paths; it is **not** a per-guest-instruction-to-native-instruction map.

## Configuration

Configure DOSBox Staging on Linux/x86-64 with `-DOPT_JIT_BLOCK_TRACE=ON`. Runtime instrumentation remains disabled unless explicitly enabled:

```sh
mkdir -m 700 -p /tmp/quake-jit-snapshots
DOSBOX_JIT_BLOCK_TRACE=/tmp/quake-jit-blocks.csv \
DOSBOX_JIT_SNAPSHOT_DIR=/tmp/quake-jit-snapshots \
DOSBOX_JIT_SNAPSHOT_EIPS=0x10d3b,0x11216,0x112cc,0x11343,0x1143f,0x3e233,0x3e29a,0x3e3ef,0x3e455 \
DOSBOX_JIT_SNAPSHOT_MAX=16 \
  /path/to/instrumented/dosbox --conf /path/to/benchmark.conf
```

The snapshot directory **must exist**, be writable and ideally private; the lifecycle trace CSV must not exist before execution. Only the explicitly selected *guest EIP* entries are eligible. The maximum snapshots default to 16; at most 32 EIPs and 256 snapshots can be configured. A snapshot is counted once attempted, including when a write fails.

Files are named `jit-PID-genID-csCS-eipEIP.guest.bin`, `.host.bin`, and `.json`. The JSON contains generation ID matching the lifecycle CSV, guest CS:EIP, guest linear range and exact native interval, lengths, capture status and basenames. The guest and host binary files are each written with exclusive creation permissions 0600.

**Privacy:** the guest file can contain commercial game's machine code. Keep raw snapshots local or in private short-lived artifacts, never commit them to public GitHub.

## Important correctness boundary

The guest snapshot is captured **immediately after** the decoder finishes the block, using only already readable direct guest TLB mappings. This avoids new guest `mem_readb()` calls, paging side effects, exceptions, or MMIO access introduced by the research tool. When a direct map is absent the snapshot stops and `guest_complete=false`; it does not fabricate missing bytes.

This is a **post-decode memory image**, not a byte-by-byte recorder of the decoder's individual fetches. In code that changes during compilation or executes from unusual memory handlers, it might differ from what the decoder fetched. On ordinary Quake protected-mode RAM it is expected to be useful, but the metadata must still be verified; capturing exact decode-time bytes across all fetch paths would be a separate, more intrusive experiment.

The complete native interval is taken after emission and includes deferred helper/exception paths. Comparing disassemblies can identify plausible call/branch overhead but cannot by itself establish a precise guest-instruction → host-instruction range mapping.

## Initial targets

| Quake EIP | Candidate function | Hypothesis |
|---|---|---|
| `0x11216`, `0x112cc`, `0x11343`, `0x1143f` | `D_DrawSpans8` family | H1: guest memory translation and helper calls |
| `0x10d3b` | `D_DrawZSpans` | H2: packed Z writes and host register shuffles |
| `0x3e233`, `0x3e29a` | `R_DrawSurfaceBlock8_mip0` | H3: runtime-patched texture displacement |
| `0x3e3ef`, `0x3e455` | `R_DrawSurfaceBlock8_mip1` | H3: repeated mip1 table loads |

Source attribution is from original Quake 1.06 x86 comparisons with later id Software ASM, not debug symbols in the old executable. Treat names as hypotheses unless disassembly confirms boundaries.

## Validation

```sh
c++ -std=c++20 -O2 -Wall -Wextra -Werror \
  extras/guest-block-mapping/test_jit_selected_snapshot.cpp -o /tmp/jit-snapshot-test
/tmp/jit-snapshot-test
```

The autonomous test verifies EIP filtering, exact guest/native bytes, the lifecycle generation key and incomplete cross-page snapshots. The planned integration test will use original DOS Quake 1.06, compare live guest bytes with the embedded COFF .text at corresponding EIPs, and disassemble the generated native blocks locally.

**Review order:** this is a stacked research PR based on `experiments/quake-guest-block-mapping`. Do not merge it to `main` before PR #1 is merged/rebased; the changes in this PR depend on `jit_trace_id` from PR #1. This PR deliberately does not change x86 instruction semantics or claim any FPS improvement.
