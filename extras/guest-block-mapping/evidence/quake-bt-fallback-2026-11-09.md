# Quake 1.06 reaches the missing opcodes — measured fallback profile (2026-11-09)

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

## 1. Fallback profile

An instrumented build counts every runtime transition from the dynamic core
into the interpreter, keyed by guest address. One headless timedemo window:

| Opcode | Fallbacks | Share |
|---|---:|---:|
| `BT` | 1,365,451 | 69.54% |
| `SETE` (`0F 94`) | 296,144 | 15.08% |
| `SAHF` (`0x9E`) | 186,271 | 9.49% |
| `BTR` (`0F B3`) | 32,673 | 1.66% |
| `BTS` (`0F AB`) | 3,981 | 0.20% |
| `SETLE`, `SETL`, `SETNE` | 3,251 | 0.17% |
| non-guest segments (DOS kernel, DPMI helper) | 74,758 | 3.81% |
| **total** | **1,963,529** | 100% |

Hotest individual guest sites:

| Guest CS:EIP | Instruction | Fallbacks | Share of total |
|---|---|---:|---:|
| `0x00A7:0x2F5C6` | `BT` | 894,085 | 45.5% |
| `0x00A7:0x0491C` | `BT` | 368,672 | 18.8% |
| `0x00A7:0x31B6D` | `SETE` | 289,242 | 14.7% |
| `0x00A7:0x32994` | `BT` | 102,502 | 5.2% |
| `0x00A7:0x4ED40` | `SAHF` | 97,808 | 5.0% |
| `0x00A7:0x4ED5C` | `SAHF` | 88,458 | 4.5% |
| `0x00A7:0x2F6A6` | `BTR` | 32,673 | 1.7% |
| `0x00A7:0x2CE25` | `SETE` | 4,469 | 0.2% |
| `0x00A7:0x32F50` | `BTS` | 3,424 | 0.2% |

Three quarters of all fallbacks come from two opcodes, and the single
hottest site is the bit-test instruction in the BSP frustum-culling loop.

## 2. Measured cost

SIGPROF samples of the main thread inside the same timedemo window,
attributed by symbol (7,520 samples in the window):

| Symbol | Samples | Share of window | Role |
|---|---:|---:|---|
| `CPU_Core_Normal_Run` | 42 | 0.56% | the interpreter runs the untranslatable instruction |
| `FPU_GetPRegsTo` | 29 | 0.39% | x87 state transfer out of the dynamic core |
| `FPU_SetPRegsFrom` | 19 | 0.25% | x87 state transfer into the interpreter |
| **directly attributable** | **90** | **1.20%** | |

Two further symbols carry part of the fallback cost but also serve ordinary
block execution, so sampling cannot separate the shares:

| Symbol | Samples | Share of window |
|---|---:|---:|
| `CPU_Core_Dyn_X86_Run` (dispatcher) | 170 | 2.26% |
| `MakeCodePage` (code page resolution) | 114 | 1.52% |

**1.20% is a floor.** Each of the ~1.96 million fallbacks also exits a
block, re-enters the dispatcher, re-resolves the code page and resumes
translation; a credible total is **1.3–1.7%** of the main thread.

## 3. The x87 share of the helper traffic

The helper attribution (see the research repository) resolves nested helper
calls as well, i.e. it can tell a `mem_readd` that was called by
`FPU_FLD_32` from one that was called by guest code directly:

| Caller → callee | Samples | Share of window |
|---|---:|---:|
| `FPU_FLD_32` → `mem_readd` | 389 | 5.17% |
| `FPU_FLD_32` (own samples) | 122 | 1.62% |
| `FPU_FST_32` (own samples) | 19 | 0.25% |
| **x87-attributable total** | **530** | **7.05%** |

Only 136 of the 525 `mem_readd` samples (2.58% of the window) are direct
guest memory reads. The remaining 74% of `mem_readd` traffic is x87
operand loading, which is why the raw `mem_readd` share must not be read as
"guest memory access cost".

## 4. Time per guest region

Samples inside dynamic-core blocks, attributed to the guest block, share of
the window:

| Region | JIT-block samples | Share of window | Helper samples | Share of window |
|---|---:|---:|---:|---:|
| `0x10A8–0x12FFF` (span region) | 1,368 | 18.19% | 284 | 3.78% |
| `0x2F000–0x31FFF` (renderer) | 1,060 | 14.10% | 383 | 5.09% |
| `0x3E000–0x3FFFF` (`surf8`) | 346 | **4.60%** | 5 | 0.07% |
| `0x10D00–0x10E00` (Z-span) | 137 | **1.82%** | 13 | 0.17% |
| `0x30600–0x30800` (`R_ClipEdge`) | 96 | 1.28% | 133 | 1.77% |

Helper traffic alone understates these regions: the `surf8` region holds
4.6% of the main thread in translated code although it produces almost no
helper calls, and the Z-span region holds 1.8%.

The Z-span block `0x10D3B` shows why: its native code materialises the
flags with a `PUSHFQ` before each z-buffer store, which is the
"flag materialization" mechanism the hypothesis register describes for H2.

## 5. Source-level identification

`QUAKE.EXE` carries no symbol table, so the hot blocks were identified from
structural markers against the id Software sources (commit `bf4ac42`):

| Guest block | Function | Decisive markers |
|---|---|---|
| `0x2F584` … `0x2F930` | **`R_RecursiveWorldNode`** | `contents == CONTENTS_SOLID` (-2) check, `visframe != r_visframecount` check, `minmaxs` read as signed `short`, axial-plane type switch, `dot(corner, plane->normal) - plane->dist`, and a table of 6 `int` indices per frustum plane (`pfrustum_indexes[4][2][3]`) |
| `0x306A4` … `0x3075B` | **`R_ClipEdge`** | `DotProduct(pv0->position, clip->normal) - clip->dist` for both edge endpoints, the `0x7FFFFFFF` "do not cache clipped edges" sentinel, and the interpolation factor `f = d0 / (d0 - d1)` |

## 6. Limits

- All numbers come from an **instrumented** build that costs roughly 25% of
  the uninstrumented frame rate. They must not be mixed with clean FPS
  figures.
- The shares are SIGPROF hit statistics at a 1100 µs period, not an exact
  time budget.
- The fallback counts are exact, but they count *transitions*, not time: a
  fallback in an x87-heavy context costs more than one in a trivial one.
- The 1.20% attributable cost is a floor; see section 2.
- The function names are reconstructions from machine-code structure
  matched against the id Software sources; they are not taken from a
  symbol table.
