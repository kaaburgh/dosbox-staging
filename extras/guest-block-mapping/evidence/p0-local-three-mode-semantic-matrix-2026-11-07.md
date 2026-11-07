# Local semantic-control matrix — 2026-11-07

This test asks whether **adding/enabling guest JIT block tracing changes
the behavior of the emulated guest code**, not whether it changes FPS.

On the same local Linux x86-64 container, with the same Xvfb screen,
SDL software texture, CPU type Pentium, `core=dynamic` and DOS programs,
each probe was run under three configurations:

1. `OFF`: DOSBox Release with `OPT_JIT_BLOCK_TRACE=OFF`.
2. `INACTIVE`: DOSBox Release with `OPT_JIT_BLOCK_TRACE=ON`,
   but `DOSBOX_JIT_BLOCK_TRACE` environment variable **unset**.
3. `ACTIVE`: the **same executable as INACTIVE**, with lifecycle
   CSV recording enabled.

## Independently checked results

| DOS guest program | OFF: marker | INACTIVE: marker | ACTIVE: marker | ACTIVE JIT lifecycle |
|---|---|---|---|---|
| `SMC.COM` | correct | correct | correct | 34 created / 10 invalidated |
| `XPG.COM` | correct | correct | correct | 30 created / 7 invalidated |
| `WRAP.COM` | correct | correct | correct | 16,025 created / 6 invalidated |

- **All nine independent DOS executions passed**, with exit status 0.
- Each COM created its designated `*.OK` file with the exact bytes
  `OK\r\n` only after the guest-side checksum/condition passed.
- No lifecycle CSV was created in OFF or INACTIVE modes; all three ACTIVE
  traces were present and accepted by the lifecycle analyzer.
- ACTIVE logs showed no recorder I/O error.
- This test used the **standard 8 MiB JIT cache**, in order to isolate
  instrumentation state from cache-size stress tests.

Exact executable SHA-256:

```text
e6e5392201ea6b8afe974e33c48dc1925f90d8bb2e7918d75fad51cf378f0f6b  dosbox-off
1894784284f6040981f9f4845c658e01393e79e6c39165eebee92c596ff47569  dosbox-on (inactive or active)
```

The independent native-cache wraparound check (1 MiB diagnostic cache,
three guest passes, five physical cache wraps) is documented separately in
`p0-local-cache-wrap-2026-11-07.md`.

### Limitations

These tests demonstrate behavioral equivalence **for these three specific
programs**. They cannot establish bit-for-bit equivalence for all x86
instructions, FPU state, exceptions, DMA/video devices or every DOS game.
Neither do they establish a precise FPS overhead estimate on this
variable-load host.

The reusable local-only harness is in
[kaaburgh/configs#5](https://github.com/kaaburgh/configs/pull/5):
`benchmarks/quake-staging/harness/semantic_modes_local.py`.
