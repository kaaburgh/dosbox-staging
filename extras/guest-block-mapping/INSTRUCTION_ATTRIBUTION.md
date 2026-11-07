# Verify sampled native JIT instructions (Quake H1/H2)

This follow-up is stacked on native RIP profiling PR #3. **No additional
Quake execution or speedup is claimed.** It is an offline decoder/evidence
validation step runnable even without DOSBox or game assets.

## Rationale

The PR #3 profiler reports an exact native RIP offset within a generation's
emitted JIT bytes, plus coarse 16-byte bins. A bin is not an x86 instruction
and cannot tell whether a sampled RIP is on a TLB lookup, an arithmetic
operation, or a deferred helper-exit path.

This tool invokes GNU objdump with 64-bit x86 binary disassembly on the
*private* host blob and maps a sampled RIP to a **start offset in the linear
disassembly**. It verifies every disassembled byte against the original
blob; the raw blob must match the SHA-256 and length originally reported by
PR #3, keyed by JIT generation and guest EIP. The tool rejects mismatched
samples, missing/unknown generations, corrupted blobs and unexpected
snapshot filenames. Output contains no native bytes or absolute addresses.

## Offline usage

Run PR #3's analyze_native_rips.py first, producing
derived/summary.json and derived/native-rips.csv. Keep its raw
snapshots directory private.

    python3 extras/guest-block-mapping/resolve_native_instructions.py \
      --summary /private/run/derived/summary.json \
      --rips /private/run/derived/native-rips.csv \
      --snapshots /private/run/snapshots \
      --out /private/run/native-instructions

Outputs:
- summary.json: selected samples, exact linear-instruction-start matches,
  unmatched counts and coarse machine-instruction family totals.
- sampled-instructions.csv: generation, guest EIP, native byte offset,
  instruction length, mnemonic, redacted operand shape, family and RIP
  sample count.

The tool intentionally avoids embedding full instruction bytes or raw
process addresses in its output. Even the derived CSV must be reviewed
before publishing because some operand syntax may reveal program structure.

## Important limits

- **Linear sweep != verified execution CFG.** Code generation may emit
  unreachable deferred paths, embedded data or alternate instruction entries.
  A sampled RIP at an offset that is not a linear-sweep instruction start is
  counted as ambiguous, *never* folded into the preceding instruction.
- The small mnemonic families are **syntactic**, not JIT semantic labels:
  'move-or-address' does not mean TLB load, 'call-site' does not establish
  helper execution counts, and one sample does not equal one instruction
  execution.
- RIPs outside the JIT still cannot be assigned to C++ helpers or callers.
  Instruction attribution helps examine H1's inline fast path, but does not
  close the helper blind spot.
- Repeated profiles with multiple sampling periods and verified control-flow
  interpretations are needed before favoring H1 over H2.
- Final performance A/B must use uninstrumented builds on a stable host.

## Local validation (2026-11-07)

The tool was executed in the available Debian 13 container with GNU
objdump, against synthetic native x86-64 bytes only (not game code).
Seven instruction-level tests and six PR #3 generation-level tests passed.
The tests check full disassembly coverage, instruction start offsets,
non-start sample ambiguity, modified snapshot hashes, mismatched count
tables, unknown JIT generations, process-address redaction, and
syntax-only mnemonic categories. CI reruns both suites.

The complete historical nine-block Quake captures from PR #2 were not
available in this session's container. No actual H1/H2 hotspot percentages
were inferred from synthetic results.
