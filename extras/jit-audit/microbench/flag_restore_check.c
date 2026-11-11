// SPDX-License-Identifier: GPL-2.0-or-later
//
// Exhaustive native check of the POPFQ-free guest-flag restore sequence used
// by the x86-64 dynamic core (candidate C4). The exact byte sequence the JIT
// emits is executed from an RWX buffer for every 12-bit saved-flags image
// (all 64 combinations of CF PF AF ZF SF OF times every combination of the
// other low bits a saved image can contain, TF excluded), with RAX "live"
// (must be preserved) and with the popping and non-popping variants.
//
// Checked for each image v:
//   1. the six arithmetic flags after the sequence equal those of v;
//   2. they equal what POPFQ produces for the same image;
//   3. DF and the other non-arithmetic flags of the host are unchanged;
//   4. RAX is preserved when the save/restore pair is emitted;
//   5. RSP is adjusted by exactly 16 (pop variant) or 0 (keep variant).
//
// Build: gcc -O2 -o flag_restore_check flag_restore_check.c ; exit code 0 = pass
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>

#define ARITH 0x8D5u // OF SF ZF AF PF CF

// SysV: CALLSTACK = 8. Saved image at [rsp+8], scratch slot at [rsp].
static const uint8_t save_rax[]    = {0x48, 0x89, 0x04, 0x24};       // mov [rsp], rax
static const uint8_t restore[]     = {0x8A, 0x44, 0x24, 0x09,        // mov al, [rsp+9]
                                      0x24, 0x08,                    // and al, 8
                                      0x04, 0x78,                    // add al, 0x78   -> OF
                                      0x8A, 0x64, 0x24, 0x08,        // mov ah, [rsp+8]
                                      0x9E};                         // sahf           -> SF ZF AF PF CF
static const uint8_t reload_rax[]  = {0x48, 0x8B, 0x04, 0x24};       // mov rax, [rsp]
static const uint8_t pop_image[]   = {0x48, 0x8D, 0x64, 0x24, 0x10}; // lea rsp, [rsp+16]
static const uint8_t popfq_ref[]   = {0x48, 0x83, 0xC4, 0x08, 0x9D}; // add rsp, 8 ; popfq (baseline)

struct result {
	uint64_t flags, rax, rsp_delta;
};

typedef void (*harness_fn)(uint64_t image, uint64_t rax_in, struct result* out);

static uint8_t* emit(uint8_t* p, const uint8_t* b, size_t n)
{
	memcpy(p, b, n);
	return p + n;
}

// rdi = image, rsi = rax_in, rdx = out
static harness_fn build(uint8_t* buf, int variant, int live_rax, int pop)
{
	uint8_t* p = buf;
	static const uint8_t pre[] = {
	        0x49, 0x89, 0xE0,             // mov r8, rsp
	        0x49, 0x89, 0xD1,             // mov r9, rdx
	        0x48, 0x83, 0xEC, 0x10,       // sub rsp, 16
	        0x48, 0x89, 0x7C, 0x24, 0x08, // mov [rsp+8], rdi
	        0x48, 0x89, 0xF0,             // mov rax, rsi
	        0x31, 0xC9, 0x48, 0xFF, 0xC9, // xor ecx,ecx ; dec rcx  (scramble host flags: SF=1 ...)
	};
	p = emit(p, pre, sizeof pre);
	if (variant == 0) {
		if (live_rax) p = emit(p, save_rax, sizeof save_rax);
		p = emit(p, restore, sizeof restore);
		if (live_rax) p = emit(p, reload_rax, sizeof reload_rax);
		if (pop) p = emit(p, pop_image, sizeof pop_image);
	} else {
		p = emit(p, popfq_ref, sizeof popfq_ref);
	}
	static const uint8_t post[] = {
	        0x9C,                         // pushfq
	        0x59,                         // pop rcx
	        0x49, 0x89, 0x09,             // mov [r9], rcx       flags
	        0x49, 0x89, 0x41, 0x08,       // mov [r9+8], rax
	        0x4C, 0x89, 0xC1,             // mov rcx, r8
	        0x48, 0x29, 0xE1,             // sub rcx, rsp        (clobbers flags, already captured)
	        0x49, 0x89, 0x49, 0x10,       // mov [r9+16], rcx    0 if popped, 16 if kept
	        0x4C, 0x89, 0xC4,             // mov rsp, r8
	        0xFC,                         // cld
	        0xC3,
	};
	p = emit(p, post, sizeof post);
	return (harness_fn)buf;
}

int main(void)
{
	uint8_t* buf = mmap(0, 4096, PROT_READ | PROT_WRITE | PROT_EXEC,
	                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (buf == MAP_FAILED) return 2;
	long checks = 0, fails = 0;
	for (int pop = 0; pop <= 1; ++pop) {
		for (int live = 0; live <= 1; ++live) {
			harness_fn seq = build(buf, 0, live, pop);
			harness_fn ref = build(buf + 2048, 1, 0, 1);
			for (uint32_t v = 0; v < 0x1000; ++v) {
				if (v & 0x100) continue; // TF would trap in the POPFQ reference
				const uint64_t rax_in = 0x1122334455667788ull ^ ((uint64_t)v << 40);
				struct result a = {0}, b = {0}, idle = {0};
				seq(v, rax_in, &a);
				ref(v & ~0x400u, rax_in, &b); // reference without DF
				// what the host's non-arithmetic flags look like untouched
				ref(0x202, rax_in, &idle);
				++checks;
				int bad = 0;
				if ((a.flags & ARITH) != (v & ARITH)) bad |= 1;
				if ((a.flags & ARITH) != (b.flags & ARITH)) bad |= 2;
				if ((a.flags & ~(uint64_t)ARITH) != (idle.flags & ~(uint64_t)ARITH)) bad |= 4;
				if (live && a.rax != rax_in) bad |= 8;
				if (a.rsp_delta != (pop ? 0u : 16u)) bad |= 16;
				if (bad) {
					if (++fails <= 10)
						printf("FAIL pop=%d live=%d image=%03x got=%03llx ref=%03llx why=%d\n",
						       pop, live, v, (unsigned long long)(a.flags & 0xfff),
						       (unsigned long long)(b.flags & 0xfff), bad);
				}
			}
		}
	}
	printf("flag_restore_check: %ld images checked, %ld failures\n", checks, fails);
	return fails ? 1 : 0;
}
