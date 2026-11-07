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
- **0.84.0-alpha:** source port requires fresh build and integration
  validation; do not reuse historical absolute addresses or FPS as
  evidence for 0.84.
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

## Open correctness checks

1. Verify both normal invalidations and code-cache wrap-around (reuse of
   host addresses) lead to distinct generation IDs, never overlapping
   live regions.
2. Verify self-modifying and cross-page guest code produce valid lifecycle
   events and do not violate JIT interval accounting.
3. Measure the steady-state overhead using paired A/B runs on the same
   host, with the same renderer and game configuration. The prior
   shared Linux container exhibited large external load variability.
4. The tracer maps native JIT RIPs only. Time inside C++ helper functions
   cannot yet be assigned to one guest instruction.

Keep the implementation restricted to diagnostics; generalized and
upstreamable performance improvements should be separately tested
against the unmodified official Quake 1.06 DOS benchmark.
