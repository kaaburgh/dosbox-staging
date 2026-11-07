// SPDX-License-Identifier: GPL-2.0-or-later
// Test the recorder without DOSBox/SDL: fake CacheBlock, real Linux I/O.
#include <cstdint>
struct CacheBlock {
    uint64_t jit_trace_id = 0;
    struct { const uint8_t* start = nullptr; } cache;
};
#include "../../src/cpu/jit_block_trace.h"
#include <cassert>
#include <fstream>
#include <string>
#include <vector>

int main()
{
    char dir[] = "/tmp/dosbox-jit-io-XXXXXX";
    assert(mkdtemp(dir) != nullptr);
    const std::string path = std::string(dir) + "/trace.csv";
    assert(setenv("DOSBOX_JIT_BLOCK_TRACE", path.c_str(), 1) == 0);
    const uint8_t instructions[] = {0x90, 0x90};
    CacheBlock block;
    block.cache.start = instructions;
    JitBlockTrace_Create(block, 0x1234, 0x1236, 0xa7, 0x1234, 2);
    assert(block.jit_trace_id == 1);
    JitBlockTrace_Invalidate(block);
    assert(block.jit_trace_id == 0);

    std::ifstream stream(path);
    std::vector<std::string> lines;
    for (std::string line; std::getline(stream, line); )
        lines.push_back(line);
    assert(lines.size() == 3);
    assert(lines[0].starts_with("event,monotonic_ns,pid,generation,"));
    assert(lines[1].starts_with("C,"));
    assert(lines[2].starts_with("D,"));
    assert(lines[1].find(",0x00a7,0x00001234,") != std::string::npos);

    // /dev/full causes a deterministic write error (ENOSPC).
    // Recording must stop and report the failure.
    close(JitBlockTrace::fd);
    JitBlockTrace::fd = open("/dev/full", O_WRONLY);
    assert(JitBlockTrace::fd >= 0);
    JitBlockTrace::Emit('C', 100, 0, 0, 0, 0, 0, 0);
    assert(JitBlockTrace::fd == -1);
    (void)unlink(path.c_str());
    (void)rmdir(dir);
    return 0;
}
