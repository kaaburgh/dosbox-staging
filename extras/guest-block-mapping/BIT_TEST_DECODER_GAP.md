# Bit-test opcodes are missing from the dynamic core (diagnostic evidence)

This document records a **diagnostic finding**, not an optimization. It
identifies a gap in the x86-64 dynamic core's two-byte opcode table, shows
the exact execution path the dynamic core takes when it meets the gap, and
identifies the guest code that runs into it in original DOS Quake 1.06.

It is the (a)–(d) evidence package required by the hypothesis register in
[kaaburgh/engineering-lab](https://github.com/kaaburgh/engineering-lab)
before a dynamic-core optimization may be proposed for this instruction
family.

## Summary

| Item | Value |
|---|---|
| Gap | `0F A3`, `0F AB`, `0F B3`, `0F BB`, `0F BA /4../7` are not implemented by the dynamic core |
| Affected instructions | `BT`, `BTS`, `BTR`, `BTC` with a register or immediate bit index |
| Execution path | decoder `illegalopcode:` → `gen_return(BR_Opcode)` → `CPU_Core_Normal_Run()` |
| Guest impact | original Quake 1.06 executes `BT`/`BTR` inside a per-node BSP frustum-culling loop |
| Measured cost | 38 of 7520 main-thread samples (0.51%) landed in `CPU_Core_Normal_Run` in the Quake timedemo window |
| Guest regression test | `bit_test_semantics.asm`, 16 cases, both CPU cores byte-identical, zero deviations |

## 1. The gap in the decoder

`src/cpu/core_dyn_x86/decoder.h` dispatches the two-byte opcode space in the
`case 0x0f:` branch of the main opcode switch. That branch implements
**88 of the 256** two-byte sub-opcodes. The bit-test family is entirely
absent:

| Opcode | Instruction | Implemented? |
|---|---|---|
| `0F A3` | `BT r/m32, r32` | no |
| `0F AB` | `BTS r/m32, r32` | no |
| `0F B3` | `BTR r/m32, r32` | no |
| `0F BB` | `BTS r/m32, imm8` | no |
| `0F BA /4` | `BT r/m32, imm8` | no |
| `0F BA /5` | `BTS r/m32, imm8` | no |
| `0F BA /6` | `BTR r/m32, imm8` | no |
| `0F BA /7` | `BTC r/m32, imm8` | no |

Neighbouring instructions such as `BSF`/`BSR` (`0F BC`/`0F BD`) are missing
as well, so this is a family-level gap and not a single forgotten case.

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
template:

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

`BR_Opcode` is the value 4 of the `BlockReturn` enum
(`src/cpu/core_dynrec.cpp`):

```c
enum BlockReturn {
	BR_Normal=0,
	BR_Cycles,
	BR_Link1,BR_Link2,
	BR_Opcode,
	...
```

The dispatcher handles it in `CPU_Core_Dynrec_Run()`:

```c
		case BR_Opcode:
			// some instruction has been encountered that could not be translated
			// (thus it is not part of the code block), the normal core will
			// handle this instruction
			CPU_CycleLeft+=CPU_Cycles;
			CPU_Cycles=1;
			return CPU_Core_Normal_Run();
```

So the guest instruction is executed by the **interpreter**, one instruction
at a time, and the dynamic core's block chain is broken at that point. This
is a correct fallback, but it is also the slowest possible path for a
one-bit instruction.

## 3. Which guest code reaches the gap

Original DOS Quake 1.06 (SHA-256
`7cdb244122cc607cc6f162524bfcac60f6d11d8ca2ff07473d6cd962311161fb`) uses
the bit-test family in the **client-side software renderer**, inside the
BSP traversal that culls nodes against the four frustum planes.

The two hot guest instructions are:

| Guest CS:EIP | Instruction | Role |
|---|---|---|
| `0x00A7:0x2F5C6` | `BT r/m32, r32` | test one frustum-plane bit per plane |
| `0x00A7:0x2F6A6` | `BTR r/m32, r32` | clear that bit again |

Both live in the same loop, so a node visit that walks four planes performs
several interpreter round-trips.

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

The same loop's dot product is the reason the block is x87-heavy: the corner
coordinates are `short` values converted with `fild`, and the plane distance
is accumulated in the x87 stack.

For completeness, the second hottest block of this family, guest `0x306BD`,
is **`R_ClipEdge`** (`r_draw.c`): it computes
`d0 = DotProduct(pv0->position, clip->normal) - clip->dist` and the same
for `d1`, stores the `0x7FFFFFFF` "do not cache clipped edges" sentinel and
computes the interpolation factor `f = d0 / (d0 - d1)`.

### Evidence chain for the fallback

1. **Live guest bytes.** The captured guest snapshot for the block at
   `0x2F5C6` contains exactly the two-byte encoding of `BT r/m32, r32`
   (opcode `0F A3`).
2. **Block boundaries.** The dynamic core's own lifecycle trace shows the
   block that starts at guest `0x2F5B3` ending at guest `0x2F5C8`, i.e.
   immediately after the bit-test instruction, and a separate single
   instruction block created for guest `0x2F5C6` itself.
3. **Native code path.** That single-instruction block is 94 native bytes
   long and its exit sequence is the `gen_return(BR_Opcode)` template above,
   with `mov eax,0x4` — `BR_Opcode` is 4.
4. **Measured cost.** In a headless Quake 1.06 timedemo run with an
   extended SIGPROF sampler, 38 of 7520 main-thread samples in the timedemo
   window were inside `CPU_Core_Normal_Run` (0.51%); 19 of them belong to
   the bit-test block discussed above.

The sample share is a lower bound on the real cost: every interpreter round
trip also breaks the dynamic core's block chain and returns control to the
dispatcher, and that transition cost is not visible as interpreter time.

## 4. Guest regression test

`bit_test_semantics.asm` is a real-mode DOS program that executes 16 cases
of the bit-test family on a 32-bit operand and checks every result against
expectations computed from the Intel SDM:

* `BT`/`BTS`/`BTR`/`BTC` with a register bit index (including an index that
  must be masked to five bits),
* `BT`/`BTS`/`BTR`/`BTC` with an immediate bit index,
* both the carry flag and the resulting operand value,
* an operand with bits 0 and 31 set, so that set/clear/toggle results are
  distinguishable.

It writes the raw results to `BTOUT.BIN` through `int 21h` and reports the
number of deviations from the expected values in its own output.

`run_bit_test_semantics.py` assembles and runs the program on both CPU
cores and compares the results byte for byte:

```sh
nasm -f bin bit_test_semantics.asm -o BITSEM.COM
python3 run_bit_test_semantics.py --dosbox /path/to/dosbox --out /tmp/bit-test
```

Result on the fork's current `main` (see
[evidence/bit-test-semantics-2026-11-09.md](evidence/bit-test-semantics-2026-11-09.md)):
both cores produce the identical SHA-256
`640dffd87f299bc437c43c9ae07825deabd04057f295fc3a65ae13fa1580fd37` and the
in-program self-check reports **0 deviations out of 16**.

The trace-enabled run additionally shows that the dynamic core emits only
three blocks for the whole test program — the last one ends exactly at the
first bit-test instruction — while the program still completes correctly.
Everything after that point is executed by the interpreter.

## 5. Acceptance criteria for a follow-up implementation

This PR proposes **no** emulator change. If the gap is closed later, the
change is acceptable only if all of the following hold:

1. `bit_test_semantics.asm` still reports 0 deviations out of 16 on
   `cpu core = dynamic`, and the output file stays byte-identical to the
   interpreter run.
2. The interpreter and the dynamic core agree for the 16-bit operand forms
   and for a bit index that exceeds the operand width (currently undefined
   by the SDM and therefore not covered by the committed test).
3. The dynamic core's block trace shows a **translated** block at the
   bit-test guest address instead of a `BR_Opcode` stub.
4. The original Quake 1.06 timedemo still completes the same number of
   frames, and any performance statement is backed by an A/A control run on
   a stable host.

## Privacy

This document contains no Quake assets, no raw guest byte snapshots, no raw
native code blobs, no process maps and no raw sample addresses. Only
hashes, block-lifecycle metadata and derived disassembly observations are
included.
