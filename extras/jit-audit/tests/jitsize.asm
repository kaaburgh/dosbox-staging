; SPDX-License-Identifier: GPL-2.0-or-later
;
; JITSIZE.COM - one guest block made of the maximum number of decoded
; instructions (the dynamic core stops a block after 32 opcodes, plus one)
; that are all x87 loads with a memory operand. Each load uses a 32-bit SIB
; address with a segment override so the generated address calculation is
; as long as possible, and integer registers are touched in between so the
; register allocator has dirty state to spill on the slow paths.
;
; Prints "JITSIZE_PASS" when the sum is correct. A dynamic-core build whose
; per-instruction code grew too much aborts with "CacheBlock overrun".
; Build: nasm -f bin -o JITSIZE.COM jitsize.asm
;        nasm -f bin -DDIRTY -o JITSIZED.COM jitsize.asm (dirty-register variant)

bits 16
org 100h
	push cs
	pop es
	xor ebx, ebx
	xor esi, esi
	xor edi, edi
	mov ecx, 1
	fninit
	fldz
	jmp near blk
align 16
blk:
%ifdef DIRTY
	; dirty every guest GPR except ESP so slow paths/exception stubs must spill
	inc eax
	inc ecx
	inc edx
	add ebx, 0
	inc ebp
	add esi, 0
	add edi, 0
%rep 26
	fadd dword [es:dword ebx+esi*4+one]
%endrep
%else
%rep 33
	fadd dword [es:dword ebx+esi*4+one]
%endrep
%endif
	jmp near after
after:
	fstp dword [result]
%ifdef DIRTY
	cmp dword [result], 41d00000h      ; 26.0f
%else
	cmp dword [result], 42040000h      ; 33.0f
%endif
	jne fail
	mov dx, pass
	mov ah, 9
	int 21h
	mov ax, 4c00h
	int 21h
fail:
	mov dx, failmsg
	mov ah, 9
	int 21h
	mov ax, 4c01h
	int 21h
align 4
one dd 3f800000h
result dd 0
pass db 'JITSIZE_PASS', 13, 10, '$'
failmsg db 'JITSIZE_FAIL', 13, 10, '$'
