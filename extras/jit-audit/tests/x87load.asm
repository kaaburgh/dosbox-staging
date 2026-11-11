; SPDX-License-Identifier: GPL-2.0-or-later
;
; X87LOAD.COM - guest semantics gate for x87 instructions with a 32-bit memory
; source operand: FLD/FADD/FSUB/FSUBR/FMUL/FDIV/FDIVR/FCOM/FCOMP m32real and
; FILD/FIADD/FISUB/FIMUL/FICOM/FICOMP m32int (Intel SDM vol. 2, "FLD", "FADD",
; "FSUB", "FSUBR", "FMUL", "FDIV", "FDIVR", "FCOM/FCOMP/FCOMPP", "FILD",
; "FIADD", "FISUB", "FIMUL", "FICOM/FICOMP").
;
; The operand is placed at page offset 0xFFC (last dword inside a 4 KiB page),
; 0xFFD, 0xFFE, 0xFFF (dword crosses the page boundary) of a page-aligned
; scratch segment (linear 30000h), and in VGA memory A000:xxxx in mode 13h (a
; page served by a handler with no direct pointer). Around every x87
; instruction the test keeps live values in EAX/ECX/EDX/EBX/ESI/EDI/EBP and
; the flags of "cmp esi,edi" (CF=1, ZF=0, SF=1); x87 instructions do not
; modify EFLAGS (SDM vol. 1, 8.1.3), so all of them must be unchanged.
; Further blocks mix in-page and page-crossing loads in one translated block
; and contain 22 loads in one block with dirty registers.
;
; Expected values (single-precision bit patterns, computed from the SDM
; definitions, ST0 = 8.0 initially, memory = 2.0 or integer 3 / 2 as noted):
;   FLD 2.0=40000000  FADD 10.0=41200000  FSUB 8-2=6.0=40C00000
;   FSUBR 2-8=-6.0=C0C00000  FMUL 16.0=41800000  FDIV 8/2=4.0=40800000
;   FDIVR 2/8=0.25=3E800000
;   FCOM/FICOM status C3,C2,C0 (bits 14,10,8) : ST0>m -> 000, ST0=m -> C3,
;   ST0<m -> C0; TOP is 7 after the preceding FLD and 0 after a popping compare.
;   FILD 3.0=40400000 FIADD 11.0=41300000 FISUB 5.0=40A00000 FIMUL 24.0=41C00000
;
; Prints "X87LOAD deviations=<hex>" followed by failing case ids. Id = suite
; base + operation number (0..15); a wrong ST0 / status adds 80h to the id,
; wrong integer registers or flags use the id itself.
; Build: nasm -f bin -o X87LOAD.COM x87load.asm
;        nasm -f bin -DNEG -o X87NEG.COM x87load.asm   (negative control:
;        FADD expectation deliberately wrong, must report id 11 / 91)

bits 16
org 100h

SCRATCH_SEG equ 3000h

%macro PRE 2            ; offset, init ST0 bits
	mov dword [initv], %2
	fninit
	fld dword [initv]
	mov eax, 11111111h
	mov ecx, 22222222h
	mov edx, 33333333h
	mov esi, 55555555h
	mov edi, 66666666h
	mov ebp, 77777777h
	mov ebx, %1
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

%macro VAL 3            ; case id, mode (f|s), expected
%ifidn %2, f
	fstp dword [res]
	mov eax, [res]
	cmp eax, %3
%else
	fnstsw ax
	and ax, 7D00h
	cmp ax, %3
%endif
	je %%ok
	mov al, (%1)|80h
	call fail
%%ok:
%endmacro

; id, mnemonic, offset, init ST0 bits, memory dword, mode, expected
%macro CASE 7
	mov dword [es:%3], %5
	PRE %3, %4
	%2 dword [es:ebx]
	POST %1, %3
	VAL %1, %6, %7
%endmacro

; All 16 operations at one operand offset. Base id in %1, offset in %2.
%macro SUITE 2
	CASE %1+0,  fld,    %2, 41000000h, 40000000h, f, 40000000h
%ifdef NEG
	CASE %1+1,  fadd,   %2, 41000000h, 40000000h, f, 41200001h
%else
	CASE %1+1,  fadd,   %2, 41000000h, 40000000h, f, 41200000h
%endif
	CASE %1+2,  fsub,   %2, 41000000h, 40000000h, f, 40C00000h
	CASE %1+3,  fsubr,  %2, 41000000h, 40000000h, f, 0C0C00000h
	CASE %1+4,  fmul,   %2, 41000000h, 40000000h, f, 41800000h
	CASE %1+5,  fdiv,   %2, 41000000h, 40000000h, f, 40800000h
	CASE %1+6,  fdivr,  %2, 41000000h, 40000000h, f, 3E800000h
	CASE %1+7,  fcom,   %2, 41000000h, 40000000h, s, 3800h        ; ST0>m, TOP=7
	CASE %1+8,  fcomp,  %2, 41000000h, 40000000h, s, 0000h        ; ST0>m, popped
	CASE %1+9,  fild,   %2, 41000000h, 3, f, 40400000h
	CASE %1+10, fiadd,  %2, 41000000h, 3, f, 41300000h
	CASE %1+11, fisub,  %2, 41000000h, 3, f, 40A00000h
	CASE %1+12, fimul,  %2, 41000000h, 3, f, 41C00000h
	CASE %1+13, ficom,  %2, 41000000h, 3, s, 3800h                ; 8>3
	CASE %1+14, fcom,   %2, 41000000h, 41000000h, s, 7800h        ; 8=8 -> C3
	CASE %1+15, ficomp, %2, 41000000h, 9, s, 0100h                ; 8<9 -> C0, popped
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
	; also a plain in-page offset
	SUITE 50h, 0100h

; ---- mixed block: fast and slow loads in one translated block --------------
	; memory: 2.0@100h(fast) 3.0@FFFh(cross) 5.0@FF8h(fast) int 7@1FFEh(cross)
	;         int 2@104h(fast)
	mov dword [es:0100h], 40000000h
	mov dword [es:0FFFh], 40400000h
	mov dword [es:0FF8h], 40A00000h
	mov dword [es:1FFEh], 7
	mov dword [es:0104h], 2
	fninit
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
	; 2 -> +3 =5 -> *5 =25 -> +7 =32 -> -2 =30 -> /2 =15  (FLD,FADD,FMUL,FIADD,FISUB,FDIV)
	fld dword [es:edi-66666666h+0100h]
	fadd dword [es:edi-66666666h+0FFFh]
	fmul dword [es:edi-66666666h+0FF8h]
	fiadd dword [es:edi-66666666h+1FFEh]
	fisub dword [es:edi-66666666h+0104h]
	fdiv dword [es:edi-66666666h+0100h]
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
	jmp short mixok
mixbad:
	mov al, 60h
	call fail
mixok:
	VAL 60h, f, 41700000h   ; 15.0

; ---- 22 loads in one block, registers dirtied inside the block --------------
	mov dword [es:0100h], 3F800000h        ; 1.0 (fast)
	mov dword [es:0FFDh], 3F800000h        ; 1.0 (crossing)
	fninit
	fldz
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
%rep 11
	fadd dword [es:ebx+0100h]
	fadd dword [es:ebx+0FFDh]
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
	jmp short bigok
bigbad:
	mov al, 61h
	call fail
bigok:
	VAL 61h, f, 41B00000h   ; 22.0

; ---- VGA memory (handler page, no direct pointer), mode 13h ----------------
	mov ax, 13h
	int 10h
	mov ax, 0A000h
	mov es, ax
	SUITE 70h, 0010h
	; second pass over the same page also covers the cached TLB handler
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

msg db 'X87LOAD deviations=$'
align 4
initv dd 0
res dd 0
fails db 0
faillist times 32 db 0
