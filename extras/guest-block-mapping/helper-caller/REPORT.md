# Quake: helper callers, guest block lifetimes, and semantic checks

## Outcome

Two independent original Quake 1.06 `demo1` executions completed 969 frames with sampled helper-entry return addresses and the *same process's* JIT CREATE/INVALIDATE trace. Direct call-site attribution now connects selected helper executions to specific live guest blocks. No speedup or product optimization is claimed.

The probes are experimental and intrusive. Sampling counts calls, not elapsed CPU time; sampling every 257th or 263rd call is systematic and may alias with loops. No statistical confidence intervals or precise runtime share follow. The demo window is determined by polling the Quake console. Neither test captures helper calls outside the selected probes, or attributes the total `mem_readd` profile to these callers.

## Method and provenance

- Local build based on fork `66884b444e6497fd13b3ac8ff5501af53b02a3ed` (PR #6 / before PR #7's research-only files), plus the helper probe sources in this PR and two non-CPU Debian SDK shims used in PR #7. `OPT_JIT_BLOCK_TRACE=ON`; binary SHA-256 `875ac76773e44f56be5c37391c8a105e1ab5140c727d2402f6a3c5518edbd096`. Dynamic x86-64 core, SDL dummy and software texture, original Quake 1.06 `demo1` at 320x200; Quake EXE SHA-256 `7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb`, PAK0 SHA-256 `35a9c55e5e5a284a159ad2a62e0e8def23d829561fe2f54eb402dbc0a9a946af`.
- A probe at function entry records sampled direct native return address, timestamp, ordinal, guest linear memory address, and in one handler wrapper the virtual-table fingerprint. The probe file is opened exclusively, mode 0600. The trace is per process; the join uses `return_address - 1` inside the native code range that was live at the timestamp. Both runs had zero unmatched records inside the observed demo window. The return address is direct evidence of a call from that native block, not automatically proof of a one-to-one mapping to one guest instruction.
- Run A sampled every 257th call; run B every 263rd. The full derived block counts are in `helper-counts-257.json` and `helper-counts-263.json`. Exact code bytes, game assets, process addresses, raw samples and maps are not in the repository.
- Separately, a previous probe build SHA-256 `441e75c48d7787887d1bfd73fb6465ebf93c7bb602406de16032f0fae7f04a50`, with 32-byte probe records and otherwise equivalent FPU/caller instrumentation, captured five complete selected guest/native snapshots in the *same run*, with SIGPROF. The diagnostic `snapshot-call-validation.json` records the JIT offsets and sample counts. Every observed return offset for these snapshots was an instruction boundary preceded by a native `call` in linear disassembly. This verifies native call sites for that run and build; do not join its raw offsets to the final build.
- `handler-types-*.json` identify handler classes by looking up the sampled vptr within the DOSBox ELF's `nm -S` vtable ranges using the ASLR mapping from the same process. This relies on the Linux x86-64 C++ ABI. The vptr is obtained from the handler pointer actually used for that sampled call. It is a fingerprint, not a generic portable type system. Initial page-handler fingerprints can change during execution; the experiment records each selected sample, not only the first handler per page.

## Observed direct callers

| Helper / live guest block entry | A (257) | B (263) | Evidence and interpretation |
|---|---:|---:|---|
| FPU 32-bit load, `00a7:000306bd` | 80,672 | 78,704 | Snapshot contains x87 loads, multiplies and adds. Several native call sites lead to `FPU_FLD_32`; the source of that helper calls `mem_readd`. Guest Quake function name remains unknown. |
| FPU 32-bit store, `00a7:000306bd` | 11,453 | 11,182 | Same block also calls `FPU_FST_32`; snapshot contains two memory `fstp` operations. |
| FPU 32-bit load, `00a7:000112cc` | 21,704 | 21,164 | Previously identified textured-span block. Snapshot starts with x87 arithmetic followed by byte-oriented texture work. Its four sampled native call sites were validated. |
| Checked dword write-handler wrapper, `00a7:00010572` | 59,890 | 58,649 | Live guest block disassembly is a loop containing `REP MOVSD`. All sampled effective linear addresses were in `0xA0000–0xAFFFF`. |
| Checked stack dword push wrapper, `00a7:0004df18` | 18,298 | 17,950 | The snapshot has three guest pushes and a call; direct helper call returns belong to this live generation. The probe covers the `dyn_push` checked-dword lowering, not every possible call to `mem_writed_checked`. |

For `00a7:00010572`, vtable attribution was `VGA_ChainedVGA_Handler`: 59,821 / 58,599 records, and `InitPageHandler`: 69 / 50 records. The latter matters: an initial handler can initialize or redirect a page, so a single address or a single first observed handler would have been insufficient. The sampled distribution is of *that wrapper's calls from that block*, not overall game runtime. Handler class and VGA-range evidence support a framebuffer-copy interpretation in this workload; they do not make other VGA modes or mappings equivalent.

`MakeCodePage` is on decoder/cache setup paths in the inspected source; this experiment did not establish a direct guest-block helper call for it. The measured `mem_readd` symbol has many possible callers; the verified x87 path is one source-supported chain, not a claim that all its earlier profiler samples come from `0x306bd`.

## Behavioral validation

`run_semantic_matrix.py` ran four public synthetic COM tests in baseline trace-OFF, diagnostic build inactive, and diagnostic build active; each in `normal` and `dynamic` core: **24/24 pass** (`semantic-matrix.json`).

- `fputest.asm`: x87 load/store roundtrips for finite positives and negatives, smallest float denormal, negative zero and positive infinity. Guest addresses ending `0xFFC` through `0xFFF` cover both same-page and cross-page dword accesses. Normal and dynamic output agrees; a separate dynamic run with stride 1 recorded **five load and five store helper calls at each of `0xFFD`, `0xFFE` and `0xFFF`**. This shows the tested paths actually reached the probe. `FPU.OK` is written only on expected result.
- `vgatest.asm`: sets VGA mode 13h, repeatedly copies eight known dwords via 32-bit-address `REP MOVSD` to VGA memory starting at offset `0xFFD` and reads them back. The active dynamic run sampled the dword store handler. It tests a page-crossing unaligned copy in this mode, not the full VGA device model.
- Existing `SMC.COM` and `XPG.COM` verify self-modifying and cross-page guest-code invalidation. These are regression checks, not exhaustive proof of this instrumentation or of prospective optimizations.
- Four new synthetic join tests cover return-at-range-end, generation reuse, partial records, recorder errors and PID mismatch; 15 pre-existing trace-analysis tests also pass.

Not tested here: protected-mode page-fault recovery and privilege checks, x87 exception/status behavior beyond the fixture's masked operations, NaN payload/signaling behavior, all rounding modes, VGA bank/mode switches, first-line mirror/render correctness, and repeated/overlapping copies in all directions. The COM tests use simple known oracles and independent normal-core comparison, not physical hardware as an oracle.

## Consequences for next experiments

1. **H5 / x87 is now a concrete candidate.** The direct FPU helper call sequence in `0x306bd`, and x87 setup in `0x112cc`, can be inspected for safe lowering or batching; a frequent helper call does not establish that an optimization exists. Preserve floating-point environment, stack/register state, memory translation, and fault/exception semantics.
2. **VGA `REP MOVSD` is a separate candidate.** `VGA_ChainedVGA_Handler::writed` performs address conversion, banking/masking, delay, change tracking and updates both backing memory and fast cache including first-line replication. A naive RAM pointer or memcpy bypass can be wrong. First isolate which costs are paid and prove correct mode/boundary/exception handling with additional tests.
3. **Stack pushes are a third candidate, not yet an optimization.** The checked path protects exception semantics; identify protected-mode faults and restart state before modifying it.

All FPS from diagnostic executions must remain diagnostic. Small performance gains require a separately controlled, trace-OFF A/B experiment on a more stable host and additional representative workloads.
