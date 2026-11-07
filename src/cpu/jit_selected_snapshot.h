// SPDX-FileCopyrightText: 2026 DOSBox Staging guest JIT research
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Diagnostic only. Included in core_dyn_x86.cpp after dyn_cache.h and paging.h.
// Captured guest/native machine code may be proprietary: NEVER commit output.
#pragma once

#include <algorithm>
#include <array>
#include <cerrno>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <string>
#include <unistd.h>
#include <vector>

namespace JitSelectedSnapshot {

constexpr size_t MaxTargets = 32;
constexpr size_t MaxGuestBytes = 4096;
constexpr size_t MaxHostBytes = 64 * 1024;

struct Configuration {
	bool initialized = false;
	bool enabled = false;
	std::string dir = {};
	std::array<uint32_t, MaxTargets> eips = {};
	size_t eip_count = 0;
	size_t snapshots = 0;
	size_t max_snapshots = 16;
};
inline Configuration config;

inline void Initialize()
{
	if (config.initialized)
		return;
	config.initialized = true;
	const char* dir = std::getenv("DOSBOX_JIT_SNAPSHOT_DIR");
	const char* eips = std::getenv("DOSBOX_JIT_SNAPSHOT_EIPS");
	if (!dir || !*dir || !eips || !*eips)
		return;
	config.dir = dir;
	const char* current = eips;
	while (*current) {
		while (*current == ',' || *current == ' ' || *current == '\t')
			++current;
		if (!*current)
			break;
		if (config.eip_count == MaxTargets) {
			std::fprintf(stderr, "JIT snapshot: at most %zu EIPs allowed\n", MaxTargets);
			return;
		}
		errno = 0;
		char* end = nullptr;
		const unsigned long value = std::strtoul(current, &end, 0);
		if (errno || current == end || value > std::numeric_limits<uint32_t>::max() ||
		    (*end != ',' && *end != ' ' && *end != '\t' && *end != '\0')) {
			std::fprintf(stderr, "JIT snapshot: invalid EIP list\n");
			return;
		}
		config.eips[config.eip_count++] = static_cast<uint32_t>(value);
		current = end;
	}
	if (const char* limit = std::getenv("DOSBOX_JIT_SNAPSHOT_MAX"); limit && *limit) {
		errno = 0;
		char* end = nullptr;
		const unsigned long value = std::strtoul(limit, &end, 10);
		if (errno || *end || value < 1 || value > 256) {
			std::fprintf(stderr, "JIT snapshot: invalid max (expected 1..256)\n");
			return;
		}
		config.max_snapshots = static_cast<size_t>(value);
	}
	config.enabled = config.eip_count > 0;
}

inline bool WriteAll(int fd, const uint8_t* ptr, size_t length)
{
	while (length) {
		const auto n = write(fd, ptr, length);
		if (n < 0 && errno == EINTR)
			continue;
		if (n <= 0)
			return false;
		ptr += n;
		length -= static_cast<size_t>(n);
	}
	return true;
}

inline bool WriteExclusive(const std::string& path, const uint8_t* bytes, size_t size)
{
	const int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
	if (fd < 0)
		return false;
	const bool ok = WriteAll(fd, bytes, size);
	const bool closed = close(fd) == 0;
	if (!ok || !closed) {
		(void)unlink(path.c_str());
		return false;
	}
	return true;
}

// Direct TLB access never invokes a new guest memory handler or page fault.
// If a page lacks a readable direct mapping, report the snapshot incomplete.
inline bool ReadGuestMappedByte(uint32_t linear, uint8_t& result)
{
	const auto host_base = get_tlb_read(linear);
	if (!host_base)
		return false;
	result = *(host_base + linear);
	return true;
}

inline void Capture(const CacheBlock& block,
                    uint32_t guest_linear_begin,
                    uint32_t guest_linear_end,
                    uint16_t guest_cs,
                    uint32_t guest_eip,
                    size_t native_bytes)
{
	// Match the generation in the lifecycle CSV; never invent a second ID.
	if (!block.jit_trace_id)
		return;
	Initialize();
	if (!config.enabled || config.snapshots >= config.max_snapshots)
		return;
	if (std::find(config.eips.begin(), config.eips.begin() + config.eip_count,
	              guest_eip) == config.eips.begin() + config.eip_count)
		return;
	++config.snapshots;
	if (guest_linear_end < guest_linear_begin || !block.cache.start ||
	    !native_bytes || native_bytes > MaxHostBytes) {
		std::fprintf(stderr, "JIT snapshot: invalid native/guest extent\n");
		return;
	}
	const uint64_t guest_length = static_cast<uint64_t>(guest_linear_end) -
	                              guest_linear_begin;
	const auto wanted = static_cast<size_t>(
	        std::min<uint64_t>(guest_length, MaxGuestBytes));
	std::vector<uint8_t> guest;
	guest.reserve(wanted);
	bool directly_readable = true;
	for (size_t i = 0; i < wanted; ++i) {
		uint8_t byte = 0;
		if (!ReadGuestMappedByte(guest_linear_begin + static_cast<uint32_t>(i), byte)) {
			directly_readable = false;
			break;
		}
		guest.push_back(byte);
	}
	const bool guest_complete = directly_readable && guest.size() == guest_length;
	const std::string prefix = config.dir + "/jit-" + std::to_string(getpid()) +
	                           "-gen" + std::to_string(block.jit_trace_id) +
	                           "-cs" + std::to_string(guest_cs) +
	                           "-eip" + std::to_string(guest_eip);
	const std::string guest_path = prefix + ".guest.bin";
	const std::string host_path = prefix + ".host.bin";
	const std::string meta_path = prefix + ".json";
	const auto native = reinterpret_cast<const uint8_t*>(block.cache.start);
	if (!WriteExclusive(guest_path, guest.data(), guest.size()) ||
	    !WriteExclusive(host_path, native, native_bytes)) {
		std::fprintf(stderr,
		             "JIT snapshot: failed writing blobs for CS:EIP %04x:%08x\n",
		             guest_cs, guest_eip);
		return;
	}
	char meta[2048] = {};
	const auto host_addr = reinterpret_cast<uintptr_t>(native);
	const auto guest_name = guest_path.substr(config.dir.size() + 1);
	const auto host_name = host_path.substr(config.dir.size() + 1);
	const int len = std::snprintf(
	        meta, sizeof(meta),
	        "{\n  \"schema\": 1,\n  \"pid\": %ld,\n  \"generation\": %llu,\n"
	        "  \"guest_cs\": \"0x%04x\",\n  \"guest_eip\": \"0x%08x\",\n"
	        "  \"guest_linear_begin\": \"0x%08x\",\n"
	        "  \"guest_linear_end\": \"0x%08x\",\n"
	        "  \"guest_expected_bytes\": %llu,\n"
	        "  \"guest_captured_bytes\": %zu,\n  \"guest_complete\": %s,\n"
	        "  \"guest_capture_method\": \"post-decode direct TLB, no guest reads\",\n"
	        "  \"host_begin\": \"0x%llx\",\n  \"host_end\": \"0x%llx\",\n"
	        "  \"host_bytes\": %zu,\n"
	        "  \"native_includes_deferred_slow_paths\": true,\n"
	        "  \"guest_filename\": \"%s\",\n  \"host_filename\": \"%s\"\n}\n",
	        static_cast<long>(getpid()),
	        static_cast<unsigned long long>(block.jit_trace_id),
	        static_cast<unsigned>(guest_cs),
	        static_cast<unsigned>(guest_eip),
	        static_cast<unsigned>(guest_linear_begin),
	        static_cast<unsigned>(guest_linear_end),
	        static_cast<unsigned long long>(guest_length), guest.size(),
	        guest_complete ? "true" : "false",
	        static_cast<unsigned long long>(host_addr),
	        static_cast<unsigned long long>(host_addr + native_bytes),
	        native_bytes, guest_name.c_str(), host_name.c_str());
	if (len <= 0 || static_cast<size_t>(len) >= sizeof(meta) ||
	    !WriteExclusive(meta_path, reinterpret_cast<const uint8_t*>(meta),
	                    static_cast<size_t>(len))) {
		std::fprintf(stderr,
		             "JIT snapshot: metadata write failed for generation %llu\n",
		             static_cast<unsigned long long>(block.jit_trace_id));
	}
}

} // namespace JitSelectedSnapshot
