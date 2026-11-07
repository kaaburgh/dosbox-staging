# DOSBox Staging 0.84.0-alpha: Quake 1.06 integration evidence

## Provenance

- **Tested fork commit:** `edf22e77de501200192fbaf595e8d016490d53d3`.
- **Runner:** GitHub Actions, Ubuntu 24.04 host with Debian 13 (trixie) container.
- **Build:** `CMAKE_BUILD_TYPE=Release`, `USE_SYSTEM_LIBS=ON`,
  `OPT_JIT_BLOCK_TRACE=ON`, `OPT_MT32EMU=OFF`, `OPT_TESTS=OFF`.
- The test container patched **only two unrelated SDK-incompatible
  functions** in its temporary checkout: new SDL3 mapper default-scale API
  and new FluidSynth MIDI portamento API. Neither workaround is in the
  fork's PR; no CPU or JIT source was altered by the test.
- Official shareware Quake 1.06, DOS `QUAKE.EXE` SHA-256:
  `7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb`.
- GitHub Actions [successful build and game test](https://github.com/kaaburgh/configs/actions/runs/42365520877).
  The action artifact contains the raw tracer log, samples and DOSBox build logs.

## Results

- Full 969-frame `timedemo demo1` completed: **165.2 FPS** (5.9 game seconds).
- Created JIT generations: **7,846**; invalidated: **185**.
- Raw main-thread profiler samples (entire startup and benchmark): 1,095;
  matched native JIT samples: 591.
- **Demo-only interval** (from observed start/end in `result.json`):
  959 sampled main-thread RIPs, **553 matched guest JIT generations** and 406
  samples outside native JIT code. Timing uses the same Linux
  `CLOCK_MONOTONIC` clock for events and sample timestamps.
- Mapping analyzer completed without duplicate generation errors, unknown
  clears or overlapping simultaneously-live native ranges.
- Full demo-only **Top-30 guest blocks**: `quake106-084-demo-top30.csv`.

**Important:** One run is a functionality check, not a reliable performance
comparison with DOSBox 0.83.0. Old `0.83.0` CSV in this directory contains
different conditions; compare recurring guest block identities, not their
absolute sample counts or FPS. The Quake test did not exercise the separate
`smc_invalidations.asm` synthetic probe; this remains open.

Raw event and sample SHA-256 (artifact evidence integrity):

```text
9efe08d4a620bb514e868ae6f3e647906b521d3a753ed74866135ced6c6111f6  jit-blocks.csv
d11076bf5f50059c45c3d562d713099e77bdb488a3a11de9972fc2e8b1d74f7f  samples.bin
5602cefc74ea2580f57c31d71ffd459e6c87d48adbe1462d4381970abf73f534  guest-hotspots-demo-only.csv
```

### Notes for future profiling

- The top raw all-process sample block `00A7:00044D60` (32 samples)
  is **not** top among demo-only samples; most of its samples occurred
  before the demo began. Always filter to `demo_start_observed_ns` and
  `demo_end_observed_ns` when investigating the game rendering workload.
- The strongest recurring demo blocks include
  `00A7:00010D3B`, `00A7:0002F5CF`, and `00A7:00011343`.
- A further independent run plus the SMC probe is needed to make stronger
  correctness and stability claims.
