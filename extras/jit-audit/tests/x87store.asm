; SPDX-License-Identifier: GPL-2.0-or-later
;
; X87STORE.COM - guest semantics gate for x87 instructions with a 32-bit memory
; destination: FST/FSTP m32real and FIST/FISTP m32int (Intel SDM vol. 2,
; "FST/FSTP", "FIST/FISTP"; vol. 1, 4.8.3.7 "Integer indefinite", 4.8.4
; "Rounding").
;
; The operand is placed at page offset 0xFFC (last dword inside a 4 KiB page),
; 0xFFD, 0xFFE, 0xFFF (dword crosses the page boundary) and 0x100 of a
; page-aligned scratch segment (linear 30000h), and in VGA memory A000:xxxx in
; mode 13h (a page served by a handler with no direct pointer). Around every
; store the test keeps live values in EAX/ECX/EDX/EBX/ESI/EDI/EBP and the flags
; of "cmp esi,edi" (CF=1, ZF=0, SF=1); x87 instructions do not modify EFLAGS
; (SDM vol. 1, 8.1.3), so all of them must be unchanged. The bytes just before
; and after the destination hold a canary (A5h) that must survive.
;
; Expected values (FNINIT: all exceptions masked, round to nearest even):
;   FST  8.0      -> 41000000, ST0 kept, TOP=7   (status & 3801h = 3800h)
;   FSTP 8.0      -> 41000000, popped,   TOP=0   (status & 3801h = 0000h)
;   FST  -0.25    -> BE800000
;   FST  1/3      -> 3EAAAAAB (rounded to single precision)
;   FIST 8.0      -> 00000008
;   FIST -2.5     -> FFFFFFFE (-2, nearest even)
;   FISTP 3.5     -> 00000004, popped
;   FISTP 3.0e9   -> 80000000 (integer indefinite, IE set), popped
;
; Further blocks: fast and slow stores mixed in one translated block, 22
; stores in one block with dirty registers, and a FISTP into the immediate of
; an instruction that executes afterwards (self-modifying code: the store
; goes through the code page handler and must invalidate the translation).
;
; Prints "X87STORE deviations=<hex>" followed by failing case ids. Id = suite
; base + operation number (0..7); a wrong memory value adds 80h, a damaged
; canary adds 40h, a wrong status adds C0h; wrong registers or flags use the
; id itself.
; Build: nasm -f bin -o X87STORE.COM x87store.asm
;        nasm -f bin -DNEG -o X87SNEG.COM x87store.asm   (negative control:
;        FIST -2.5 expectation deliberately wrong, must report 94 A4 B4 C4
;        D4 F4)

bits 16
org 100h

SCRATCH_SEG equ 3000h

%macro PRE 1            ; offset
	mov ebx, %1
	mov byte [es:ebx-1], 0A5h
	mov byte [es:ebx+4], 0A5h
	mov dword [es:ebx], 0
	mov eax, 11111111h
	mov ecx, 22222222h
	mov edx, 33333333h
	mov esi, 55555555h
	mov edi, 66666666h
	mov ebp, 77777777h
	cmp esi, edi            ; CF=1 ZF=0 SF=1
%endmacro

%macro POST 2           ; case id, offset
	jae %%bad
	je %%bad
	jns %%bad
	cmp eax, 11111111h
	jne %%bad
	cmp ecx, 22222222h
	jne %%bad
	cmp edx, 33333333h
	jne %%bad
	cmp ebx, %2
	jne %%bad
	cmp esi, 55555555h
	jne %%bad
	cmp edi, 66666666h
	jne %%bad
	cmp ebp, 77777777h
	jne %%bad
	jmp short %%ok
%%bad:
	mov al, %1
	call fail
%%ok:
%endmacro

%macro CHECK 4          ; case id, offset, expected memory, expected status & 3801h
	cmp dword [es:%2], %3
	je %%m
	mov al, (%1)|80h
	call fail
%%m:
	cmp byte [es:%2-1], 0A5h
	jne %%c
	cmp byte [es:%2+4], 0A5h
	je %%k
%%c:
	mov al, (%1)|40h
	call fail
%%k:
	fnstsw ax
	and ax, 3801h
	cmp ax, %4
	je %%s
	mov al, (%1)|0C0h
	call fail
%%s:
%endmacro

; id, mnemonic, offset, ST0 source (dword real), expected memory, expected status
%macro CASE 6
	fninit
	fld dword [%4]
	PRE %3
	%2 dword [es:ebx]
	POST %1, %3
	CHECK %1, %3, %5, %6
%endmacro

; All operations at one operand offset. Base id in %1, offset in %2.
%macro SUITE 2
	CASE %1+0, fst,   %2, v8,    41000000h, 3800h
	CASE %1+1, fstp,  %2, v8,    41000000h, 0000h
	CASE %1+2, fst,   %2, vm025, 0BE800000h, 3800h
	CASE %1+3, fist,  %2, v8,    00000008h, 3800h
%ifdef NEG
	CASE %1+4, fist,  %2, vm25,  0FFFFFFFDh, 3800h
%else
	CASE %1+4, fist,  %2, vm25,  0FFFFFFFEh, 3800h
%endif
	CASE %1+5, fistp, %2, v35,   00000004h, 0000h
	CASE %1+6, fistp, %2, v3e9,  80000000h, 0001h
	; 1/3 computed in extended precision, then rounded by FST
	fninit
	fld1
	fld dword [v3]
	fdivp st1, st0
	PRE %2
	fst dword [es:ebx]
	POST %1+7, %2
	CHECK %1+7, %2, 3EAAAAABh, 3800h
%endmacro

start:
	push cs
	pop ds
	mov ax, SCRATCH_SEG
	mov es, ax

	SUITE 10h, 0FFCh        ; last dword inside the page
	SUITE 20h, 0FFDh        ; crossing
	SUITE 30h, 0FFEh
	SUITE 40h, 0FFFh
	SUITE 50h, 0100h        ; plain in-page offset

; ---- mixed block: fast and slow stores in one translated block --------------
	mov dword [es:0100h], 0
	mov dword [es:0FFEh], 0
	mov dword [es:0FF8h], 0
	mov dword [es:1FFDh], 0
	fninit
	fld dword [v8]          ; ST0 = 8.0
	mov eax, 11111111h
	mov ecx, 22222222h
	mov edx, 33333333h
	mov esi, 55555555h
	mov edi, 66666666h
	mov ebp, 77777777h
	mov ebx, 88888888h
	cmp esi, edi
	jmp near mixblk
align 16
mixblk:
	fst dword [es:edi-66666666h+0100h]      ; fast
	fist dword [es:edi-66666666h+0FFEh]     ; crossing
	fst dword [es:edi-66666666h+0FF8h]      ; fast
	fistp dword [es:edi-66666666h+1FFDh]    ; crossing, pops
	jmp near mixend
mixend:
	jae mixbad
	je mixbad
	jns mixbad
	cmp eax, 11111111h
	jne mixbad
	cmp ecx, 22222222h
	jne mixbad
	cmp edx, 33333333h
	jne mixbad
	cmp ebx, 88888888h
	jne mixbad
	cmp esi, 55555555h
	jne mixbad
	cmp edi, 66666666h
	jne mixbad
	cmp ebp, 77777777h
	jne mixbad
	cmp dword [es:0100h], 41000000h
	jne mixbad
	cmp dword [es:0FFEh], 8
	jne mixbad
	cmp dword [es:0FF8h], 41000000h
	jne mixbad
	cmp dword [es:1FFDh], 8
	jne mixbad
	fnstsw ax
	and ax, 3801h
	jz mixok                ; popped: TOP=0
mixbad:
	mov al, 60h
	call fail
mixok:

; ---- 22 stores in one block, registers dirtied inside the block -------------
	fninit
	fld dword [v8]
	jmp near bigblk
align 16
bigblk:
	mov eax, 11111111h
	mov ecx, 22222222h
	mov edx, 33333333h
	mov esi, 55555555h
	mov edi, 66666666h
	mov ebp, 77777777h
	mov ebx, 0
	cmp esi, edi
%assign i 0
%rep 11
	fst dword [es:ebx+0200h+i*4]
	fist dword [es:ebx+1FFDh-i*8]
%assign i i+1
%endrep
	jmp near bigend
bigend:
	jae bigbad
	je bigbad
	jns bigbad
	cmp eax, 11111111h
	jne bigbad
	cmp ecx, 22222222h
	jne bigbad
	cmp edx, 33333333h
	jne bigbad
	test ebx, ebx
	jne bigbad
	cmp esi, 55555555h
	jne bigbad
	cmp edi, 66666666h
	jne bigbad
	cmp ebp, 77777777h
	jne bigbad
%assign i 0
%rep 11
	cmp dword [es:0200h+i*4], 41000000h
	jne bigbad
	cmp dword [es:1FFDh-i*8], 8
	jne bigbad
%assign i i+1
%endrep
	jmp short bigok
bigbad:
	mov al, 61h
	call fail
bigok:

; ---- self-modifying code: FISTP into a later instruction's immediate --------
	fninit
	fild dword [smcval]     ; 12345678h
	mov eax, 0
	jmp near smcblk
align 16
smcblk:
	fistp dword [patch+2]   ; imm32 of "mov eax, imm32" below (66 B8 imm32)
	jmp near patch
patch:
	mov eax, 0DEADBEEFh
	cmp eax, 12345678h
	je smcok
	mov al, 62h
	call fail
smcok:

; ---- VGA memory (handler page, no direct pointer), mode 13h ----------------
	mov ax, 13h
	int 10h
	mov ax, 0A000h
	mov es, ax
	SUITE 70h, 0010h
	mov ax, 3
	int 10h
	mov ax, SCRATCH_SEG
	mov es, ax

; ---- report -----------------------------------------------------------------
	mov dx, msg
	mov ah, 9
	int 21h
	mov al, [fails]
	call hex8
	mov si, faillist
	mov cl, [fails]
.lst:
	test cl, cl
	jz .done
	mov dl, ' '
	mov ah, 2
	int 21h
	lodsb
	call hex8
	dec cl
	jmp .lst
.done:
	mov dl, 13
	mov ah, 2
	int 21h
	mov dl, 10
	int 21h
	mov ax, 4c00h
	int 21h

fail:                      ; AL = case id
	push bx
	movzx bx, byte [fails]
	cmp bx, 32
	jae .full
	mov [faillist+bx], al
	inc byte [fails]
.full:
	pop bx
	ret

hex8:
	push ax
	shr al, 4
	call .nib
	pop ax
	and al, 0Fh
.nib:
	add al, '0'
	cmp al, '9'
	jbe .out
	add al, 7
.out:
	mov dl, al
	mov ah, 2
	int 21h
	ret

msg db 'X87STORE deviations=$'
align 4
v8     dd 41000000h        ; 8.0
vm025  dd 0BE800000h       ; -0.25
vm25   dd 0C0200000h       ; -2.5
v35    dd 40600000h        ; 3.5
v3e9   dd 4F32D05Eh        ; 3.0e9
v3     dd 40400000h        ; 3.0
smcval dd 12345678h
fails db 0
faillist times 32 db 0
