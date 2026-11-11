// SPDX-License-Identifier: GPL-2.0-or-later
//
// Cost of the guest-flags save that the x86-64 dynamic core emits before an
// inlined TLB-checked memory access (gen_protectflags: PUSHFQ; LEA RSP,-8),
// measured in the same instruction sequence the JIT emits for a dword store.
//
//   A: OR (flag producer) + PUSHFQ/LEA + TLB-checked store + ADD RSP,16
//   B: same without the flag save (flags dead)
//   C: A followed by a POPFQ restore (flags live after the access)
//   D: LAHF/SETO-style save into a register instead of PUSHFQ
//
// Build: gcc -O2 -mno-red-zone -o flags_save flags_save.c ; run: ./flags_save [iterations]
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static uint64_t tlb[1 << 20];
static uint32_t page[1024 * 4];

static double now(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec * 1e-9;
}

#define BODY_PRE                                                             \
	"xor %%r13d, %%r13d\n\t"                                             \
	"xor %%r15d, %%r15d\n\t"                                             \
	"or %%r15d, %%ecx\n\t"
#define TLB_STORE                                                            \
	"lea 3(%%r13), %%rax\n\t"                                            \
	"and $-4096, %%rax\n\t"                                              \
	"cmp %%r13, %%rax\n\t"                                               \
	"ja 9f\n\t"                                                          \
	"shr $12, %%eax\n\t"                                                 \
	"mov (%[tlb],%%rax,8), %%rax\n\t"                                    \
	"test %%rax, %%rax\n\t"                                              \
	"je 9f\n\t"                                                          \
	"mov %%ecx, (%%rax,%%r13)\n\t"                                       \
	"9:\n\t"

static double run(int variant, long iters)
{
	tlb[0] = (uint64_t)(uintptr_t)page;
	double t0 = now();
	for (long i = 0; i < iters; ++i) {
		switch (variant) {
		case 0:
			__asm__ volatile(BODY_PRE "pushfq\n\tlea -8(%%rsp), %%rsp\n\t" TLB_STORE
			                 "add $16, %%rsp\n\t"
			                 :
			                 : [tlb] "r"(tlb), "c"(1), "r"(0)
			                 : "rax", "r13", "r15", "memory", "cc");
			break;
		case 1:
			__asm__ volatile(BODY_PRE TLB_STORE
			                 :
			                 : [tlb] "r"(tlb), "c"(1)
			                 : "rax", "r13", "r15", "memory", "cc");
			break;
		case 2:
			__asm__ volatile(BODY_PRE "pushfq\n\tlea -8(%%rsp), %%rsp\n\t" TLB_STORE
			                 "add $8, %%rsp\n\tpopfq\n\t"
			                 :
			                 : [tlb] "r"(tlb), "c"(1)
			                 : "rax", "r13", "r15", "memory", "cc");
			break;
		case 3:
			__asm__ volatile(BODY_PRE "lahf\n\tseto %%dl\n\tmov %%eax, %%r8d\n\t" TLB_STORE
			                 "mov %%r8d, %%eax\n\tadd $127, %%dl\n\tsahf\n\t"
			                 :
			                 : [tlb] "r"(tlb), "c"(1)
			                 : "rax", "rdx", "r8", "r13", "r15", "memory", "cc");
			break;
		}
	}
	return (now() - t0) / iters * 1e9;
}

int main(int argc, char** argv)
{
	long iters = argc > 1 ? atol(argv[1]) : 50000000;
	const char* names[] = {"A pushfq+store", "B store only", "C pushfq+store+popfq",
	                       "D lahf/seto+store+sahf"};
	for (int rep = 0; rep < 3; ++rep) {
		for (int v = 0; v < 4; ++v) {
			printf("%-24s %.3f ns/iter\n", names[v], run(v, iters));
		}
	}
	return 0;
}
