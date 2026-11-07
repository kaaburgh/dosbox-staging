# Quake native RIP hotspots — generation-safe sampling (H1/H2/H3)

**Status:** diagnostic tool + synthetic tests. No new Quake runtime profile or
speedup has been measured by this PR. This branch is stacked on PR #2 and #1.

## Objective and limits

PR #2 captured nine complete guest/native JIT blocks from original Quake 1.06,
but linear disassembly counted static call sites including deferred slow paths.
This experiment joins per-thread SIGPROF RIPs to native JIT compilation
**generations** and records the offset within the selected block, not a guessed
guest instruction. This is necessary before attempting H1 (textured spans)
or H2 (Z-buffer) optimization.

Input from one process: lifecycle CSV, main-thread profiler samples, nine
snapshot metadata records and host/guest blobs. Output: only a summary JSON,
per-generation exact offset counts, and 16-byte interval counts. Metadata and
blob extents must match CREATE events, and host RIP ranges are half-open. The
existing generation-aware LiveMap handles invalidation and code cache reuse.
The selected CPU thread is TID equal to trace PID.

Non-JIT samples are not automatically attributed to helper calls. Sampling
inside a JIT block cannot establish whether any static call actually executes,
nor does a byte interval necessarily coincide with instruction boundaries.
Sample ratios are not retired instruction counts. Signal samples are correlated
and the demo window is observed via log polling rather than instrumented
engine frame boundaries.

## Local capture — private inputs

Build the fork with PR #1 + PR #2 and OPT_JIT_BLOCK_TRACE=ON. Under Linux
x86-64, compile the existing profiler:

    cc -O2 -shared -fPIC -o /tmp/preload_sigprof_thread.so \
      extras/guest-block-mapping/preload_sigprof_thread.c -ldl -pthread

Run original unmodified Quake 1.06. The working directory needs
resources/mapping from the DOSBox checkout (Staging 0.84). Every run
must have a unique new output directory:

    python3 extras/guest-block-mapping/run_guest_trace.py \
      --dosbox /absolute/path/to/instrumented/dosbox \
      --game /absolute/path/to/private/quake106 \
      --runtime-cwd /absolute/path/to/dosbox-staging \
      --profiler /tmp/preload_sigprof_thread.so \
      --snapshot-eips 0x10d3b,0x11216,0x112cc,0x11343,0x1143f,0x3e233,0x3e29a,0x3e3ef,0x3e455 \
      --snapshot-max 16 \
      --out /private/new-unique-run

The runner writes result.json with demo_start_observed_ns and
demo_end_observed_ns (CLOCK_MONOTONIC). Use these observed bounds in
the analysis; their precision is limited by log polling:

    python3 extras/guest-block-mapping/analyze_native_rips.py \
      --trace /private/new-unique-run/jit-blocks.csv \
      --samples /private/new-unique-run/samples.bin \
      --snapshots /private/new-unique-run/snapshots \
      --dosbox-log /private/new-unique-run/dosbox.log \
      --start-ns DEMO_START_MONOTONIC_NS \
      --end-ns DEMO_END_MONOTONIC_NS \
      --out /private/new-unique-run/derived

The output folder must not already exist. Contents are summary.json,
native-bins.csv and native-rips.csv, with offsets and counts rather than
raw game/JIT code. Never commit game assets, guest/native .bin snapshots,
or unbounded profiler logs to public GitHub.

For **private** host disassembly, use the host_begin address in each JSON:

    objdump -D -b binary -m i386:x86-64 \
      --adjust-vma=0xHOST_BEGIN file.host.bin

Subtract host_begin to join the disassembly to native-rips.csv; verify actual
instruction boundaries first. The full emitted native blob contains deferred
slow/exception paths, so identify only verified fast/slow control-flow paths.

## Decision gates

- Fewer than 100 selected samples causes a warning (heuristic only, not a
  statistical threshold). If sparse, gather more repeated runs on the same
  build; aggregate generations only after checking they have equivalent
  control flow and instruction layout.
- If H1 samples cluster at TLB fast-path guards, examine one x86 memory
  lowering and design a minimal DOS oracle (page crossing, permissions,
  SMC, interrupts/cycles) before changing the JIT.
- If H1 samples are in texture arithmetic, or if native helpers outside the
  JIT dominate, do not mistake static call-site counts for runtime overhead;
  use a symbolized helper/call-edge study or proceed to H2.
- H3 runtime displacement patching was confirmed in PR #2, but excess
  invalidation was not shown.
- No claim of FPS improvement until an independent uninstrumented A/B test.

Synthetic validation (no Quake bytes):

    python3 -m unittest discover -s extras/guest-block-mapping \
      -p 'test_analyze_native_rips.py' -v

Tests cover recycled host addresses, half-open boundaries, thread filtering,
non-selected JIT addresses, inconsistent metadata/blobs, unsafe file paths
and sample-bucket validation. Existing trace/snapshot unit tests still apply.
