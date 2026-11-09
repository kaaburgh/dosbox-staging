# Follow-up: protected faults, debugger watchpoints, unmasked x87, and other workloads

## Outcome

The PR #9 one-line candidate and baseline agree in all tested runs. A protected-mode page-fault fixture recovered successfully on both cores and both builds; a heavy-debugger harness verified the actual read-watchpoint flag and `DEBUG_HeavyIsBreakpoint()` decision; and four unmasked x87 cases produced identical status/control state. **The unmasked exception-delivery test is not a full pass:** the baseline and candidate both failed to deliver the observed divide-by-zero as protected-mode `#MF`, an existing limitation visible in both cores. Additional local timings for synthetic x87 and Doom 1.9 are inconclusive relative to their A/A controls.

## Provenance and setup

- Fork PR #9 head `bfd4b98012bb0ce5601ec5ee77d95660fdcabf71`; baseline is its immediate parent PR #8 head `c50d2fa10e024aa4e90f10646e40f9b01d685c58`. Same checkout/toolchain, GCC 14.2.0, `-O3 -DNDEBUG`, Linux x86-64, same two non-CPU Debian SDK shims and shared `libiir` workaround as earlier reports. Release `OPT_JIT_BLOCK_TRACE=OFF`. Candidate and baseline rebuilt sequentially with just the x87 line changed:
  - baseline SHA-256 `369a5d31a0b23d41f7de6641a8c8ed5425a550739c58ddfa537de67b87458b67`;
  - candidate SHA-256 `af1c324492f94d0128ad86542b9051d0a5ec3e1f7209689a036db7af8b36fac8`.
- Fresh managed Linux container; CPU model `AMD EPYC 7763 64-Core Processor`, nine CPUs visible in affinity. Quota/throttling statistics were not accessible. It is a different execution environment from PR #9's EPYC 9V74 observations; do not pool those numbers. SDL dummy video / software texture and dummy audio.
- Heavy-debugger binaries use `OPT_HEAVY_DEBUGGER=ON`, `OPT_JIT_BLOCK_TRACE=ON`, and `-DX87_DEBUG_TEST_HOOK` with the separately stored `debug-test-hooks.patch`. These extra hooks are only for tests and are **not included in the product change**. Heavy baseline SHA-256 `c10135a08586638853af1904c1cfb0f3af6e65d5db9e9396b1d436b6d0bbb6cc`, heavy candidate SHA-256 `097b2b24d163d8414e0e96c56893bdd27e52357f9cc9051225968e12a726b885`.

## Protected-mode page-fault recovery

`pagefault.asm` is a self-contained DOS COM fixture. It enters 32-bit protected mode, creates its own GDT, IDT and paging tables, and performs two `fld dword` reads:

1. A dword whose page is initially not present.
2. A dword beginning at page offset `0xFFE`, with its first page present and the next page absent.

The #PF handler installs the missing page and retries. All four baseline/candidate × normal/dynamic cases completed with identical 32-byte output: two faults; `CR2=0x00800000` and then `0x00801000`; page-fault error codes both zero (supervisor, read, not present); two recovered values `0x3f800000`; error marker zero. This tests actual protected paging and successful resumption; it does not test present-page protection faults, CPL3 permissions, or every restart side effect.

The separately instrumented heavy-debugger dynamic runs also recorded `FPU_FLD_32` with address `0x00800FFE` and the same two-fault result, for both builds (`pf-trace-results.json`). The first access at `0x00800000` was **not** recorded at that helper; do not claim that first fault occurred inside it. The cross-page instruction is the positive helper-path evidence. Raw trace and runtime addresses are excluded from Git.

## Memory read breakpoint

The regular Release build has no heavy-debugger read-watchpoint hook. Test binaries were therefore rebuilt with the actual heavy debugger enabled and with test-only code from `debug-test-hooks.patch`. Just before the first real `FPU_FLD_32` read, that hook installs two real debugger `CBreakpoint` objects: a matching `BKPNT_MEMORY_READ` and an unrelated address. The read takes the production `DEBUG_UpdateMemoryReadBreakpoints<uint32_t>` path. The harness then observes the flags and explicitly invokes the real `DEBUG_HeavyIsBreakpoint()` decision before removing both breakpoints.

Both builds yielded `match=1, unrelated=0, stop=1`; both produced the same 768-byte x87 arithmetic result hash `386106ecbe7b50b3caf5f61c7c42e030bfafbe230f3662f04844644ed4bdfdde`, with 256 helper records. This verifies the read-hook and debugger stop decision through the actual helper path in this harness. The harness calls the stop-check explicitly and removes the watchpoints to keep the session headless: it does **not** prove GUI interaction, ordinary user setup, or automatic stop/resume at the regular instruction boundary.

## Unmasked x87 exception delivery

`multiunmask.asm` tests invalid, divide-by-zero, overflow and precision with the relevant mask bit clear; it records x87 status/control and the real-mode INT 10h and IRQ13-vector 75h handler counts. All four baseline/candidate × normal/dynamic results matched:

| Case | Status word | Control word | INT 10h / IRQ13 counts |
|---|---|---|---|
| invalid | `0x3801` | `0x037e` | 0 / 0 |
| divide by zero | `0x3804` | `0x037b` | 0 / 0 |
| overflow | `0x0228` | `0x0377` | 0 / 0 |
| precision | `0x3a20` | `0x035f` | 0 / 0 |

The real-mode IRQ setup is observational and does not independently prove all PIC/IRQ13 configurations. A separate stronger `pmmf.asm` installs a protected-mode vector-16 handler, sets and reads back `CR0=0x80000031` (`PE`, `PG`, and `NE` enabled), executes unmasked x87 divide by zero and `FWAIT`, and records the exception count: **zero in all four builds/cores**; status is `0x3804`, control `0x037b`. A host native x87 control with the same unmasked divide and `fwait` produced `SIGFPE` (`FPE_FLTDIV`, si_code 3); this is an independent host/OS control, not a full reproduction of the guest environment. In the current source both dynamic x86 and normal core implement `WAIT/FWAIT` as a no-op; the dynamic x87 control-word setup also masks host x87 exceptions. These observations support an existing exception-delivery limitation, not a regression newly introduced by the candidate. The candidate has not been proven correct against a fully faithful unmasked-x87 implementation.

`run_fault_cases.py` reproduces all sixteen COM cases and fails on incorrect expected page-fault recovery or baseline/candidate differences. It records the #MF observation without treating absence as a successful architectural oracle. `run_debug_checks.py` reproduces the four heavy-debugger checks using the specially instrumented binaries. Their derived outputs are retained in the accompanying JSON files; do not expose private runtime trace files.

## Additional workloads

All measurements are exploratory trace-OFF Release runs on the EPYC 7763 container, without deliberate concurrent builds. Each workload used two warmups, one same-binary A/A `ABBA` quartet, and two fixed counterbalanced A/B quartets (`ABBA`, `BAAB`): **2 A and 2 B in the A/A placebo, 4 baseline and 4 candidate in A/B**. Fresh game/fixture directory each run. No samples were excluded. Wall time includes DOSBox startup and shutdown and is therefore not pure guest execution time; all process times and individual rows are in `other-bench.csv`. The fixed order, very small sample, unknown host contention and adaptive `cpu_cycles=max` preclude a precise performance estimate.

| Workload | A/A same-binary A / B median wall | A/A B vs A | A/B baseline / candidate median wall | A/B B vs A |
|---|---:|---:|---:|---:|
| Synthetic 20M iterations of x87 `fadd` + `fmul` memory operands, checksum verified | 4.731 / 5.055 s | +6.85% | 5.455 / 5.267 s | −3.44% |
| DOS Doom 1.9 shareware `-timedemo demo3`, 2134 gametics | 50.626 / 46.070 s | −9.00% | 53.574 / 53.348 s | −0.42% |

The table compares medians and does not exploit pairing. As a separate paired sensitivity analysis on log wall times, the two A/B quartets individually gave **+7.80% and −24.76%** for the x87 loop and **+15.90% and −21.84%** for Doom (B relative to A; lower is faster). Their paired geometric aggregates are −9.94% and −4.82%, respectively. With only two heterogeneous quartets, these aggregates are highly unstable; they are included to expose the order-sensitive evidence, not to replace the table with a speedup claim (`other-bench-quartets.json`).

Lower wall time is better. Each A/A number is from only one quartet; it is a warning about noise, not an estimate of its full distribution. The x87 A/B baseline includes a slow 8.82-second run; none was removed. Doom has substantial variation in both A/A and A/B. Neither result supports a reliable speedup or regression claim, nor does this synthetic loop establish game performance.

Doom 1.9 shareware was obtained from `https://image.dosgamesarchive.com/games/doom19s.zip` (archive SHA-256 `cacf0142b31ca1af00796b4a0339e07992ac5f21bc3f81e7532fe1b5e1b486e6`). Its `DOOMS_19.1` and `.2` pieces were concatenated into the original self-extracting ZIP and extracted locally. `DOOM.EXE` SHA-256 `b8020523561a5ad9706e009a52d61c578f37faafd85ac471962308406292ce27`; `DOOM1.WAD` SHA-256 `1d7d43be501e67d927e415e0b8f3e29c3bf33075e859721816f652a526cac771`. No game files are committed. Every Doom run logged exactly 2134 gametics and completed; its recorded `realtics` are guest clock observations, not a substitute for an independently stable host timer.

`benchmark_other_executed.py` is the exact local script with its original absolute paths. `benchmark_other.py` is a parameterized adaptation; its synthetic smoke run passed, but the entire 28-run series was produced by the executed script, not re-run with the adaptation.

## Remaining gates

Before treating this as merge-ready: test protected present-page/CPL3 permission faults and debugger UI/automatic stop-resume; handle or explicitly accept the existing #MF limitation; measure on a stable host with interleaved A/A and randomized A/B, more repetitions and additional real FPU-using games/applications. Debug and benchmark test instrumentation must remain out of the production patch.
