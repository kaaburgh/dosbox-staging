# JIT audit tools (Phase 1, research only)

> To re-check the Phase 2 pull requests on your own machine with one
> command (build, guest tests, Quake benchmark, ZIP for review), see
> [validate/README.md](validate/README.md).

Research tooling and guest tests from the independent audit of the Quake 1.06
x86-64 dynamic-core experiments (PR #1–#10). Nothing here is built into
DOSBox Staging; the instrumentation header is applied to a separate checkout
only for diagnostic builds.

Findings, evidence tables and the Phase 2 plan are in
`kaaburgh/engineering-lab`, branch `claude/relaxed-lamport-nquiqb`, directory
`benchmarks/quake-staging/research/phase1-audit-2026-11-09/`.

## Guest tests (`tests/`)

| File | What it checks | Build |
|---|---|---|
| `pfprec.asm` → `PFPREC.COM` | What a guest `#PF` handler observes when an integer or x87 memory operand faults: pushed EIP (faulting instruction `I` vs start of the JIT block `B`), a register written just before the instruction (`R=ok/stale`), the loaded/stored value (`V`). Cases: integer `MOV`, `FLD/FADD/FILD/FIADD m32`, cross-page `FLD m32`, `FLD m64`, `FSTP m32`. | `nasm -f bin -o PFPREC.COM pfprec.asm` |
| `bitops.asm` → `BITOPS.COM` | Semantics gate for instructions the dynamic core hands to the normal core in Quake (`BR_Opcode`): `BT/BTS/BTR/BTC` register, immediate, 16-bit and memory bit-string forms (offsets 37, 40, −1, −32), `SETcc`, `SAHF/LAHF`, and the GCC x87 compare idioms. CF is captured with `SBB`, not `SETcc`. Expected values from the SDM; prints `BITOPS deviations=00` on success. | `nasm -f bin -o BITOPS.COM bitops.asm` |
| `jitsize.asm` → `JITSIZE.COM` | A maximum-length dynamic-core block made of x87 m32 loads (SIB + segment override); prints `JITSIZE_PASS`. With a block dump it measures per-block code growth against the 12 KiB `CACHE_MAXSIZE` reservation. `-DDIRTY` variant dirties all GPRs first. | `nasm -f bin -o JITSIZE.COM jitsize.asm` |

Observed results (Release, GCC 14.2, `main` 27b1b7c):

| Build | Core | int_mov | x87 m32 loads (5 cases) | fld64 / fstp32 |
|---|---|---|---|---|
| base, PR #9 | normal | `E=I R=ok` | `E=I R=ok` | `E=I R=ok` |
| base, PR #9 | dynamic | `E=I R=ok` | `E=B R=stale` | `E=B R=stale` |
| PR #10 patch | dynamic | `E=I R=ok` | `E=I R=ok` | `E=B R=stale` |

All cases load/store the correct value after exactly one fault. The dynamic
core's helper-based x87 memory accesses use the legacy nested
`PAGING_PageFault` path, so the guest handler sees the EIP of the block start
and stale register values; PR #10 changes this for m32 loads only.

### Phase 2 gates

Each test derives its expected values from the Intel SDM, not from the normal
core; most have a negative control (`-DNEG` or a deliberately broken build).

| File | What it checks | Pass |
|---|---|---|
| `flagrst.asm` + `check_flagrst.py` | Every path on which the dynamic core reloads saved guest flags (CMC/STC/CLC, 16 Jcc, LOOPE/LOOPNE, SALC, shift by CL=0, ROL, IMUL, SHLD) for all 64 combinations of the six arithmetic flags, from a POPF image and from live flags saved around a memory access, all GPRs live. The checker holds the SDM model. | `check_flagrst.py out.txt` → 0 deviations |
| `flagops.asm` | BT/BTS/BTR/BTC r,r / r,imm8, all SETcc (registers and memory), SAHF/LAHF over all 64 flag combinations, with canaries in all GPRs and both live and saved host-flag paths. Run with `--cycles max`. | `FLAGOPS deviations=0000` |
| `x87load.asm` | All 16 x87 m32-source forms with the operand at page offsets 0xFFC–0xFFF and in mode-13h VGA memory; ST0/status results and preservation of GPRs and EFLAGS. | `X87LOAD deviations=00` |
| `x87store.asm` | FST/FSTP m32real and FIST/FISTP m32int at page offsets 0xFFC–0xFFF, in-page and in mode-13h VGA memory: stored bits (incl. rounding to nearest even and integer indefinite), TOP after pop, canaries around the destination, GPRs/EFLAGS preserved; mixed fast/slow block, 22 stores in one block, FISTP into a later instruction's immediate (self-modifying code). | `X87STORE deviations=00` |
| `stackops.asm` | PUSH/POP/CALL/RET: operand sizes, PUSH ESP/POP ESP/POP [ESP], 16-bit and real-mode stacks, page crossing, ring-3 accesses, `#PF` with retry through a task-gate handler. 305 checks plus observations (no SDM result) that must match the baseline. | `STACKOPS fails=00000000` on the dynamic core (the normal core has one pre-existing failure: faulting `CALL` pushes the wrong EIP) |
| `pfregs.asm` | `pfprec.asm` plus cases in which inline x87 m32 loads with live registers precede a faulting legacy-helper load in the same block; prints the handler's PUSHAD frame. | Output identical to the baseline build |

`microbench/flag_restore_check.c` runs the exact byte sequence of the
POPFQ-free flag restore (candidate C4) for all 8192 relevant saved-image
values and compares it with `POPFQ` (`gcc -O2 -o flag_restore_check flag_restore_check.c`; exit code 0 = pass).

Phase 2 results (draft PRs, timing, counters, negative results) are in
`kaaburgh/engineering-lab`, `benchmarks/quake-staging/research/phase2-2026-11-11/`.

## Tools (`tools/`)

- `run_dos_test.py` — run one `.COM` headlessly and print its redirected stdout.
- `bench_quake.py` — interleaved, seeded Quake timedemo runner. Records Quake's
  FPS and the main thread's CPU time (Linux `/proc/<pid>/task/<pid>/schedstat`,
  Windows `GetThreadTimes`, see `hostplat.py`) between "Playing demo from" and
  the result line; optional `--affinity`, `--run-logs`, `--sensors`. With fixed
  `--cycles 1000000` the guest work is deterministic (identical guest
  seconds/FPS in every run).
- `summarize_bench.py` — per-label medians/CV and within-block paired ratios
  with a bootstrap over blocks.
- `pooled_compare.py` — pools several series; each block's reference is the
  geometric mean of the identical-binary placebo labels (e.g. `base`,`base2`).
- `profile_quake.py` — attaches `perf record --call-graph dwarf` only for the
  demo window.
- `analyze_jit.py` — joins `perf script` samples with the JIT block dump by
  (time, address) generation; helper samples are attributed to the first JIT
  caller frame.
- `count_quake.py` — exact event counts for the demo window from a
  `JitCounters` build (SIGUSR1 snapshots at demo start and at the result).
- `sanitize_summary.py` — strips paths/addresses before publishing.

Game files, perf data, block dumps and raw addresses must stay private.

## Instrumentation (`instrumentation/`)

`jit_counters.h` + `apply_jit_counters.py <checkout>` add exact counters
(C++ and flags-neutral counters in generated code) for executed `PUSHFQ`/
`POPFQ`, stack helper calls, x87 helper calls, dispatcher entries,
normal-core fallbacks, created blocks and TLB-miss helper calls. Guest results
are unchanged (Quake demo1, `PFPREC`, `JITSIZE` identical to base); the
generated code is larger, so use this build for counting, never for timing.
Note: do not emit code inside the inline slow path of `dyn_read_word`/
`dyn_write_word` — it is skipped by an 8-bit short jump.

`apply_flag_injection.py <checkout> popf|pushf K` adds K semantically
neutral `PUSHFQ;POPFQ` (or `PUSHFQ;LEA RSP,+8`) after every flag restore (or
save) to measure the in-situ cost per event.

`jit_perf_map.h` + `apply_jit_perf_map.py <checkout>` add hooks at block
creation/clear that write `/tmp/perf-<pid>.map` (perf JIT symbolisation) and,
with `DOSBOX_JIT_DUMP=<file>`, a binary dump of native bytes and guest bytes
(read through the existing TLB only). Build with
`-DCMAKE_CXX_FLAGS=-DDOSBOX_JIT_PERF_MAP=1`; run with `DOSBOX_JIT_PERF_MAP=1`.
Emitted code is unchanged.

Demo-window counts on `main` 27b1b7c (fixed `cpu_cycles_protected=1000000`):
604 M `PUSHFQ`, 37.3 M `POPFQ`, 152.4 M `FPU_FLD_32`, 36.1 M `FPU_FST_32`,
65.4 M push + 39.5 M pop helper calls, 18.0 M dispatcher entries, 1.95 M
normal-core fallbacks, 3 730 blocks created. With the PR #10 patch,
`FPU_FLD_32` calls drop to 0 and TLB-miss read helpers rise by 30.8 k.
Injection: +4×`POPFQ` per site → ×1.31 demo CPU time (≈10–12 ns each),
+4×`PUSHFQ` → ×1.13 (≈0.3 ns each).

## Micro-benchmark (`microbench/flags_save.c`)

Cost of the dynamic core's guest-flag save around an inlined TLB-checked
store: on Intel Emerald Rapids `PUSHFQ`+`LEA` adds ≈0.3–0.8 ns, a `POPFQ`
restore ≈10 ns, `LAHF/SETO/SAHF` ≈0.6 ns per access.
