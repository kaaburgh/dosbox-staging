// SPDX-License-Identifier: GPL-2.0-or-later
//
// Research-only exact event counters for the x86-64 dynamic core. Compiled in
// only with DOSBOX_JIT_COUNTERS. Counters inside generated code use a
// flags-neutral sequence (push/mov/lea/mov/pop), so guest flags and register
// allocation are unaffected; the generated code is larger, so this build is
// for counting only, never for timing.
//
// Run with DOSBOX_JIT_COUNTERS=<file>; every SIGUSR1 appends one snapshot
// (Count little-endian uint64 values) to the file.
#pragma once

#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <fcntl.h>
#include <unistd.h>

namespace JitCounters {

enum Id {
	PushfqExec,     // executions of PUSHFQ emitted by gen_protectflags
	PopfqExec,      // executions of POPFQ emitted by gen_needflags
	PushHelper,     // dyn_push helper calls (mem_write{w,d}_checked)
	PopHelper,      // dyn_pop checked helper calls (mem_read{w,d}_checked)
	FpuFld32,       // FPU_FLD_32 calls
	FpuFst32,       // FPU_FST_32 calls
	FpuFld64,       // FPU_FLD_64 calls
	DispatchLoop,   // restart_core iterations in CPU_Core_Dyn_X86_Run
	MakeCodePageCall,
	RunBlock,       // blocks entered from the C++ dispatcher
	NormalCoreRun,  // sync_dh_fpu_and_run_normal_core
	CreateBlock,
	ReadSlowPath,   // dyn_read_word TLB-miss helper calls (excl. page crossing)
	WriteSlowPath,  // dyn_write_word TLB-miss helper calls (excl. page crossing)
	PushFastEligible,   // push helper calls whose access is in-page with a direct TLB pointer
	PopFastEligible,    // pop helper calls whose access is in-page with a direct TLB pointer
	DispatchSamePage,   // dispatcher entries whose code page equals the previous entry's
	FallbackNoCodePage, // normal core because MakeCodePage gave no code page
	FallbackInvalidationMap, // normal core because the byte was modified >= 4 times
	FallbackSmc,        // BR_SMCBlock
	FallbackOpcode,     // BR_Opcode (instruction not handled by the dynamic core)
	FallbackTrap,       // single-step trap run
	Count
};

inline uint64_t counters[Count] = {};
// Opcode histogram for BR_Opcode fallbacks: [0..255] one-byte opcodes after
// prefixes, [256..511] 0F-prefixed opcodes.
inline uint64_t opcode_hist[512] = {};
inline int fd = -1;

inline void Snapshot(int)
{
	if (fd >= 0) {
		[[maybe_unused]] auto r = write(fd, counters, sizeof(counters));
		[[maybe_unused]] auto q = write(fd, opcode_hist, sizeof(opcode_hist));
	}
}

inline void Init()
{
	static bool done = false;
	if (done) {
		return;
	}
	done = true;
	const char* path = std::getenv("DOSBOX_JIT_COUNTERS");
	if (!path || !*path) {
		return;
	}
	fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	std::signal(SIGUSR1, Snapshot);
}

// push rax; push rcx; mov rax, imm64; mov rcx,[rax]; lea rcx,[rcx+1];
// mov [rax],rcx; pop rcx; pop rax  -- no flag changes
inline void Emit(Id id)
{
	cache_addb(0x50);
	cache_addb(0x51);
	cache_addb(0x48);
	cache_addb(0xB8);
	cache_addq(reinterpret_cast<uint64_t>(&counters[id]));
	cache_addb(0x48); cache_addb(0x8B); cache_addb(0x08);
	cache_addb(0x48); cache_addb(0x8D); cache_addb(0x49); cache_addb(0x01);
	cache_addb(0x48); cache_addb(0x89); cache_addb(0x08);
	cache_addb(0x59);
	cache_addb(0x58);
}

} // namespace JitCounters

#define JIT_COUNT_EMIT(id) JitCounters::Emit(JitCounters::id)
#define JIT_COUNT(id) (++JitCounters::counters[JitCounters::id])
