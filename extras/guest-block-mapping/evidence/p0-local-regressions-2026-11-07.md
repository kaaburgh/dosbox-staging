# P0 regression validation — local Linux execution (2026-11-07)

Tested on the assistant's own **Linux x86-64 container** with the DOSBox Staging **0.84.0-alpha** Release binary built from fork commit `a090d30391785eb658650db2352b91029e5814fb`, `OPT_JIT_BLOCK_TRACE=ON`, and `DOSBOX_JIT_BLOCK_TRACE` enabled.

**Not GitHub-runner performance results:** the binaries were compiled in Debian 13 CI using the same pinned source revision and copied into the local container with linked runtime libraries. Tests below actually executed in the local container with Xvfb and SDL software texture.

| Probe | Verified result | JIT creations | Invalidations |
|---|---|---:|---:|
| `SMC.COM` | `SMC.OK` contains `OK\r\n`; program checked AX = 96 after 96 self-patches | 34 | 10 |
| `XPG.COM` | `XPG.OK` contains `OK\r\n`; cross-page immediate changed from 0x1234 to 0x5678 | 30 | 7 |

The cross-page program generated **two JIT generations** whose guest entry address was at page offset 0xFFF; **one previous generation was invalidated**. Both lifecycle CSV files passed the analyzer's generation consistency and non-overlap checks.

A separate compiled C++ test exercises the recorder's ordinary create/invalidate CSV writes and forces `ENOSPC` via `/dev/full`. The recorder printed the error and disabled itself instead of silently continuing to emit incomplete evidence. Python analyzer tests: **13 passed**, including truncated record handling and 48 generations across three simulated reuse epochs of the same host address range.

### Scope of validation

- Successfully exercised **actual self-modifying DOS programs** and cross-page instruction invalidation, not just synthetic analyzer CSV.
- Tested **simulated** cache-wrap address reuse across epochs in Python; **not yet forced full native JIT cache exhaustion and reset on DOSBox**.
- These tests establish trace lifecycle soundness for the tested paths, not an exhaustive proof of all DOSBox CPU-mode transitions.
- Bundled Debian 13 runtime dependencies were captured by build manifest. Two isolated CI-only compatibility edits to unrelated keyboard mapper (SDL3) and MIDI (FluidSynth) code are still needed with those Debian libraries; neither touches CPU/JIT.
- No proprietary game files or executable binaries are stored in the repo.

Related: [experimental fork PR](https://github.com/kaaburgh/dosbox-staging/pull/1). The two COM test sources are in `extras/guest-block-mapping`.
