# Experiment: inline the existing dword read inside the x87 helper

## Result and decision

The tested one-line change makes `FPU_FLD_32` call `mem_readd_inline<MemOpMode::WithBreakpoints>(addr)` rather than the out-of-line `mem_readd(addr)`. The latter's implementation is exactly the same inline template instantiation. This tests whether removing the additional C++ function boundary in the heavily exercised helper is useful; it does not eliminate the call from generated JIT code to `FPU_FLD_32`.

The Release compiler here changed the helper's shape as expected. Focused semantics tests passed, including x87 arithmetic, masked special values and VGA memory-handler reads. A local randomized A/B showed an exploratory +4.08% geometric estimate with a wide descriptive interval including zero, and a strong outlier; **this is not evidence of a reliable speedup**. Keep as an experimental draft until dedicated-host measurement and missing correctness tests are available.

## Compiler evidence

Fork source is PR #8 head `c50d2fa10e024aa4e90f10646e40f9b01d685c58` plus this one-line experimental change. Baseline was built earlier from `66884b444e6497fd13b3ac8ff5501af53b02a3ed` with two Debian SDK shims; the intervening PR #7/#8 code is optional research instrumentation, compiled out in both Release benchmark builds. The candidate uses the same SDK shims, GCC 14.2.0, `-O3 -DNDEBUG`, SDL3 system packages and `OPT_JIT_BLOCK_TRACE=OFF`. Benchmark binary hashes:

- baseline: `992fb3eb5899e7dcf00b9b9cc2868cb1ba36bcdb03528728ed7426a9c8b3a9d7`;
- candidate: `078b1d386d4b6d0b6aea097f08d0b9760fed490132b321f50cd76370b2373fb0`.

`inspect_helper.py` and `helper-shape.json` record the observed machine-code shape for these exact binaries. The baseline `FPU_FLD_32` is 20 bytes and calls the out-of-line `mem_readd<WithBreakpoints>`. The candidate helper is 120 bytes, and its fast path directly checks the page boundary and read TLB then loads the dword. Its slow paths still invoke the page handler or unaligned read. This is compiler-dependent; the sizes do not themselves prove speed. The original out-of-line `mem_readd` remains used elsewhere, so inlining also grows total code and could affect the instruction cache. A false result in `has_unaligned_read_reference` for the baseline helper only means that the reference is in its called `mem_readd` function, not that baseline lacks an unaligned path.

The template explicitly retains `WithBreakpoints`; in the source the same debug-breakpoint hook, direct TLB lookup, handler fallback and unaligned-read behavior are selected. This is source reasoning, not a substitute for executing debugger and protected-mode fault cases.

## Functional checks

- PR #8's four fixtures ran 24/24 combinations: baseline trace-OFF, candidate traced inactive and candidate traced active; normal/dynamic cores; FPU cross-page roundtrip, VGA `REP MOVSD`, SMC and cross-page code invalidation. The additional stride-one run confirms candidate x87 helper load and store execution at offsets `0xFFD`, `0xFFE` and `0xFFF` across a guest page boundary. See `pr8-semantic-matrix.json` and `fpu-all-call-results.json`.
- `fpuarith.asm`: 128 combinations of four x87 memory operations (`fadd`, `fsub`, `fmul`, `fdiv`), four rounding modes, and eight operands including signed zero, denormal, infinity, quiet and signaling NaNs with exceptions masked. Each case stores output bits and x87 status. All 768 output bytes matched across baseline normal/dynamic, candidate normal/dynamic, and candidate traced inactive/active. The active probe captured 256 records.
- `fpummio.asm`: with VGA mode 13h, x87 reads values from VGA linear addresses `0xA0FFD`, `0xA0FFE`, `0xA0FFF` and compares output bits. The same six cases passed; stride-one recording confirms four x87 helper reads per address. This checks a page-handler/MMIO path rather than only direct RAM.
- `run_tests.py` reproduces the twelve comparison cases; `semantic-results.json` contains the actual results. Fixture output is synthetic; no game bytes are included.

**Still untested:** protected-mode faults and privilege/restart state, enabled/unmasked x87 exception delivery, debugger memory breakpoints, all device mappings, and other architectures/toolchains. These gaps matter before upstreaming even though the same underlying read template is used.

## Local randomized A/B

Quake 1.06 `demo1`, 969 frames, dynamic core, SDL dummy/software renderer, four warmups and 32 measured runs: eight randomized `ABBA`/`BAAB` quartets, 16 runs of each binary. All 36 completed 969 frames. A = original, B = candidate. Each run used a clean game directory and no profiling/tracing. Quake EXE SHA-256 `7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb`; PAK0 SHA-256 `35a9c55e5e5a284a159ad2a62e0e8def23d829561fe2f54eb402dbc0a9a946af`. Exact labels, order, settings, hashes, individual runs and calculation are in `ab-manifest.json`, `ab-timedemos.csv`, `ab-summary.json`, and `benchmark_ab.py`.

| Metric | Observation |
|---|---:|
| A / B median FPS | 137.5 / 141.25 |
| A / B min–max FPS | 108.0–144.1 / 123.0–148.4 |
| Quartet geometric B/A estimate | +4.08% |
| Descriptive quartet-bootstrap interval | −0.36% to +10.37% |
| Median of eight individual quartet effects | +2.32% |

One quartet produced +25.08%: its two A runs were 118.1 and 108.0 FPS while B was 144.4 and 138.2. As a **post-hoc sensitivity check**, removing this quartet gives +1.38%; no runs were removed from the reported primary estimate. With eight quartets, potential serial dependence, unknown CPU throttling, and the prior same-host A/A pseudo-effect of −1.38% in PR #7, neither the descriptive bootstrap nor this point estimate establish a speedup. An artifact-hashing command ran briefly during the measured series, so it was not strictly isolated. The first attempted A/B was interrupted during startup because a functional test ran concurrently; none of that attempt's measurements are included.

Quake completion proves the timedemo finished, not that every rendered pixel was compared. No performance assertion generalizes to other DOS software, CPUs, compilers or render backends.

## Reproduction / next gate

1. Build baseline and candidate Release binaries from the recorded commits with trace OFF using the same toolchain and SDK; build the candidate again with trace ON for the probe-dependent correctness tests. The two SDK shims and libiir link are described in PR #7.
2. Run `run_tests.py` with `--baseline`, `--candidate`, `--candidate-traced`, `--runtime-cwd`, `--out` and `--nasm`; use PR #8's `run_semantic_matrix.py` for its inherited fixtures.
3. Run `benchmark_ab.py` with `--runner executed-headless-runner.py`, explicit baseline/candidate/game/runtime paths, private and output directories, and the recorded seed/quartet count. Missing cgroup data appears as blank in the CSV, despite the original helper's pre-correction progress output printing zero.
4. Before considering a merge, add meaningful protected fault/debugger coverage and run randomized paired A/B with A/A controls on a low-noise target plus another workload. Evaluate code-size and performance regressions, and preserve exception and debugger semantics.


## Follow-up validation — 2026-11-09

Protected-mode non-present read faults recovered equivalently in normal and dynamic cores, and a heavy-debugger harness confirmed the read-watchpoint flag and stop decision. Unmasked x87 #MF delivery remains a **pre-existing emulator limitation** in the tested case: both builds set the status flag but neither delivered vector 16 with CR0.NE set. This is a known failing architectural boundary, not a proof of full x87 exception correctness. Additional x87-loop and Doom 1.9 measurements on a different, noisy host were inconclusive against A/A controls. See [the detailed follow-up](../x87-validation/REPORT.md) for test boundaries, data and reproducible fixtures.
