# Bit-test and SETcc opcodes are missing from the dynamic core (diagnostic evidence)

This document records a **diagnostic finding**, not an optimization. It
identifies a gap in the x86-64 dynamic core's opcode tables, shows the exact
execution path the dynamic core takes when it meets the gap, identifies the
guest code that runs into it in original DOS Quake 1.06, and provides a guest
regression test with a measurable coverage report.

It is the (a)–(d) evidence package required by the hypothesis register in
[kaaburgh/engineering-lab](https://github.com/kaaburgh/engineering-lab)
before a dynamic-core optimization may be proposed for this instruction
family.

## Summary

| Item | Value |
|---|---|
| Gap | 168 of the 256 two-byte `0F` sub-opcodes are not implemented, including the whole `SETcc` family, `BT`/`BTS`/`BTR`/`BTC`, `BSF`/`BSR`, `CMOVcc`, `CMPXCHG` (0F B1) and `PREFETCH`; one-byte `SAHF` (0x9E) and `LAHF` (0x9F) are missing as well |
| Execution path | decoder `illegalopcode:` → `gen_return(BR_Opcode)` → `CPU_Core_Dyn_X86_Run` → `sync_dh_fpu_and_run_normal_core()` → **x87 state transfer** → `CPU_Core_Normal_Run()` |
| Guest impact | original Quake 1.06 executes `BT`/`BTR` inside a per-node BSP frustum-culling loop, and `SAHF` in its x87 comparison idiom |
| Measured fallbacks | **1,963,529** dynamic-core → interpreter fallbacks in one timedemo window: `BT` 69.5%, `SETE` 15.1%, `SAHF` 9.5%, `BTR` 1.7%, `BTS` 0.2% |
| Measured cost | **1.20%** of the sampled main-thread time is directly attributable (interpreter 0.56% + x87 state transfer 0.64%); the dispatcher and code-page re-resolution add further cost that sampling cannot separate, so 1.3–1.7% is a credible total |
| Guest regression test | `bit_test_semantics.asm`, 19 cases, both CPU cores byte-identical, zero deviations, with a measured translation-coverage report |

## 1. The gap in the decoder

`src/cpu/core_dyn_x86/decoder.h` dispatches the two-byte opcode space in the
`case 0x0f:` branch of the main opcode switch. That branch implements
**88 of the 256** two-byte sub-opcodes; the remaining **168** reach the
`default:` label and fall through to `illegalopcode`.

| Family | Opcodes | Implemented? |
|---|---|---|
| `SETcc` | `0F 90`–`0F 9F` | **none of 16** |
| `BT`, `BTS`, `BTR`, `BTC` | `0F A3`, `0F AB`, `0F B3`, `0F BB`, `0F BA /4../7` | **none of 5** |
| `BSF`, `BSR` | `0F BC`, `0F BD` | none |
| `CMOVcc` | `0F 40`–`0F 4F` | **none of 16** |
| `CMPXCHG` | `0F B0`, `0F B1` | `0F B1` missing |
| `PREFETCH` | `0F 18` | missing |
| `LAHF`, `SAHF` | one-byte `0x9F`, `0x9E` | **both missing** (the one-byte switch implements `0x9C` PUSHF and `0x9D` POPF, but not `0x9E`/`0x9F`) |

`PUSHF` and `POPF` *are* implemented, which matters for the regression test
below: a guest program can read the carry flag without using `SETcc`.

The two families that matter for Quake are the bit-test family and the
x87 comparison idiom `fnstsw ax; sahf`, which the Quake compiler emits for
every floating-point comparison and which uses `SAHF`.

## 2. The code path

An unhandled opcode reaches the `illegalopcode:` label of the decoder
(`src/cpu/core_dyn_x86/decoder.h`):

```c
illegalopcode:
	dyn_set_eip_last();
	dyn_reduce_cycles();
	dyn_save_critical_regs();
	gen_return(BR_Opcode);
	dyn_closeblock();
```

`gen_return()` (`src/cpu/core_dyn_x86/risc_x64.h`) emits the block exit
template, with `BR_Opcode` as the return code:

```c
static void gen_return(BlockReturn retcode) {
	gen_protectflags();
	opcode(1).setea(4,-1,0,CALLSTACK).Emit8(0x8B); // mov ecx, [rsp+8/40]
	opcode(0).set64().setrm(4).setimm(CALLSTACK+8,1).Emit8(0x83); // add rsp,16/48
	if (retcode==0) cache_addw(0xc033);		// xor eax,eax
	else {
		cache_addb(0xb8);	//MOV EAX, retcode
		cache_addd(retcode);
	}
	opcode(4).setea(4,-1,0,CALLSTACK-8).Emit8(0xFF); // jmp [rsp+CALLSTACK-8]
}
```

`BR_Opcode` is the value 4 of the `BlockReturn` enum in
`src/cpu/core_dyn_x86.cpp`, and the dispatcher answers it there — **not** in
`core_dynrec.cpp`, whose body is compiled out on x86-64:

```c
	case BR_Opcode:
		CPU_CycleLeft+=CPU_Cycles;
		CPU_Cycles=1;
		{
			return sync_dh_fpu_and_run_normal_core();
		}
```

`sync_dh_fpu_and_run_normal_core()` (`src/cpu/core_dyn_x86.cpp`) is where
the **x87 state is carried across the boundary** in both directions:

```c
static Bits sync_dh_fpu_and_run_normal_core() noexcept
{
	if (last_core == CoreType::Dynamic) {
		maybe_sync_host_fpu_to_dh();
		FPU_SetTag(static_cast<uint16_t>(dyn_dh_fpu.state.tag & 0xffff));
		FPU_SetCW(static_cast<uint16_t>(dyn_dh_fpu.state.cw & 0xffff));
		FPU_SetSW(static_cast<uint16_t>(dyn_dh_fpu.state.sw & 0xffff));
		TOP = FPU_GET_TOP();
		FPU_SetPRegsFrom(dyn_dh_fpu.state.st_reg);
		last_core = CoreType::Normal;
	}
	assert(!dyn_dh_fpu.state_used);
	return CPU_Core_Normal_Run();
}
```

and the mirror image on the way back (`FPU_GetPRegsTo`). Every fallback
therefore costs an 8×10-byte x87 register transfer plus tag/control/status
word handling, on top of the block exit, the interpreter dispatch of a
single instruction, and the re-entry into the dynamic core.

## 3. Which guest code reaches the gap

Original DOS Quake 1.06 (SHA-256
`7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb`) reaches
the gap in the **client-side software renderer**:

| Guest CS:EIP | Instruction | Role |
|---|---|---|
| `0x00A7:0x2F5C6` | `BT r/m32, r32` | test one frustum-plane bit per plane |
| `0x00A7:0x2F6A6` | `BTR r/m32, r32` | clear that bit again |
| `0x00A7:0x31B6D` | `SETcc` | the compiler's x87 comparison idiom |
| `0x00A7:0x4ED40`, `0x4ED5C` | `SAHF` | the compiler's x87 comparison idiom |

### Source-level identification

`QUAKE.EXE` is a COFF image **without a symbol table** (`nsyms = 0`), so the
function identity was reconstructed from structural markers and matched
against the id Software sources (commit `bf4ac42`), the same source the
hypothesis register uses.

The function starting at guest `0x2F584` is **`R_RecursiveWorldNode`**:

| Machine-code marker | Source counterpart |
|---|---|
| `cmp DWORD PTR [ebp+0x0],0xfffffffe` | `if (node->contents == CONTENTS_SOLID) return;` (`CONTENTS_SOLID = -2`) |
| `cmp DWORD PTR [ebp+0x4], <r_visframecount>` | `if (node->visframe != r_visframecount) return;` |
| `movsx eax,WORD PTR [ebp+eax*2+0x8]` | `(float)node->minmaxs[pindex[0]]` — `minmaxs` is a `short` array |
| `movzx eax,BYTE PTR [edx+0x10]` with a 0/1/2 switch | axial-plane fast path selecting a precomputed normal |
| `fld`/`fmul`/`faddp` over `plane->normal`, then `fsub [edx+0xc]` | `d = DotProduct(rejectpt, view_clipplanes[i].normal); d -= view_clipplanes[i].dist;` |
| table of 6 `int` indices per plane at a global | `pfrustum_indexes[4][2][3]` |

The bit-test instructions sit inside the per-plane loop of that function, so
a node visit performs several fallbacks.

For completeness, the hottest x87 block of the same family, guest `0x306BD`,
is **`R_ClipEdge`** (`r_draw.c`): it computes
`d0 = DotProduct(pv0->position, clip->normal) - clip->dist` and the same
for `d1`, stores the `0x7FFFFFFF` "do not cache clipped edges" sentinel and
computes the interpolation factor `f = d0 / (d0 - d1)`. Its compiler-emitted
`fnstsw ax; sahf` comparisons are the other half of the `SAHF` traffic.

### Measured fallback distribution

An instrumented build that counts every runtime fallback into the
interpreter, keyed by guest address, gives for one headless timedemo
window:

| Opcode | Fallbacks | Share |
|---|---:|---:|
| `BT` | 1,365,451 | 69.54% |
| `SETE` (`0F 94`) | 296,144 | 15.08% |
| `SAHF` (`0x9E`) | 186,271 | 9.49% |
| `BTR` (`0F B3`) | 32,673 | 1.66% |
| `BTS` (`0F AB`) | 3,981 | 0.20% |
| `SETLE`, `SETL`, `SETNE` | 3,251 | 0.17% |
| **total** | **1,963,529** | 100% |

Hotest individual sites:

| Guest CS:EIP | Instruction | Fallbacks |
|---|---|---:|
| `0x00A7:0x2F5C6` | `BT` | 894,085 |
| `0x00A7:0x0491C` | `BT` | 368,672 |
| `0x00A7:0x31B6D` | `SETE` | 289,242 |
| `0x00A7:0x32994` | `BT` | 102,502 |
| `0x00A7:0x4ED40` | `SAHF` | 97,808 |
| `0x00A7:0x4ED5C` | `SAHF` | 88,458 |
| `0x00A7:0x2F6A6` | `BTR` | 32,673 |

### Measured cost

SIGPROF samples of the main thread inside the timedemo window, attributed by
symbol:

| Symbol | Samples | Share of window | Role |
|---|---:|---:|---|
| `CPU_Core_Normal_Run` | 42 | 0.56% | the interpreter executing the untranslatable instruction |
| `FPU_GetPRegsTo` | 29 | 0.39% | x87 state transfer out of the dynamic core |
| `FPU_SetPRegsFrom` | 19 | 0.25% | x87 state transfer into the interpreter |
| **directly attributable total** | **90** | **1.20%** | |

The dispatcher (`CPU_Core_Dyn_X86_Run`, 2.26% of the window) and the code
page resolution (`MakeCodePage`, 1.52%) also carry part of the fallback
cost, but they also serve ordinary block execution, so sampling cannot
separate the two. **1.20% is therefore a floor, and 1.3–1.7% is a credible
estimate of the total.**

Note that the `SAHF` and `SETcc` fallbacks are not incidental: they are the
compiler's x87 comparison idiom, and they sit inside the very routines that
the hypothesis register tracks as H5.

## 4. Guest regression test

`bit_test_semantics.asm` is a real-mode DOS program with two sections:

* **16 cases of the bit-test family** (`BT`, `BTS`, `BTR`, `BTC` with a
  register and with an immediate bit index) on a 32-bit operand with bits 0
  and 31 set, including an index that must be masked to five bits. The
  carry flag is captured with `PUSHF`/`POP`, **not** with `SETcc`.
* **3 cases of the x87 comparison idiom** (`fld; fldz; fucompp; fnstsw ax;
  sahf`), which is what the Quake compiler emits for every floating-point
  comparison.

The program compares every result against expectations computed from the
Intel SDM and writes the raw results to `BTOUT.BIN` through `int 21h`;
byte 1 of that file is the number of deviations it counted itself.

The `PUSHF` choice matters: an earlier revision of this test read the carry
flag with `SETcc`, and since `SETcc` is **not translated either**, that
revision compared the interpreter with the interpreter and proved nothing.
The current revision uses `PUSHF`, which the dynamic core does implement, so
the test program is genuinely translated — and the runner reports how much
of it.

`run_bit_test_semantics.py` runs the program on both CPU cores, compares
the outputs byte for byte, and — with a trace-enabled build — reports the
translation coverage and the fallback profile:

```sh
nasm -f bin bit_test_semantics.asm -o BITSEM.COM
python3 run_bit_test_semantics.py \
    --dosbox /path/to/dosbox --trace-dosbox /path/to/dosbox-trace \
    --out /tmp/bit-test
```

Result on the fork's current sources (see
[evidence/bit-test-semantics-2026-11-09.md](evidence/bit-test-semantics-2026-11-09.md)):

| Check | Result |
|---|---|
| `cpu core = normal` vs `cpu core = dynamic` | byte-identical output, SHA-256 `6a4d859e19bc8ab1f8f885c1…` |
| in-program self-check | **0 deviations out of 19** |
| dynamic core coverage | 33 blocks, 689 of 1021 image bytes translated (67.5%) |
| uncovered *executed* ranges | exactly the 16 bit-test and 3 `SAHF` instructions |
| fallback profile of the run | 19 fallbacks, one per uncovered instruction |

## 5. Acceptance criteria for a follow-up implementation

This PR proposes **no** emulator change. If the gap is closed later, the
change is acceptable only if all of the following hold:

1. `bit_test_semantics.asm` still reports 0 deviations out of 19 on
   `cpu core = dynamic`, and the output file stays byte-identical to the
   interpreter run.
2. The dynamic core's block trace shows a **translated** block covering the
   bit-test guest addresses instead of an uncovered range, and the run's
   fallback count for those addresses drops to zero.
3. The interpreter and the dynamic core agree for the **16-bit operand
   forms** and for a bit index that exceeds the operand width. For a
   *register* bit base the index is masked to the operand size; for a
   **memory** bit base the instruction operates on a *bit string* that may
   extend beyond the operand's dword, so the implementation must reproduce
   the SDM's addressing (byte offset `index/8`, bit `index%8`) instead of
   masking the index, and must not fault on the extended access. The
   committed test covers neither memory operands nor the 16-bit forms.
4. `SETcc`, `LAHF` and `SAHF` are addressed as well, or explicitly left out
   of scope with a measurement that says what that costs — the `SAHF`
   fallbacks alone are 9.5% of the measured fallback traffic, and the
   `SETcc` fallbacks another 15.3%.
5. The original Quake 1.06 timedemo still completes the same number of
   frames, and any performance statement is backed by an A/A control run on
   a stable host.

## Privacy

This document contains no Quake assets, no raw guest byte snapshots, no raw
native code blobs, no process maps and no raw sample addresses. Only
hashes, block-lifecycle metadata, counter aggregates and derived
disassembly observations are included.
