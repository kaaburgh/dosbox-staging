# Quake x87 32-bit memory-read investigation

## Decision

The experiment identifies a repeatably sampled x87 memory-helper path and a functional inline-read prototype. **It does not establish a performance improvement or production correctness.** Keep `experimental-inline.patch.b64` as a research patch; it is not applied to the emulator by this branch. The present host cannot distinguish a small improvement reliably. Complete a controlled uninstrumented A/B and remaining protection/exception coverage before proposing a JIT change.

## Provenance

Source for both binaries was fork commit `91774d582069d52f4d3bf04952cb6047dbdc073f` (PR #7), with the same mapper and FluidSynth SDK compatibility shims used in PR #7. Baseline and candidate were locally built with GCC 14.2.0, Release `-O3`, trace OFF. Only the candidate additionally used `experimental-inline.patch.b64`. Baseline SHA-256 `1fb0d1f17aac13b191f3f84f13b9fad042c6956b0180911a3257c6f54433cf44`; candidate SHA-256 `06c22959c2487e1ba4f5111d49e8eda15d6807841eb617a5a48dcbb5462b20bc`. The checked-in patch is experimental and currently leaves `FPU_FLD_32` unused, which causes a compiler warning. Do not treat it as a polished product change.

The separate trace builds used `OPT_JIT_BLOCK_TRACE=ON` and `-fno-omit-frame-pointer`. Quake executable/PAK are the hash-verified originals from the PR #7 protocol, and remain private. Host: AMD EPYC 7763, 3 vCPUs visible, quota 2 CPUs, Debian 13, SDL dummy. Benchmark processes ran without affinity unless noted.

## Live snapshots and call sites

Two fresh trace + per-thread SIGPROF runs at 1100 and 1700 µs completed demo1 at 969 frames. Each captured complete guest/native snapshots for `0x10f4a`, `0x306bd`, `0x112cc`, joined to the lifecycle generation in the same process. There were 63 and 55 samples in those selected blocks; all were linear-disassembly instruction starts. The measured demo window is observed by polling, and profiler/trace perturb execution.

`0x10f4a` contains 32-bit memory `FILD`, `FMUL`, `FADD`, and `FLD`; `0x306bd` contains repeated `FLD`, `FMUL`, and `FSUB`. Matching sampled frame chains to live generations and checking the captured bytes verified return addresses immediately following indirect `call` instructions at these offsets:

| Guest EIP | Return offsets, 1100 µs (samples) | Return offsets, 1700 µs (samples) |
|---|---|---|
| 0x10f4a | 78 (4), 326 (4), 214 (3), 268 (2), 154 (2), 299 (2) | 78 (7), 181 (3), 268 (2), 127 (2), 355 (2), 100 (2) |
| 0x306bd | 224 (2), 172 (2) | 78 (3), 272 (2) |
| 0x112cc | 158 (2) | 104 (2) |

These samples have `mem_readd` as current symbol and the return after `FPU_FLD_32`'s direct call to it as the first frame. The next frame points after the call in the active selected block. The target of the indirect JIT call was not separately reconstructed from register state; the observed stack plus disassembly supports the chain, with the limitations of frame-pointer sampling. Raw guest/native bytes, maps, game assets and raw sample addresses are excluded. Top-15 candidate lists are in `fpu-attribution-*.json`; they do not represent every call site or execution counts.

## Prototype and correctness boundary

The patch replaces four `FPU_FLD_32` call sites (x87 escape groups D8, D9 FLD, DA, DB FILD) with the existing dynamic-core checked dword read, then stores the loaded word into `dyn_dh_fpu.temp.m1`. This keeps the original host x87 operation and reuses the inline TLB/cross-page/handler paths. It does change the exception path and needs independent review.

The generated native blocks demonstrably changed: for `0x10f4a`, 832 → 2137 bytes; `0x306bd`, 832 → 2373; `0x112cc`, 2504 → 2937. These ranges include deferred paths; raw size is not runtime cost. A trace-enabled candidate completed 969 frames; no claim of video/audio equivalence follows from frame count alone.

Nine independent DOS test programs were run on both binaries and on both normal/dynamic cores, **36/36 pass**. `FPU_FLAGS` checks x87 float and integer operands, page crossing, fresh data after writes, a patched instruction displacement (SMC), VGA page-handler memory and integer-flag preservation. Five paging cases enter protected mode, install an IDT and identity paging, trigger one non-present-page #PF, repair the PTE, restart the x87 instruction, and check the result and where applicable the previously loaded x87 stack value: FLD with first or second page absent, FADD, FILD and FIADD. Three additional tests enter CPL3 and read across a user/supervisor page boundary. They check the page-fault error code 5, grant user access, and verify FLD/FADD retry and x87 stack state; first- and second-page protection faults are covered. These tests remain incomplete: other privilege and protection combinations, multiple exception priorities, unmasked x87 exceptions, broad MMIO side effects, asynchronous interrupts and cache-wrap stress remain unverified.

Reproduce locally (a new empty work directory is required):

```sh
python3 extras/guest-block-mapping/fpu-read-study/run_oracles.py \
  --dosbox /absolute/path/to/dosbox --runtime-cwd /absolute/path/to/checkout \
  --work /absolute/path/to/new-work-directory --nasm /absolute/path/to/nasm \
  --sdk-lib-dir /absolute/path/to/sdk-libraries
```

Omit `--sdk-lib-dir` for a system-installed SDK. For candidate builds, apply `base64 -d extras/guest-block-mapping/fpu-read-study/experimental-inline.patch.b64 | git apply` on the exact matching source after recording the baseline; use an isolated worktree. The patch is base64-encoded to preserve exact upstream whitespace. The runner preserves logs in its work directory and requires both exit success and an exact guest `FPU_ORACLE_PASS` marker. `FPU_PF*.asm` covers supervisor-mode non-present faults; `FPU_USER*.asm` covers selected CPL3 user/supervisor read-protection faults.

## Exploratory local timings

Baseline A vs candidate B: 4 warmups, 32 measured executions in 8 randomly ordered balanced ABBA/BAAB quartets, all 969 frames. A median 107.1 FPS, B median 112.0 FPS; geometric within-quartet B/A estimate **+9.59%**, descriptive quartet-bootstrap interval **−3.27% to +26.26%**. Ten cgroup throttling events occurred across measured runs. Only eight quartets are available; observed quartet effects ranged from −10.56% to +52.32%. The interval is descriptive and may understate uncertainty under correlated host load. It neither proves improvement nor rules out a regression. The predeclared plan and rows are in `comparison-manifest.json` and `comparison.csv`; `compare_local.py` is the actual local harness and contains local absolute paths.

To check a possible noise mitigation, the original binary was separately pinned to virtual CPU 0: 4 warmups + 24 A/A measurements, 28/28 complete. FPS CV **11.02%**, p10–p90 spread **25.40%** of median; identical-binary placebo A/B **−2.04%**, descriptive interval **−10.68% to +9.22%**. Zero measured throttling events. This sequential series has fewer samples and cannot establish a causal effect of pinning versus the earlier unpinned run. It does show that this particular pinned series is not precise enough. In `summary.json`, the inherited analyzer's GitHub Actions limitation is generic boilerplate: this execution ran locally in the managed workspace.

## Next boundary

On a stable host first establish its own same-binary A/A and randomized repeated paired A/B, then test independent workloads. In parallel expand the DOS oracle for other privilege/protection combinations, unmasked x87 exceptions, side-effectful MMIO, interrupts and cycle changes. Only after evidence supports both correctness and speed should the prototype be narrowed, cleaned up, and proposed as a product patch. No speedup or general correctness is claimed here.
