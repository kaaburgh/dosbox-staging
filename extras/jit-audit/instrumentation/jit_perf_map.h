// SPDX-License-Identifier: GPL-2.0-or-later
//
// Research-only JIT symbolisation for `perf`. Compiled in only when
// DOSBOX_JIT_PERF_MAP is defined; it never changes emitted code.
//
// - Appends "<start> <size> jit_<cs>:<eip>_g<seq>" lines to
//   /tmp/perf-<pid>.map so `perf report` can name samples in the code cache.
// - When DOSBOX_JIT_DUMP=<file> is set, also writes binary records with the
//   emitted native bytes and the guest bytes read through the existing TLB
//   (no handler calls, so no side effects). Dumps may contain proprietary
//   guest code: keep them private.
#pragma once

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <unistd.h>

namespace JitPerfMap {

inline FILE* map_file  = nullptr;
inline FILE* dump_file = nullptr;
inline bool initialised = false;
inline uint64_t sequence = 0;

inline uint64_t NowNs()
{
	timespec ts = {};
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL +
	       static_cast<uint64_t>(ts.tv_nsec);
}

inline void Init()
{
	if (initialised) {
		return;
	}
	initialised = true;
	if (!std::getenv("DOSBOX_JIT_PERF_MAP")) {
		return;
	}
	char path[64];
	std::snprintf(path, sizeof(path), "/tmp/perf-%d.map", static_cast<int>(getpid()));
	map_file = std::fopen(path, "w");
	if (const char* dump = std::getenv("DOSBOX_JIT_DUMP"); dump && *dump) {
		dump_file = std::fopen(dump, "wb");
	}
}

inline void BlockCreated(const uint8_t* native, uint32_t native_len,
                         uint32_t guest_linear, uint32_t guest_end_linear,
                         uint16_t cs, uint32_t eip)
{
	Init();
	if (!map_file) {
		return;
	}
	const auto seq = ++sequence;
	std::fprintf(map_file, "%lx %x jit_%04x:%08x_g%lu\n",
	             reinterpret_cast<unsigned long>(native), native_len, cs, eip,
	             static_cast<unsigned long>(seq));
	std::fflush(map_file);
	if (!dump_file) {
		return;
	}
	uint32_t guest_len = guest_end_linear > guest_linear
	                           ? guest_end_linear - guest_linear
	                           : 0;
	if (guest_len > 4096) {
		guest_len = 0;
	}
	uint8_t guest[4096];
	uint32_t got = 0;
	for (; got < guest_len; ++got) {
		const auto addr = guest_linear + got;
		const HostPt tlb = get_tlb_read(addr);
		if (!tlb) {
			break;
		}
		guest[got] = host_readb(tlb + addr);
	}
	const uint32_t magic = 0x314b4c42; // "BLK1"
	const uint64_t now = NowNs();
	const uint64_t addr = reinterpret_cast<uint64_t>(native);
	std::fwrite(&magic, 4, 1, dump_file);
	std::fwrite(&seq, 8, 1, dump_file);
	std::fwrite(&now, 8, 1, dump_file);
	std::fwrite(&guest_linear, 4, 1, dump_file);
	std::fwrite(&cs, 2, 1, dump_file);
	std::fwrite(&eip, 4, 1, dump_file);
	std::fwrite(&addr, 8, 1, dump_file);
	std::fwrite(&native_len, 4, 1, dump_file);
	std::fwrite(&got, 4, 1, dump_file);
	std::fwrite(native, 1, native_len, dump_file);
	std::fwrite(guest, 1, got, dump_file);
}

inline void BlockCleared(const uint8_t* native)
{
	if (!dump_file) {
		return;
	}
	const uint32_t magic = 0x31564e49; // "INV1"
	const uint64_t seq = ++sequence;
	const uint64_t now = NowNs();
	const uint64_t addr = reinterpret_cast<uint64_t>(native);
	std::fwrite(&magic, 4, 1, dump_file);
	std::fwrite(&seq, 8, 1, dump_file);
	std::fwrite(&now, 8, 1, dump_file);
	std::fwrite(&addr, 8, 1, dump_file);
}

} // namespace JitPerfMap
