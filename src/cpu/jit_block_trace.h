// SPDX-FileCopyrightText:  2026 DOSBox Staging guest-block mapping experiment
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Included only from core_dyn_x86.cpp after dyn_cache.h when the diagnostic
// CMake option OPT_JIT_BLOCK_TRACE is enabled. Do not include in other TUs.

#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <time.h>
#include <unistd.h>

namespace JitBlockTrace {

// Negative file descriptor means disabled; -2 is uninitialised.
static int fd = -2;
static uint64_t next_generation = 0;

static uint64_t NowNs()
{
    timespec ts = {};
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return 0;
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL + ts.tv_nsec;
}

// A truncated record is worse than no trace: stop recording on I/O error.
// Writes normally finish in one syscall, but handle EINTR and partial writes.
static bool WriteAll(const char* data, size_t length)
{
    while (length) {
        const auto written = write(fd, data, length);
        if (written < 0 && errno == EINTR)
            continue;
        if (written <= 0) {
            const int error = written == 0 ? EIO : errno;
            std::fprintf(stderr,
                         "JIT block trace: recording stopped after write failure: %s\n",
                         std::strerror(error));
            (void)close(fd);
            fd = -1;
            return false;
        }
        data += written;
        length -= static_cast<size_t>(written);
    }
    return true;
}

static void Initialize()
{
    if (fd != -2)
        return;
    fd = -1;
    const char* path = std::getenv("DOSBOX_JIT_BLOCK_TRACE");
    if (!path || !*path)
        return;
    // O_EXCL prevents accidentally overwriting or mixing traces.
    const int opened = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (opened < 0) {
        std::fprintf(stderr, "JIT block trace: cannot create %s: %s\n",
                     path, std::strerror(errno));
        return;
    }
    fd = opened;
    static constexpr char header[] =
            "event,monotonic_ns,pid,generation,guest_cs,guest_eip,guest_linear_begin,guest_linear_end,host_begin,host_end\n";
    (void)WriteAll(header, sizeof(header) - 1);
}

static void Emit(const char event, const uint64_t generation,
                 const uint16_t guest_cs, const uint32_t guest_eip,
                 const uint32_t guest_begin, const uint32_t guest_end,
                 const uintptr_t host_begin, const uintptr_t host_end)
{
    Initialize();
    if (fd < 0)
        return;
    char line[256];
    const int length = std::snprintf(line, sizeof(line),
        "%c,%llu,%ld,%llu,0x%04x,0x%08x,0x%08x,0x%08x,0x%llx,0x%llx\n",
        event,
        static_cast<unsigned long long>(NowNs()),
        static_cast<long>(getpid()),
        static_cast<unsigned long long>(generation),
        static_cast<unsigned>(guest_cs),
        static_cast<unsigned>(guest_eip),
        static_cast<unsigned>(guest_begin),
        static_cast<unsigned>(guest_end),
        static_cast<unsigned long long>(host_begin),
        static_cast<unsigned long long>(host_end));
    if (length > 0 && static_cast<size_t>(length) < sizeof(line))
        (void)WriteAll(line, static_cast<size_t>(length));
}

} // namespace JitBlockTrace

inline void JitBlockTrace_Invalidate(CacheBlock& block)
{
    if (!block.jit_trace_id)
        return;
    JitBlockTrace::Emit('D', block.jit_trace_id, 0, 0, 0, 0, 0, 0);
    block.jit_trace_id = 0;
}

inline void JitBlockTrace_Create(CacheBlock& block,
                                 uint32_t guest_begin,
                                 uint32_t guest_end,
                                 uint16_t guest_cs,
                                 uint32_t guest_eip,
                                 size_t native_bytes)
{
    // Code may be overwritten without a corresponding page-handler clear;
    // in that case explicitly close the old generation first.
    JitBlockTrace_Invalidate(block);
    JitBlockTrace::Initialize();
    if (JitBlockTrace::fd < 0)
        return;
    const uint64_t id = ++JitBlockTrace::next_generation;
    block.jit_trace_id = id;
    const auto host_begin = reinterpret_cast<uintptr_t>(block.cache.start);
    JitBlockTrace::Emit('C', id, guest_cs, guest_eip,
                         guest_begin, guest_end, host_begin,
                         host_begin + native_bytes);
}