// Research-only sampled direct caller probe. Private files contain addresses.
#pragma once
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <unistd.h>
namespace JitHelperProbe {
struct Record { uint64_t timestamp, return_address, sequence; uint32_t address, kind; uintptr_t handler_vptr; };
static_assert(sizeof(Record) == 40);
static int fd = -2;
static uint64_t count[5] = {};
static uint64_t stride = 257;
static void Sample(uint32_t kind, uintptr_t ret, uint32_t address, const void* handler = nullptr) {
 if (fd == -2) {
  fd = -1;
  if (auto p = std::getenv("DOSBOX_JIT_HELPER_PROBE"); p && *p) {
   if (const char* value = std::getenv("DOSBOX_JIT_HELPER_STRIDE"); value && *value) {
    char* end = nullptr;
    const auto parsed = std::strtoul(value, &end, 10);
    if (*end || parsed < 1 || parsed > 1000000) {
     std::fprintf(stderr, "JIT helper probe: invalid stride\n"); return;
    }
    stride = parsed;
   }
   fd = open(p, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
   if (fd < 0) std::fprintf(stderr, "JIT helper probe: open failed\n");
  }
 }
 if (fd < 0) return;
 const auto n = ++count[kind];
 if (((n - 1) % stride) != 0) return;
 uintptr_t vptr = 0;
 if (handler) std::memcpy(&vptr, handler, sizeof(vptr));
 const Record r{JitBlockTrace::NowNs(), ret, n, address, kind, vptr};
 if (write(fd, &r, sizeof(r)) != sizeof(r)) {
  std::fprintf(stderr, "JIT helper probe: write failed\n"); close(fd); fd = -1;
 }
}
}
