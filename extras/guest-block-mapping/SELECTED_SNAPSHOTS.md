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

## Completed local Quake integration (2026-11-07)

The diagnostic binary built from **source commit `630e0533c344fed69b0838d3d71c373a1545d5f6`** executed the unmodified DOS Quake 1.06 `demo1` (969 frames) twice in the assistant's local Linux x86-64 environment.

- Run A: 7,845 CREATE / 185 INVALIDATE, **9/9** selected snapshots complete.
- Run B: 7,850 CREATE / 185 INVALIDATE, **9/9** selected snapshots complete.
- Each snapshot's generation and guest/native intervals matched the matching event in its run's JIT block lifecycle CSV. Binary payload lengths matched metadata.
- Guest bytes and the sequence of native JIT mnemonics **match across the two runs for all 9 EIPs**; raw native bytes differ due ASLR/host pointer values.
- **Four surface mip blocks** had the original `0x12345678` x86 displacement replaced in live guest memory by `0x0018007c` (6, 7, 6 and 2 occurrences); the other five blocks matched the original Quake 1.06 COFF bytes.
- Native disassembly shows fast TLB memory accesses, conditional branches, and helper fallbacks. **Static call-site counts are not executed-call counts**, and full generated blocks include deferred slow paths.

The derived per-block report is stored in
[configs/research/selected-guest-native-snapshots-2026-11-07.md](https://github.com/kaaburgh/configs/blob/bench/quake-p0-p1-local-builds/benchmarks/quake-staging/research/selected-guest-native-snapshots-2026-11-07.md).
Raw snapshots contain game machine code and remain private. No benchmark speedup
or native-instruction-level attribution is claimed.

**Runtime integration detail:** DOSBox Staging 0.84 needs its Unicode mapping
resources to be locatable. Running the executable from a directory without
`resources/mapping` makes the mounted game directory appear empty
(`Illegal command: quake.exe`). The external local runner now sets its working
directory to the fork checkout containing `resources`.
