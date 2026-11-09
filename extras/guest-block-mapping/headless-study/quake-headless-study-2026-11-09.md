# Quake 1.06: headless A/A stability and exploratory host sampling

## Decision

This managed container is useful for semantic and code-path research. The observed A/A spread does not support a credible 1–3% speedup claim from a similarly sized local comparison. Continue attribution before changing the JIT. This is one session and one environment, not a general characterization of all Codex environments.

## Fresh fork build and workload

- Fork commit `66884b444e6497fd13b3ac8ff5501af53b02a3ed` (PR #6, stacked on #5) built locally in this session, Release, `OPT_JIT_BLOCK_TRACE=OFF`, GCC 14.2.0, CMake 3.31.6, Debian trixie extracted development packages. Binary SHA-256 `992fb3eb5899e7dcf00b9b9cc2868cb1ba36bcdb03528728ed7426a9c8b3a9d7`.
- Two historical compatibility source shims: remove the unused mapper SDL texture-scale call and unused MIDI FluidSynth portamento setting; point `libiir_static.a` to shared `libiir.so`. No CPU/JIT source was modified. Build library resolution and local paths differ from CI. Fresh source is not byte-identical to git HEAD because of these documented shims.
- Official benchmark archive SHA-256 `bd928b86e3bd1dc7ffeea88b11e757005be38bc8971aa228ee004d5d23a35a60`, Quake EXE SHA-256 `7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb`, PAK0 SHA-256 `35a9c55e5e5a284a159ad2a62e0e8def23d829561fe2f54eb402dbc0a9a946af`. Neither game files nor logs or raw JIT code are committed.
- SDL3 `dummy` video driver, software texture, dummy audio, dynamic core, Pentium, protected-mode cycles max, Quake `demo1` 320x200. The sandbox denied Unix sockets (`EPERM`), so Xvfb was not usable. SDL dummy differs from the Xvfb baseline; do not compare absolute FPS across modes.
- AMD EPYC 9V74, 9 CPUs visible in affinity. CPU quota and throttling were not exposed. Blank values mean unavailable, not zero.

## A/A result

Four warmups plus 32 measurements, all 969 frames. Each quartet was A,B,B,A, with the exact same executable under both labels, fresh game copy and new process per run. Individual records and manifest are alongside this report.

| Measure | Result |
|---|---:|
| FPS median / mean | 139.5 / 137.084 |
| FPS min–max | 119.3–144.6 |
| p10–p90 | 128.32–143.17, 10.645% of median |
| population CV | 4.722% |
| A/B placebo geometric estimate | −1.382% |
| descriptive quartet-bootstrap interval | −3.618% to +0.855% |
| first / second half medians | 139.35 / 139.5 |

The interval is descriptive, assumes independent quartets, and is not a calibrated guarantee of accuracy. One A/A comparison cannot determine the minimum detectable effect or false-positive rate. The recorded counter values are deliberately blank; log lines from the original run printed zero before postprocessing, which is **not** a throttling measurement. The attached runner was corrected after this experiment to print blank counters too; it otherwise preserves the executed protocol. The summary script likewise uses null for missing cgroup data and the manifest is sanitized for hostname.

The earlier 48-run Xvfb A/A experiment in PR #6 found a different bogus −2.591% estimate from identical binaries; it used a previously built binary and a separate execution, so no pooling or direct difference-in-FPS inference is justified.

## New exploratory host sampling

After the A/A series ended, used the same trace-compiled-out binary plus the existing thread-CPU-time SIGPROF preload (SHA-256 `9edffde3bcd0170e5187e9dcd138f8f66d896252aacef6d46b74f223ba255853`), with periods 1100 and 1700 µs. Two complete 969-frame runs. Samples counted only for main TID and the *observed* demo window. Each run captured `/proc/<pid>/maps` once at observed demo start; the script resolves DOSBox executable addresses using its `nm -S` symbols and exact executable mapping. Both runs printed profiler `lost=0`. Sampling perturbs execution and the window includes polling error.

| Quantity | 1100 µs | 1700 µs |
|---|---:|---:|
| main-thread samples in observed window | 1944 | 2063 |
| `mem_readd<normal>` | 75 | 71 |
| `CPU_Core_Dyn_X86_Run()` | 37 | 33 |
| `mem_writed_checked` | 27 | 27 |
| `MakeCodePage` | 24 | 19 |
| `FPU_FLD_32` | 23 | 24 |
| anonymous mappings | 821 | 895 |
| not in captured map snapshot | 119 | 126 |

The function symbols show repeatable places to inspect, not costs of the nine selected guest blocks or evidence of unnecessary work. In particular, `FPU_FLD_32` calls `mem_readd` in `src/cpu/core_dyn_x86/dyn_fpu_dh.h`; this is one plausible relationship, not proof that all `mem_readd` samples originate there. The sampled `mem_readd` symbol is not an H1 byte access, so it must not be used to confirm H1. Samples in anonymous mappings are not automatically classified as JIT: there is no generation trace or match to individual blocks in this experiment. One maps snapshot can miss later mappings; no caller stacks were captured. libc, dynamic linker and SDL library samples remain mostly unsymbolized and profiler overhead/periodic aliasing remain plausible confounders. Do not add sample counts from nested functions as a call-chain total.

## Next discriminating test

Use same-process generation-aware JIT tracing with live snapshots, while collecting unwound or otherwise verified call-site evidence for the non-JIT memory and FPU helpers. Separate generated fast paths, helper callers, VGA/other page handlers and decoder work. Verify that observed samples fall in demo time and that instrumentation does not change guest correctness. Use focused guest tests covering page boundaries, exceptions, self-modification and x87 state before considering a targeted JIT patch. Final performance A/B belongs on a more stable host with a separate A/A control.

The helper scripts are research artifacts; `run_guest_headless_profile.py` is a local SDL-dummy adaptation of PR #5's runner, and `analyze_headless_host_samples.py` consumes private captures and outputs derived-only counts. Captures and executable/PAK must remain private. The output JSONL contains no raw code or addresses.
