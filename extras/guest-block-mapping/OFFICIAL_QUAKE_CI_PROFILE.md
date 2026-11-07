# Original Quake 1.06 — executable profiling in GitHub Actions

**Scope:** full DOSBox Staging 0.84-alpha fork build + official Quake
1.06 benchmark execution, as a research check. This is a stacked
continuation of PRs #1–#4, not a JIT optimization or reliable FPS test.

## Source provenance

The [DOSBox Staging performance-test wiki](https://github.com/dosbox-staging/dosbox-staging/wiki/Performance-tests)
publishes its Quake 1.06 benchmark archive:

https://github.com/user-attachments/files/21488506/quake-benchmark.zip

The archive is downloaded by CI, prepared by
`prepare_official_quake.py`, and validated against the historical
unmodified DOS Quake 1.06 executable SHA-256:

`7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb`

Any mismatch, missing `ID1/PAK0.PAK`, unsafe ZIP entry, or excess
decompression size fails the run. Do not publish Quake's executable,
PAK0.PAK, raw JIT code snapshots or full unbounded logs.

## Reproducibility and profile

Workflow `quake-official-ci-profile.yml` checks out this precise branch,
records its source SHA, builds with the same Debian 13 dependencies and two
non-JIT SDK shims already used in prior local 0.84 experiments, and enables
OPT_JIT_BLOCK_TRACE. It compiles the existing SIGPROF preload sampler and
runs real original Quake `+timedemo demo1` (969-frame oracle) twice, using
4700 and 5300 microsecond CPU-time sampling intervals.

Each run captures nine selected Quake guest/native JIT blocks and binds
samples to lifetime generation IDs. PR #3 produces exact host-RIP
offsets within blocks. PR #4 joins those offsets to validated binary
x86-64 instruction starts. `official_quake_profile_report.py` groups
selected *entry blocks* into H1/H2/H3 and records main-thread JIT /
other-JIT / non-JIT sample denominators.

**Interpretation restrictions:** no functions' whole CPU shares can be
deduced from nine entry blocks; C++ helpers outside JIT are not attributed
to call sites; raw emitted code includes deferred slow paths; RIP samples
are periodic and correlated; Quake FPS in a shared CI runner cannot
quantify performance improvements.

## Artifact safety

The workflow retains only manifests (source/binary/game hashes), derived
sample counts/offsets/instruction-family labels and bounded diagnostic
error strings for seven days. Game binary and PAK, raw SIGPROF stream,
JIT blobs, raw trace and full logs are kept only inside the ephemeral
CI runner. The raw profiler samples and blob addresses must belong to
the same process; never join across runs.

## Validation and review gates

- The CI must explicitly confirm two complete 969-frame demo1 runs.
- Both runs must capture all nine snapshots, with matching lifecycle and
  actual native blobs; decoder disassembly must cover all native bytes.
- Treat suspiciously small counts or inconsistent disassembly as missing
  evidence, not an optimization opportunity.
- Compare real sampled hot offsets against H1's inline TLB/texture
  arithmetic; only then consider focused semantic tests and a separate
  optimization PR.

Research state and the resulting run URL should be added to the external
`kaaburgh/configs` hypothesis log without publishing original game bytes.
