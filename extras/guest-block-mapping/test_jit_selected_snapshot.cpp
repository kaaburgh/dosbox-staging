// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of diagnostic snapshot formatting, selection and missing-TLB path.
// No emulator, game assets, or guest memory handlers required.
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>
#include <unistd.h>

struct CacheBlock {
	uint64_t jit_trace_id = 0;
	struct { const uint8_t* start = nullptr; } cache;
};
static std::vector<uint8_t> guest_memory(65536);
static bool page_unavailable = false;
static uint8_t* get_tlb_read(uint32_t address)
{
	if (page_unavailable && address >= 8192)
		return nullptr;
	return guest_memory.data();
}
#include "../../src/cpu/jit_selected_snapshot.h"

int main()
{
	char directory[] = "/tmp/jit-selected-snapshot-test-XXXXXX";
	assert(mkdtemp(directory));
	assert(setenv("DOSBOX_JIT_SNAPSHOT_DIR", directory, 1) == 0);
	assert(setenv("DOSBOX_JIT_SNAPSHOT_EIPS", "0x10d3b,0x11343", 1) == 0);
	assert(setenv("DOSBOX_JIT_SNAPSHOT_MAX", "2", 1) == 0);
	for (unsigned i = 0; i < 40; ++i)
		guest_memory[0x1100 + i] = static_cast<uint8_t>(0x50 + i);
	uint8_t native[] = {0x48, 0x89, 0xd8, 0xc3, 0x90};
	CacheBlock block;
	block.jit_trace_id = 102;
	block.cache.start = native;
	JitSelectedSnapshot::Capture(block, 0x1100, 0x1110,
	                             0x00a7, 0x10d3b, sizeof(native));
	bool guest_ok = false;
	bool native_ok = false;
	bool meta_ok = false;
	int files = 0;
	for (const auto& item : std::filesystem::directory_iterator(directory)) {
		++files;
		std::ifstream f(item.path(), std::ios::binary);
		std::string bytes((std::istreambuf_iterator<char>(f)), {});
		const auto name = item.path().string();
		if (name.ends_with(".guest.bin")) {
			assert(bytes.size() == 16);
			for (unsigned i = 0; i < 16; ++i)
				assert(static_cast<uint8_t>(bytes[i]) == 0x50 + i);
			guest_ok = true;
		}
		if (name.ends_with(".host.bin")) {
			assert(bytes.size() == sizeof(native));
			assert(static_cast<uint8_t>(bytes[0]) == native[0]);
			native_ok = true;
		}
		if (name.ends_with(".json")) {
			assert(bytes.find("\"guest_complete\": true") != std::string::npos);
			assert(bytes.find("\"generation\": 102") != std::string::npos);
			meta_ok = true;
		}
	}
	assert(files == 3 && guest_ok && native_ok && meta_ok);
	CacheBlock second = block;
	second.jit_trace_id = 103;
	JitSelectedSnapshot::Capture(second, 0x1100, 0x1110,
	                             0x00a7, 0xdead, sizeof(native));
	assert(std::distance(std::filesystem::directory_iterator(directory),
	                     std::filesystem::directory_iterator()) == 3);

	// Cross-page code without a direct readable TLB mapping: mark incomplete,
	// do not invoke a guest memory handler, fault, or misrepresent exact bytes.
	page_unavailable = true;
	JitSelectedSnapshot::Capture(second, 0x1ff8, 0x2010,
	                             0x00a7, 0x11343, sizeof(native));
	bool incomplete = false;
	for (const auto& item : std::filesystem::directory_iterator(directory)) {
		if (!item.path().string().ends_with(".json"))
			continue;
		std::ifstream f(item.path());
		const std::string meta((std::istreambuf_iterator<char>(f)), {});
		if (meta.find("\"guest_complete\": false") != std::string::npos)
			incomplete = true;
	}
	assert(incomplete);
	assert(JitSelectedSnapshot::config.snapshots == 2);
	std::filesystem::remove_all(directory);
}
