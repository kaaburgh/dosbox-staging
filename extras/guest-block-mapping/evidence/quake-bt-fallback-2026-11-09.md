# Quake 1.06 guest reaches the missing bit-test opcodes — fallback evidence (2026-11-09)

Diagnostic evidence for the hypothesis register's H5 entry, collected with
the trace-enabled fork build on original DOS Quake 1.06.

| Artifact | SHA-256 |
|---|---|
| DOSBox, Release, `OPT_JIT_BLOCK_TRACE=ON` | `ffd4e25086813cd54299eb3ce9a416c64a4cb72067a2b911d07107caa8903b2d` |
| `QUAKE.EXE` | `7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb` |
| `PAK0.PAK` | `35a9c55e5e5a284a159ad2a62e0e8def23d829561fe2f54eb402dbc0a9a946af` |

The Quake assets were used locally only. No asset, guest byte snapshot,
native code blob, process map or raw sample address is part of this
document or of the repository.

## 1. Measured fallback cost

Headless timedemo run, 21 selected guest entry blocks, extended SIGPROF
sampler (1100 µs period) that also captures eight words of the
interrupted thread's stack:

| Quantity | Value |
|---|---:|
| Samples inside the timedemo window | 7,520 |
| Samples inside dynamic-core blocks | 3,871 (51.5%) |
| Samples inside emulator helpers | 1,243 (16.5%) |
| Helper samples attributed to a calling guest block | 1,045 (**84.07%** of helper samples) |
| Samples inside `CPU_Core_Normal_Run` | **38 (0.51% of the window)** |

Helper breakdown (share of the whole window):

| Helper | Samples | Share |
|---|---:|---:|
| `mem_readd` | 525 | 6.98% |
| `mem_writed_checked` | 194 | 2.58% |
| `FPU_FLD_32` | 122 | 1.62% |
| `mem_writed` | 108 | 1.44% |
| `CPU_Core_Normal_Run` | 38 | **0.51%** |
| `mem_readw` | 24 | 0.32% |
| `FPU_FST_32` | 19 | 0.25% |
| `mem_writew` | 13 | 0.17% |
| `mem_readb_checked` | 2 | 0.03% |

By guest region (share of helper traffic, and of the sampling window):

| Region | Samples | % of helpers | % of window |
|---|---:|---:|---:|
| `0x2F000–0x31FFF` (renderer, x87-heavy) | 383 | 30.8% | 5.09% |
| `0x10A8–0x12FFF` (span region) | 284 | 22.8% | 3.78% |
| `0x30600–0x30800` (`R_ClipEdge`) | 133 | 10.7% | 1.77% |
| `0x10D00–0x10E00` (`D_DrawZSpans`) | 13 | 1.05% | 0.17% |
| `0x3E000–0x3FFFF` (`surf8` region) | 5 | 0.40% | 0.07% |

## 2. The guest block that falls back

The dynamic core's own lifecycle trace for the relevant guest addresses:

| Generation | Guest CS:EIP | Guest range | Native size |
|---|---|---|---:|
| 5404 | `0x00A7:0x2F5B3` | `[0x1002F5B3, 0x1002F5C8)` | 266 B |
| 5405 | `0x00A7:0x2F5C9` | `[0x1002F5C9, 0x1002F5CF)` | 143 B |
| 5406 | `0x00A7:0x2F5CF` | `[0x1002F5CF, 0x1002F636)` | 1917 B |
| 5410 | `0x00A7:0x2F5C6` | `[0x1002F5C6, 0x1002F5C8)` | 94 B |

Two details matter:

* The block that starts at `0x2F5B3` ends at `0x2F5C8`, that is, **right
  after** the bit-test instruction at `0x2F5C6`.
* The block created for `0x2F5C6` itself covers exactly that one
  instruction (2 guest bytes) and is 94 native bytes long: it is the
  `illegalopcode:` stub, whose exit is the `gen_return(BR_Opcode)`
  template of `src/cpu/core_dyn_x86/risc_x64.h` with `mov eax,0x4`
  (`BR_Opcode` is 4 in the `BlockReturn` enum of
  `src/cpu/core_dynrec.cpp`).

The captured guest snapshot for that block contains exactly the two-byte
encoding of `BT r/m32, r32` (opcode `0F A3`); the sibling instruction at
guest `0x2F6A6` is `BTR r/m32, r32` (`0F B3`).

### Attribution correction

An earlier attribution pass assigned the interpreter samples to the block
at `0x2F5C9`. That is an artifact of the heuristic, which maps a helper
sample to the nearest preceding dynamic-core block by native address; the
94-byte stub for `0x2F5C6` is allocated *after* the block for `0x2F5C9`, so
it lost the comparison. The correct owner is the single-instruction stub at
`0x2F5C6`, which is confirmed independently by the block boundaries and by
the `BR_Opcode` exit code in the stub's native bytes.

## 3. Source-level identification

`QUAKE.EXE` carries no symbol table, so the two hot blocks were
identified from structural markers against the id Software sources
(commit `bf4ac42`):

| Guest block | Function | Decisive markers |
|---|---|---|
| `0x2F584` … `0x2F930` | **`R_RecursiveWorldNode`** | `contents == CONTENTS_SOLID` (-2) check, `visframe != r_visframecount` check, `minmaxs` read as signed `short`, axial-plane type switch, `dot(corner, plane->normal) - plane->dist`, and a table of 6 `int` indices per frustum plane (`pfrustum_indexes[4][2][3]`) |
| `0x306A4` … `0x3075B` | **`R_ClipEdge`** | `DotProduct(pv0->position, clip->normal) - clip->dist` for both edge endpoints, the `0x7FFFFFFF` "do not cache clipped edges" sentinel, and the interpolation factor `f = d0 / (d0 - d1)` |

The bit-test instructions sit inside the per-plane loop of
`R_RecursiveWorldNode`, i.e. they execute several times for every visited
BSP node, which is why a one-bit instruction is worth measuring.

## 4. Limits

* All numbers come from an **instrumented** build that costs roughly 25%
  of the uninstrumented frame rate. They must not be mixed with clean FPS
  figures.
* The shares are SIGPROF hit statistics at a 1100 µs period, not an exact
  time budget.
* The 0.51% interpreter share is a **lower bound** on the real cost of the
  gap: the interpreter round trip also breaks the dynamic core's block
  chain, and that dispatcher cost does not appear as interpreter time.
* The function names are reconstructions from machine-code structure
  matched against the id Software sources; they are not taken from a
  symbol table.
