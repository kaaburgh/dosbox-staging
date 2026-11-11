# Phase 2 validation runner

One command that independently re-checks the Phase 2 x86-64 dynamic-core
experiments on your own machine and produces a ZIP you can hand to a reviewer
(a person or a chat assistant).

It validates, against `main` at the Phase 2 baseline (`27b1b7c`):

| Label | What | Source |
|---|---|---|
| `base`, `base2` | the baseline, built once and run under two labels (A/A placebo) | `27b1b7c` |
| `c4` | PR #14: guest flags restored without `POPFQ` | `refs/pull/14/head` |
| `c1a` | PR #15: inline x87 m32 load | `refs/pull/15/head` |
| `c2` | PR #16: inline PUSH/POP | `refs/pull/16/head` |
| `c6` | PR #17: BT/SETcc/SAHF/LAHF translated | `refs/pull/17/head` |
| `all4` | C4+C1a+C2+C6 integration, measurement only | branch `phase2/integration-local` |
| `c3`, `c1b` | optional (`--with-optional`): PR #18, PR #19, neutral in Phase 2 | `refs/pull/18/head`, `refs/pull/19/head` |

Steps it runs:

1. **Sources.** Fetches the commits into a private bare repository under the
   work directory. Your checkout is only read, never modified. The exact SHA of
   every variant is recorded and compared with the Phase 2 heads.
2. **Builds.** Builds every variant with the project's CMake presets in a
   runner-owned worktree. By default variants are checked out in turn and
   rebuilt incrementally; at the end the baseline is rebuilt and must give a
   bit-identical binary. `--build-mode separate` uses one worktree and a full
   build per variant instead. It records compiler, CMake cache flags, the
   compile command of `core_dyn_x86.cpp` and the SHA-256 of every binary.
3. **Guest tests.** Assembles the DOS tests in `../tests` with NASM and runs
   them on the normal and dynamic cores of every variant.
   - Verdicts use expectations from the Intel SDM, not agreement with the
     normal core.
   - Output must also be identical to the baseline's.
   - Two documented pre-existing deviations of the unmodified emulator are
     reported as KNOWN, never as PASS: x87 `#PF` imprecision in the dynamic
     core, and the normal core's wrong EIP for a faulting `CALL` push.
   - Negative-control builds of the tests (`-DNEG`) must be reported as failing.
4. **Quake 1.06.**
   - Files: downloads the shareware `quake106.zip` (or uses `--quake-zip` /
     `--quake-dir`), unpacks `resource.1` with a built-in LHA decoder and checks
     SHA-256 of `QUAKE.EXE`, `CWSDPMI.EXE` and `ID1/PAK0.PAK`.
   - Benchmark: `timedemo demo1` at fixed 1,000,000 cycles, so the guest work
     is identical in every run; randomised balanced blocks with the A/A
     placebo; DOSBox pinned to two CPUs.
   - Metric: main-thread CPU time for the demo window.
5. **Analysis.**
   - Determinism: every run's guest result must equal the label's usual result
     and the Phase 2 reference. Deviations are kept in `diagnostics/`, flagged
     in the summary and their blocks are dropped from the statistics.
   - Statistics: bootstrap 95% CI of the speed-up against the two baseline
     copies, plus the A/A check.
6. **ZIP.** Writes the archive with CSV/JSON/text only. Local paths, user and
   host names are replaced by `<HOME>`, `<WORK>`, `<REPO>`, `<TMP>`, `<USER>`
   and `<HOST>`. No game files, binaries or JIT dumps are included.

Partial results are always packed, also after a failure or Ctrl+C. Exit code
0 means everything checked out; 5 means the run completed but the summary
lists something needing attention; 1 means a stage failed; 2 means missing
prerequisites; 3 means the host architecture is not applicable.

## Modes and time

| Mode | Benchmark | Measured on the verification host (4 vCPU) |
|---|---|---|
| `--mode quick` | 3 blocks + 1 warm-up (22 runs) | ≈9 min first run (5 min baseline build), ≈3 min afterwards |
| `--mode full` | 20 blocks + 2 warm-ups (142 runs) | ≈15 min after the first build; ≈19 min with `--with-optional` (182 runs) |

`quick` checks the whole pipeline (builds, all guest tests, determinism,
packaging). Its 3 blocks cannot resolve the effects, so its timing is always
marked INCONCLUSIVE. Use `full` for timing. Timing counts as conclusive only
when the A/A placebo is consistent with zero and its 95% CI half-width is at
most 3% (`--max-aa-resolution`). On a noisy host, add `--blocks` or quieten
the machine.

The first build of the baseline is a full DOSBox Staging build. With vcpkg it
also builds all dependencies once, which can take 30–60 minutes. The build
tree is reused by later runs in the same `--work` directory; variants then
rebuild in seconds.

## Linux x86-64

Install the build prerequisites from [docs/build-linux.md](../../../docs/build-linux.md)
plus NASM, then run from the repository root:

```bash
python3 extras/jit-audit/validate/phase2_validate.py --mode quick
```

```bash
python3 extras/jit-audit/validate/phase2_validate.py --mode full
```

Debian/Ubuntu package list used for verification (Debian 13):

```bash
sudo apt-get install -y git cmake ninja-build g++ pkg-config python3 nasm libasound2-dev libfluidsynth-dev libiir-dev libpng-dev libopusfile-dev libspeexdsp-dev libsdl3-dev libsdl3-image-dev libasio-dev zlib1g-dev libgl-dev
```

Notes:

- Default preset `release-linux` (system libraries); `release-linux-vcpkg` is
  used when `VCPKG_ROOT` is set, or pass `--preset`.
- Configure options go through `--cmake-arg=...`, one per option and with `=`,
  e.g. `--cmake-arg=-DOPT_MT32EMU=OFF` when libmt32emu is not packaged.
- Some distributions ship SDL3/FluidSynth versions that lack two functions
  used by the mapper UI and the MIDI synth. `--compat-shims` comments out
  those two calls in every variant identically (not CPU code) and records it
  in `builds.json`.
- The configuration verified for this PR, on Debian 13 (GCC 14.2):

  ```bash
  python3 extras/jit-audit/validate/phase2_validate.py --mode full --compat-shims --cmake-arg=-DOPT_MT32EMU=OFF --cmake-arg=-DOPT_TESTS=OFF --cmake-arg=-DOPT_DOCUMENTATION=OFF
  ```

## Windows x86-64

> **Verified on GitHub Actions only** (`windows-2025-vs2026`: Windows Server
> 2025, Visual Studio 2026, clang-cl 22, vcpkg, AMD EPYC, 4 vCPU). Not yet
> run on a desktop Windows machine. Please report the first results,
> including failures; the ZIP is produced even if a stage fails.

Prerequisites, as in [docs/build-windows.md](../../../docs/build-windows.md):

- Visual Studio 2026 or 2022 with **C++ Clang tools for Windows**;
- vcpkg with `VCPKG_ROOT` set;
- Git, CMake and Python 3.10+;
- NASM: `winget install NASM.NASM`.

Use a short work directory. vcpkg build trees exceed the 260-character path
limit under deep paths. From the repository root, in PowerShell or a
Developer Command Prompt:

```powershell
py -3 extras\jit-audit\validate\phase2_validate.py --mode quick --work C:\p2v
```

```powershell
py -3 extras\jit-audit\validate\phase2_validate.py --mode full --work C:\p2v
```

Notes:

- Preset: `release-windows` (Visual Studio 2026), or `release-windows-vs2022`
  when vswhere reports VS 2022. Override with `--preset`.
- CPU time is read with `GetThreadTimes` for DOSBox's main thread (the
  earliest-created thread), which has clock-tick resolution. The
  cycle-accurate `QueryThreadCycleTime` is recorded as
  `demo_thread_cycles`, and a second analysis with it is added.
- Affinity uses `SetProcessAffinityMask`; CPU topology (SMT, L3 groups) comes
  from `GetLogicalProcessorInformationEx`.
- Power state comes from `GetSystemPowerStatus` and `powercfg`. Temperature
  is reported only where WMI exposes it without administrator rights.
- With the Clang/MSVC toolchain the LAHF/SAHF detection of PR #17 is compiled
  out, so on Windows C6 keeps SAHF/LAHF in the interpreter fallback. Only its
  BT/SETcc part is active.

## macOS (Apple silicon) and other ARM64 hosts

Not applicable, and the runner says so and stops:

- `core_dyn_x86` is compiled only when CMake targets AMD64/x86_64
  (`C_DYNAMIC_X86` in the top-level `CMakeLists.txt`,
  `src/cpu/CMakeLists.txt`). ARM64 builds use the dynrec core, so the code
  changed by PRs #14–#17 and #19 is not in the binary and every variant
  behaves identically.
- The optional PR #18 also refactors `src/cpu/dyn_cache.h`, which dynrec
  includes too (`CacheBlock::Unlink` split out of `Clear`, plus a field dynrec
  does not use). That refactor is compiled on ARM64, but the feature it serves
  exists only in `core_dyn_x86`, so there is nothing to benchmark. Checking
  dynrec with the refactor would need a separate correctness test plan.
- An x86-64 build under Rosetta 2 would measure the translator's handling of
  `POPFQ`/`PUSHFQ`, not an x86-64 CPU.
- Intel Macs run `core_dyn_x86`, but this runner has no per-thread CPU-time
  backend for macOS yet.

## Useful options

```text
--with-optional              also PR #18 (c3) and PR #19 (c1b)
--variants c4,c1a            subset (base is always included)
--work DIR / --out-dir DIR   work directory / where the ZIP goes
--offline                    use only refs present in your clone (shallow clones work)
--ref c4=<rev>               test another revision under a label
--cpus auto|none|2,3         CPU affinity of DOSBox during the benchmark
--blocks N --warmups N --seed N
--quake-zip FILE | --quake-dir DIR | --no-download
--skip-build / --skip-tests / --skip-bench   reuse or skip stages
```

## What the ZIP contains

`summary.md`, `summary.json`, `metadata.json`, `sources.json`,
`builds.json`, `quake.json`, `tests/` (raw outputs and `verdicts.json`),
`bench/` (`runs.csv`, `manifest.json`, `stats.json`, `determinism.json`),
`diagnostics/`, `logs/`, `status.json` and `README_FOR_REVIEWER.md`. The
reviewer README lists what to check.

Phase 2 reference results (Intel Emerald Rapids VM, Linux) for comparison:
C4 +11.1% [+8.8, +13.4], C1a +9.1% [+7.0, +11.3], C6 +8.8% [+6.8, +11.0],
C2 +3.4% [+1.2, +5.8], all4 +37.1% [+29.5, +44.2]; C3 and C1b not
significant. Effects on other CPUs (AMD in particular) are expected to differ.
