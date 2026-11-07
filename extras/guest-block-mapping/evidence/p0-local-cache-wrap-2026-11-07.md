# Local native JIT-cache wraparound regression — 2026-11-07

## Purpose and origin

This is a **correctness** test of DOSBox Staging's experimental guest-to-host JIT block mapping, not a performance benchmark. All DOS programs in this report ran **inside the assistant's local Linux x86-64 container**, never on GitHub Actions. GitHub Actions was used only to build and export the instrumented Linux executable and NASM test program.

- Fork source revision used for both 8 MiB and 1 MiB builds: \`ca75ee731a5d31648768b1481483e3bc4846eb63\`.
- Diagnostic code-cache difference: \`#define CACHE_TOTAL (1024*1024*1)\` in temporary \`core_dyn_x86.cpp\` source for the 1 MiB build; standard cache is 8 MiB.
- Build: Release, dynamic x86 core, \`OPT_JIT_BLOCK_TRACE=ON\`, original DOS guest instructions unchanged.
- Build environment: Debian 13; temporary, unrelated SDL3 mapper and FluidSynth MIDI API compatibility shims. **No JIT logic besides the deliberate cache-size constant differs.**
- Guest test \`WRAP.COM\` comes from this PR's \`cache_wrap_stress.asm\`; NASM output is **byte-for-byte identical** to the local GNU \`as\`/GNU \`ld\` assembled COM file.
- Guest \`WRAP.COM\` SHA-256: \`277e8d8428c15caf643165c584879562bbe16633f7b1075f9d079e64417e4dde\`.
- 1 MiB DOSBox executable SHA-256: \`bf3de48d2b5cc98a6ded0f64dcbe4f98d7085479d08d4f681181aa640bd65c84\`.

## Results

| Invariant | 8 MiB cache (control) | 1 MiB cache (stress) |
|---|---:|---:|
| Guest program executions | 1 | 3 |
| Checksum validated by DOS guest | yes | yes, all three |
| JIT CREATE events | 16,025 | 48,062 |
| JIT INVALIDATE events | 6 | 39,968 |
| Unique reused native JIT start addresses | **0** | **8,535** |
| Large backwards jumps of native cache allocation pointer | **0** | **5** |
| Sum of emitted native instructions | 1.87 MiB | 5.59 MiB |
| Span of physical host JIT addresses | 1.96 MiB | **1.00 MiB** |
| Exact expected DOS guest block entries found | **16,000 / 16,000** | **16,000 / 16,000** |
| Guest \`CS:EIP → linear\` translation failures | 0 | 0 |
| Overlaps of simultaneously live native code ranges | 0 | 0 |
| Invalid generation lifecycle operations | 0 | 0 |

The native JIT address pointer physically wrapped **five times** during the three consecutive DOS guest executions under the 1 MiB configuration. The recorder assigned distinct generation IDs, with previous generations invalidated before the same host cache memory was reused.

**Negative control:** \`verify_wrap_trace.py --require-reuse\` passed on the 1 MiB trace and correctly **rejected the 8 MiB control trace** with \`no native JIT start address reused; cache never wrapped\`. This prevents a false-positive test that only checks successful guest program execution.

The test additionally confirms that the guest program's \`BX\` register reached **16,000** before each success marker \`PASS1.OK\`, \`PASS2.OK\` and \`PASS3.OK\` was generated. This checks arithmetic and control-flow semantics across JIT reuse, not just the validity of the recorded CSV.

## Integrity

\`\`\`text
bf3de48d2b5cc98a6ded0f64dcbe4f98d7085479d08d4f681181aa640bd65c84  dosbox-smallcache
277e8d8428c15caf643165c584879562bbe16633f7b1075f9d079e64417e4dde  WRAP.COM
37b9df63f20c785866f386cb8156e9b44ec0ecf4ac30cb04b97ac1a53dceebab  1mib-threepass/jit-blocks.csv
238678844119c4bebe7be19e759d0573b094ff6024d80cfbd06b431f2e21134b  8mib-onepass/jit-blocks.csv
\`\`\`

The full native lifecycle CSV traces, output logs, checksummed COM and machine-readable local manifest are in the conversation's \`dosbox-jit-cache-wrap-local-evidence-2026-11-07.zip\` artifact. No executable or proprietary game files are committed.

## Limitations

- This covers **code-cache wraparound** for 16-bit DOS \`INC BX; JMP\` blocks and earlier verified self-modifying and cross-page programs. It does not prove all CPU-mode transitions, JIT branches, exception paths, FPU code or guest-memory helper semantics.
- The 1 MiB build is for stress testing only and should **never be used as the main Quake performance benchmark**.
- The cached DOS program, analyzer and lifecycle metadata have been checked independently; reproducibility depends on preserving the executable/library/toolchain manifests.
- An orderly shutdown footer is not emitted: an external log corruption or short write may yield an incomplete trace. The analyzer validates rows and detects errors when their malformed bytes are present; the recorder reports I/O errors to stderr, but a cleanly truncated log ending on a row boundary is not always distinguishable without additional end-of-run evidence.

Related offline-only [build/export workflow](https://github.com/kaaburgh/configs/actions/runs/42377443722) and [NASM source/export workflow](https://github.com/kaaburgh/configs/actions/runs/42378495221).
