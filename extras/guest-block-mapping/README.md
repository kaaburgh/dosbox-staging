# Experimental JIT guest block mapping (Quake 1.06)

An **opt-in diagnostic feature** for the x86-64 dynamic CPU core. It records
which native code-cache intervals were generated for which guest CS:EIP
blocks, and when these intervals were invalidated. It does not alter guest
instruction semantics.

This branch ports the validated prototype from
[kaaburgh/configs PR #4](https://github.com/kaaburgh/configs/pull/4)
(DOSBox Staging **0.83.0**) to the fork's **0.84.0-alpha** source tree.
The previous Quake 1.06 samples are included as historical evidence only:
**a new profile on 0.84.0-alpha has to be measured before comparing hotspots**.

## Building and using

On Linux/x86-64, configure an ordinary Release build with the diagnostic flag:

```sh
cmake --preset release-linux-vcpkg -DOPT_JIT_BLOCK_TRACE=ON
cmake --build --preset release-linux-vcpkg
```

The compilation flag defaults to **OFF**. If enabled, *recording* still
defaults to OFF until the process is started with:

```sh
DOSBOX_JIT_BLOCK_TRACE=/absolute/path/new-jit-events.csv \
  /path/to/dosbox --conf /path/to/dosbox.conf
```

The output file must **not already exist**; exclusive file creation prevents
silently destroying or mixing runs. One CSV record is written per lifecycle
event. The process may be terminated after the completed timedemo: event
records are written without relying on `atexit` flushing.

Column | Meaning
--- | ---
`event` | `C` = block created; `D` = generation invalidated
`monotonic_ns` | Linux `CLOCK_MONOTONIC` timestamp (also used by sampler)
`pid` | Host DOSBox process ID
`generation` | Unique ID in this process, never reused, even if code-cache address is reused
`guest_cs`, `guest_eip` | Guest execution entry address **at compilation time**
`guest_linear_begin` | Linear guest address of entry point, not executable file offset
`guest_linear_end` | Decoder end marker; advisory on cross-page blocks
`host_begin`, `host_end` | Half-open native JIT machine-code interval

`src/cpu/jit_block_trace.h` implements the minimal event recorder;
`src/cpu/dyn_cache.h` records invalidations; and
`src/cpu/core_dyn_x86/decoder.h` records completed code emissions.
Only the x86 dynamic core is supported, not the ARM dynrec core.

The profiler is **not** a block execution counter. To estimate CPU time,
combine native RIP samples with these lifecycle events:

```sh
cd extras/guest-block-mapping
python3 -m unittest discover -s . -p 'test_*.py' -v
cc -O2 -shared -fPIC -o preload_sigprof_thread.so preload_sigprof_thread.c -ldl -pthread

python3 analyze_trace.py \
  --trace /path/to/new-jit-events.csv \
  --dosbox-log /path/to/captured-dosbox.log \
  --samples /path/to/samples.bin \
  --start-ns YOUR_DEMO_START_MONOTONIC_NS \
  --end-ns YOUR_DEMO_END_MONOTONIC_NS \
  --output /path/to/generations.csv \
  --output-guest /path/to/guest-hotspots.csv
```

The helper emits little-endian 24-byte `<QQII>` records:
`monotonic_ns,host_rip,linux_tid,reserved`. The analyzer excludes worker threads
using the main TID (which equals the DOSBox PID), validates generation
lifecycles, and rejects overlapping simultaneously-live native ranges.

## Validation status

- **0.83.0 prototype (prior work):** two completed original Quake 1.06
  timedemo profiles, 145.2 and 141.7 FPS using SDL software texture; created
  7,724 and 7,722 blocks; invalidated 185 each; 659 and 655 JIT RIP
  samples matched. See `evidence/quake106-top30-guest-blocks.csv`.
- **0.84.0-alpha:** the port built and successfully ran original
  Quake 1.06 in the local Linux container, plus real DOS self-modification
  and cross-page code modification probes. The detailed 0.84 evidence is
  in `evidence/quake106-084-integration.md` and
  `evidence/p0-local-regressions-2026-11-07.md`. Do not compare
  FPS across unrelated containers or compiler environments.
- The preexisting 0.83 sample evidence is available in the
  [original research PR](https://github.com/kaaburgh/configs/pull/4).
- No game binary or proprietary PAK assets are checked in here.

## Self-modifying-code regression probe

`smc_invalidations.asm` is a small real-mode DOS COM program that alternates
the first instruction of a callable code block between `NOP` and `INC AX`
96 times. It then writes `SMC.OK` containing `OK\r\n`.

Assemble it using `nasm -f bin smc_invalidations.asm -o SMC.COM`, mount the
directory in an instrumented DOSBox (`core=dynamic`), and execute `SMC.COM`.
The trace should show creation and invalidation of native JIT code. Confirm
the marker file exists before assessing the lifecycle trace. This synthetic
probe checks one self-modification path; it is not a proof for every cache
reset/cross-page scenario.

## Deterministic native JIT-cache wraparound regression

`cache_wrap_stress.asm` creates a 16-bit `WRAP.COM` with **16,000 distinct
guest basic blocks**. Every block increments `BX` and ends with a short
unconditional jump. The program verifies `BX == 16000` and writes
`WRAP.OK` containing `OK\r\n` only if the computation is correct.

```sh
nasm -f bin extras/guest-block-mapping/cache_wrap_stress.asm -o WRAP.COM
```

The local 0.84 run with a standard **8 MiB native JIT-cache** completed
successfully. Its trace contains **16,025 JIT creations** and ~1.87 MiB
of emitted native instructions. Therefore a separate build temporarily
reducing `CACHE_TOTAL` to **1 MiB** must exercise actual code-cache
wraparound. Crucially, *the small-cache build is a stress-only build*, not
an optimization or an upstream change. Validate after local execution:

1. `WRAP.OK` contains exactly `OK\r\n` (semantic correctness).
2. The trace shows native code-cache addresses reused by **distinct
   generation IDs** (actual recycling, not a simulated epoch).
3. Running `analyze_trace.py --trace ...` does not reject lifecycle
   events or overlapping simultaneously-live native ranges.
4. Test with several repeated invocations in one DOSBox process to
   exercise multiple wraparound epochs.

Do not claim the real wraparound path passed unless these four
conditions are actually observed.

## Bit-test opcode regression probe

`bit_test_semantics.asm` is a real-mode DOS COM program that executes 16
cases of the bit-test instruction family (`BT`, `BTS`, `BTR`, `BTC` with a
register and with an immediate bit index) on a 32-bit operand. It captures
the carry flag and the resulting operand, compares both against
expectations taken from the Intel SDM, and writes the raw results to
`BTOUT.BIN` through `int 21h`. Byte 1 of that file is the number of
deviations the program counted itself.

```sh
nasm -f bin extras/guest-block-mapping/bit_test_semantics.asm -o BITSEM.COM
python3 extras/guest-block-mapping/run_bit_test_semantics.py \
    --dosbox /path/to/dosbox --out /tmp/bit-test
```

The runner executes the program on `cpu core = normal` and on
`cpu core = dynamic` and compares the two outputs byte for byte. On the
current sources both cores produce the identical SHA-256
`640dffd87f299bc437c43c9ae07825deabd04057f295fc3a65ae13fa1580fd37` with
**0 deviations out of 16**.

This probe exists because the dynamic core does not implement the bit-test
opcodes: `src/cpu/core_dyn_x86/decoder.h` implements 88 of the 256 two-byte
sub-opcodes in its `case 0x0f:` branch, and `0F A3`, `0F AB`, `0F B3`,
`0F BB` and `0F BA /4../7` are absent. An unhandled opcode reaches the
decoder's `illegalopcode:` label, which ends the block with
`gen_return(BR_Opcode)`; the dispatcher then runs that single instruction
through `CPU_Core_Normal_Run()`.

The trace-enabled run makes this visible from the guest side: the dynamic
core emits only three blocks for the whole program and the last one ends
exactly at the first bit-test instruction. The rest of the program is
executed by the interpreter, and the program still completes correctly.

See [BIT_TEST_DECODER_GAP.md](BIT_TEST_DECODER_GAP.md) for the full
analysis and for the acceptance criteria of a possible follow-up
implementation.

## Verified correctness (local execution)

- `SMC.COM`: self-modifying arithmetic and JIT invalidation, with guest
  checksum validated by DOS code.
- `XPG.COM`: instruction spanning a physical 4 KiB page boundary was changed
  and executed correctly; invalidated and regenerated crossing block.
- `WRAP.COM`: 16,000 independently known x86 guest block entries, all present
  in the native-to-guest JIT trace; three local program executions passed in
  a temporary **1 MiB JIT-cache** with **five actual wraparounds**, **8,535**
  reused native address starts, **48,062 CREATE** and **39,968 INVALIDATE**
  events. No simultaneously-live native ranges overlapped. The 8 MiB
  negative control correctly failed `--require-reuse`.
- Recorder `ENOSPC` test: recording fails closed and reports an error. The
  analyzer rejects malformed rows and, with `--dosbox-log`, also rejects
  a recorder error that ends exactly on a valid CSV record boundary.
- **15** Python lifecycle/analyzer unit tests passed locally.

Detailed proof, executable hashes and limitations:
`evidence/p0-local-cache-wrap-2026-11-07.md`.

## Still unverified

1. Other x86 CPU modes, exception paths, FPU helpers, paging layouts and
   broader non-Quake game workloads; current probes are deliberately narrow.
2. Threaded or externally interrupted recorder shutdown cannot always
   provide a trustworthy "complete trace" marker. Preserve DOSBox stderr
   logs and pass `--dosbox-log` to the analyzer.
3. Steady-state performance overhead is inconclusive on a shared host and
   should be investigated on a stable dedicated machine.
4. Attribution of native C++ helpers to individual guest instructions is a
   separate follow-up; block-level mapping does not claim to solve it.

Keep the implementation restricted to diagnostics; generalized and
upstreamable performance improvements should be separately tested
against the unmodified official Quake 1.06 DOS benchmark.
