; SPDX-License-Identifier: GPL-2.0-or-later
;
; FLAGOPS.COM - exhaustive guest-flag gate for BT/BTS/BTR/BTC, SETcc, SAHF and
; LAHF (the instructions the dynamic core translates natively instead of
; handing them to the normal core).
;
; Expected values come from the Intel SDM plus the documented normal-core
; rule "BTx changes only CF" (OF/SF/ZF/AF/PF keep their old values):
;  - BT/BTS/BTR/BTC r,r / r,imm8: CF := selected bit (index mod 32 / mod 16),
;    BTS sets, BTR clears, BTC toggles that bit in the destination; the index
;    register and all other registers are unchanged.
;  - SETcc r/m8: byte := 1 if the condition holds else 0; no flag changes.
;      cc: 0 O  1 NO  2 B  3 AE  4 E  5 NE  6 BE  7 A
;          8 S  9 NS  A P  B NP  C L  D GE  E LE  F G
;  - SAHF: SF,ZF,AF,PF,CF := AH bits 7,6,4,2,0; OF unchanged; EAX unchanged.
;  - LAHF: AH := SF:ZF:0:AF:0:PF:1:CF; flags and the rest of EAX unchanged.
;
; Every case runs over all 64 combinations of the six arithmetic flags
; (CF=1 PF=4 AF=10h ZF=40h SF=80h OF=800h) with distinct canary values in all
; GPRs, which are all verified afterwards. Flags are produced by
; "push imm; popf" (also by "add al,al; sahf" in some instances) and read by
; "pushf; pop" (also by a JO/JS/JZ/JP/JC chain in some instances). The
; instances differ in the memory accesses between flag producer, tested
; instruction and consumer (none / store / load / push+pop of memory), so both
; "flags live in the host" and "flags saved on the host stack" paths run.
;
; Prints "FLAGOPS deviations=<hex word> iters=<hex dword>", then the failing
; case ids (max 16) and details of the first failure. Pass = deviations=0000.
; Build: nasm -f bin -o FLAGOPS.COM flagops.asm
;        (-DNEG: deliberately wrong BTS expectation, must report deviations)

bits 16
org 100h

FLM     equ 08D5h               ; CF|PF|AF|ZF|SF|OF
NVAL    equ 4
NIDX    equ 10

%define H_eax ax
%define H_ecx cx
%define H_edx dx
%define H_ebx bx
%define H_ebp bp
%define H_esi si
%define H_edi di

can_eax equ can_tab+0
can_ecx equ can_tab+4
can_edx equ can_tab+8
can_ebx equ can_tab+12
can_ebp equ can_tab+20
can_esi equ can_tab+24
can_edi equ can_tab+28

%macro LOADREGS 0
	call loadregs
%endmacro

%macro SAVEREGS 0
	mov [res_tab+16], esp
	call saveregs
%endmacro

; gap between flag producer / tested instruction / consumer
; 0 none, 1 store, 2 load, 3 load+store through the stack
%macro GAP 2                    ; type, register that is neither dest nor index
	%if %1==1
		mov [gapscr], %2
	%elif %1==2
		mov %2, [can_ %+ %2]
	%elif %1==3
		push word [gapscr]
		pop word [gapscr]
	%endif
%endmacro

; GAPR := a register that is neither %1 nor %2
%macro PICKGAP 2
	%undef GAPR
	%ifnidn %1,eax
	%ifnidn %2,eax
		%define GAPR eax
	%endif
	%endif
	%ifndef GAPR
	%ifnidn %1,ecx
	%ifnidn %2,ecx
		%define GAPR ecx
	%endif
	%endif
	%endif
	%ifndef GAPR
		%define GAPR edx
	%endif
%endmacro

; read the flags after the tested instruction
%macro RDFLAGS 1
	%if %1==1
		jnc %%a
		mov byte [rd_c], 1
%%a:		jno %%b
		mov byte [rd_o], 1
%%b:		jns %%c
		mov byte [rd_s], 1
%%c:		jnz %%d
		mov byte [rd_z], 1
%%d:		jnp %%e
		mov byte [rd_p], 1
%%e:
	%endif
	pushf
	pop word [resfl]
%endmacro

; clear the Jcc chain result; optionally produce the flags with
; "add al,al; sahf" (setmode 1); setmode 0 produces them just before the test
%macro PRELUDE 2                ; rd, setmode
	mov dword [rd_c], 0
	mov dword [rd_c+4], 0
	%if %2==1
		mov ax, [sahfax]
		add al, al              ; OF := 7Fh+7Fh overflow / 0
		sahf                    ; other five flags from AH, OF kept
	%endif
%endmacro

%macro FLAGSET 1                ; setmode
	%if %1==0
		push word [inflags]
		popf
	%endif
%endmacro

%macro FLAGLOOP_BEGIN 0
	%push fl
	mov [can_tab+16], esp
	mov word [fi], 0
%$floop:
	mov bx, [fi]
	shl bx, 1
	mov ax, [flagtab+bx]
	mov [inflags], ax
	mov ax, [sahftab+bx]
	mov [sahfax], ax
%endmacro

%macro FLAGLOOP_END 0
	inc word [fi]
	cmp word [fi], 64
	jb %$floop
	%pop
%endmacro

; ------------------------------------------------------------------------
; BT family: id, ins, opnum, form(0 reg/1 imm8), size, d32, s32, dnum, snum,
;            gapb, gapa, rd, setmode, imm
%macro BTCASE 14
	mov word [curid], %1
	mov byte [curop], %3
	mov byte [cursz], %5
	mov byte [dsel], %8
	mov byte [ssel], %9
	mov byte [rdmode], %12
	PICKGAP %6, %7
	%if %5==16
		%xdefine DUSE H_ %+ %6
		%xdefine SUSE H_ %+ %7
	%else
		%xdefine DUSE %6
		%xdefine SUSE %7
	%endif
	FLAGLOOP_BEGIN
	mov word [vi], 0
%%vloop:
	mov bx, [vi]
	shl bx, 2
	mov eax, [valtab+bx]
	mov [curv], eax
	mov word [xi], 0
%%xloop:
	%if %4==0
		mov bx, [xi]
		shl bx, 2
		mov eax, [idxtab+bx]
		mov [curidx], eax
	%else
		mov dword [curidx], %14
	%endif
	PRELUDE %12, %13
	LOADREGS
	mov %6, [curv]
	%if %4==0 && %8!=%9
		mov %7, [curidx]
	%endif
	FLAGSET %13
	GAP %10, GAPR
	%if %4==0
		%2 DUSE, SUSE
	%else
		%2 DUSE, %14
	%endif
	GAP %11, GAPR
	RDFLAGS %12
	SAVEREGS
	call chk_bt
	%if %4==0
		inc word [xi]
		cmp word [xi], NIDX
		jb %%xloop
	%endif
	inc word [vi]
	cmp word [vi], NVAL
	jb %%vloop
	FLAGLOOP_END
%endmacro

; SETcc to a byte register: id, cc, setins, r32, regnum, shift(0/8), gapb,
;                           gapa, rd, setmode   (setins is e.g. "sete al")
%macro SETREG 10
	mov word [curid], %1
	mov byte [curcc], %2
	mov byte [dsel], %5
	mov byte [sshift], %6
	mov byte [rdmode], %9
	PICKGAP %4, %4
	FLAGLOOP_BEGIN
	PRELUDE %9, %10
	LOADREGS
	FLAGSET %10
	GAP %7, GAPR
	%3
	GAP %8, GAPR
	RDFLAGS %9
	SAVEREGS
	call chk_setreg
	FLAGLOOP_END
%endmacro

; SETcc to memory: id, cc, mode (0 data byte, 1 A000h:1234h (mode 13h),
;                  2 last byte of a 4K page), gapb, gapa, rd, setmode, setins
%macro SETMEM 8
	mov word [curid], %1
	mov byte [curcc], %2
	mov byte [rdmode], %6
	%if %3==0
		mov word [memoff], mbuf+1
	%elif %3==1
		mov word [memoff], 1234h
	%else
		mov ax, [pgoff]
		mov [memoff], ax
	%endif
	FLAGLOOP_BEGIN
	PRELUDE %6, %7
	%if %3==1
		mov ax, 0A000h
		mov es, ax
	%endif
	mov di, [memoff]
	mov byte es:[di-1], 11h
	mov byte es:[di], 0A5h
	mov byte es:[di+1], 33h
	mov [can_tab+28], di
	LOADREGS
	FLAGSET %7
	GAP %4, ebx
	%8
	GAP %5, ebx
	RDFLAGS %6
	SAVEREGS
	mov bx, [memoff]
	mov al, es:[bx-1]
	mov [memb+0], al
	mov al, es:[bx]
	mov [memb+1], al
	mov al, es:[bx+1]
	mov [memb+2], al
	mov bx, ds
	mov es, bx
	call chk_setmem
	FLAGLOOP_END
%endmacro

; SAHF: id, gapb, gapa, rd
%macro SAHFCASE 4
	mov word [curid], %1
	mov byte [rdmode], %4
	mov [can_tab+16], esp
	mov word [ahv], 0
%%aloop:
	mov word [pre], 0
%%ploop:
	; inflags := 202h | (pre&1 ? OF : 0) | (pre&2 ? CF|PF|AF|ZF|SF : 0)
	mov ax, 202h
	test word [pre], 1
	jz %%n1
	or ax, 800h
%%n1:	test word [pre], 2
	jz %%n2
	or ax, 0D5h
%%n2:	mov [inflags], ax
	movzx eax, byte [ahv]
	shl eax, 8
	or eax, 0A5C300C3h
	mov [eaxin], eax
	PRELUDE %4, 0
	LOADREGS
	mov eax, [eaxin]
	FLAGSET 0
	GAP %2, ebx
	sahf
	GAP %3, ebx
	RDFLAGS %4
	SAVEREGS
	call chk_sahf
	inc word [pre]
	cmp word [pre], 4
	jb %%ploop
	inc word [ahv]
	cmp word [ahv], 256
	jb %%aloop
%endmacro

; LAHF: id, gapb, gapa, rd, setmode
%macro LAHFCASE 5
	mov word [curid], %1
	mov byte [rdmode], %4
	FLAGLOOP_BEGIN
	mov word [vi], 0
%%vloop:
	mov bx, [vi]
	shl bx, 2
	mov eax, [eaxtab+bx]
	mov [eaxin], eax
	PRELUDE %4, %5
	LOADREGS
	mov eax, [eaxin]
	FLAGSET %5
	GAP %2, ebx
	lahf
	GAP %3, ebx
	RDFLAGS %4
	SAVEREGS
	call chk_lahf
	inc word [vi]
	cmp word [vi], 3
	jb %%vloop
	FLAGLOOP_END
%endmacro

; ------------------------------------------------------------------------
	jmp start

loadregs:
	mov eax, [can_tab+0]
	mov ecx, [can_tab+4]
	mov edx, [can_tab+8]
	mov ebx, [can_tab+12]
	mov ebp, [can_tab+20]
	mov esi, [can_tab+24]
	mov edi, [can_tab+28]
	ret

saveregs:
	mov [res_tab+0], eax
	mov [res_tab+4], ecx
	mov [res_tab+8], edx
	mov [res_tab+12], ebx
	mov [res_tab+20], ebp
	mov [res_tab+24], esi
	mov [res_tab+28], edi
	ret

start:
	push cs
	pop ds
	push cs
	pop es
	cld
	mov [can_tab+16], esp
	; page-end byte: linear address of (cs:pgoff) has (addr & 0FFFh) == 0FFFh
	mov ax, cs
	shl ax, 4
	mov bx, 0FFFh
	sub bx, ax
	and bx, 0FFFh
.pg:	cmp bx, 0EC00h          ; (never) / beyond the image: add 4K steps
	jae .pgd
	cmp bx, pgend
	ja .pgd
	add bx, 1000h
	jmp .pg
.pgd:	mov [pgoff], bx

	call phase_prim
	call phase_bt
	call phase_setreg
	call phase_setmem
	call phase_sahf
	call phase_lahf
	call phase_x87
	jmp report

; ------------------------------------------------------------------------
; Primitives cross-check: two ways of setting, three ways of reading flags
phase_prim:
	FLAGLOOP_BEGIN
	mov bx, [inflags]
	and bx, FLM
	mov [expfl], bx
	; A: popf -> pushf
	push word [inflags]
	popf
	pushf
	pop word [resfl]
	mov ax, [resfl]
	and ax, FLM
	cmp ax, [expfl]
	je .a_ok
	mov word [curid], 1
	call note_fail
.a_ok:
	; B: add al,al; sahf -> pushf
	mov ax, [sahfax]
	add al, al
	sahf
	pushf
	pop word [resfl]
	mov ax, [resfl]
	and ax, FLM
	cmp ax, [expfl]
	je .b_ok
	mov word [curid], 2
	call note_fail
.b_ok:
	; C: popf -> Jcc chain
	mov dword [rd_c], 0
	mov dword [rd_c+4], 0
	push word [inflags]
	popf
	RDFLAGS 1
	call chk_rdchain
	jnc .c_ok
	mov word [curid], 3
	call note_fail
.c_ok:
	; D: popf -> lahf + seto
	push word [inflags]
	popf
	lahf
	seto bl
	mov bh, ah
	mov [resfl], bx
	mov ax, [inflags]
	and al, 0D5h
	or al, 2
	mov bx, [resfl]
	cmp bh, al
	je .d1
	mov word [curid], 4
	call note_fail
.d1:	mov ax, [inflags]
	shr ax, 11
	and al, 1
	cmp bl, al
	je .d2
	mov word [curid], 5
	call note_fail
.d2:
	inc dword [iters]
	FLAGLOOP_END
	ret

; check the Jcc chain bytes against expfl
chk_rdchain:
	mov ax, [expfl]
	mov bl, al
	and bl, 1
	cmp bl, [rd_c]
	jne .bad
	mov bl, ah
	shr bl, 3
	and bl, 1
	cmp bl, [rd_o]
	jne .bad
	mov bl, al
	shr bl, 7
	cmp bl, [rd_s]
	jne .bad
	mov bl, al
	shr bl, 6
	and bl, 1
	cmp bl, [rd_z]
	jne .bad
	mov bl, al
	shr bl, 2
	and bl, 1
	cmp bl, [rd_p]
	jne .bad
	clc
	ret
.bad:
	stc
	ret

phase_bt:
	BTCASE 4096, bt, 0, 0, 32, eax, edx, 0, 2, 0, 0, 0, 0, 0
	BTCASE 4097, bt, 0, 0, 32, ebx, eax, 3, 0, 1, 0, 0, 0, 0
	BTCASE 4098, bt, 0, 0, 32, edi, esi, 7, 6, 2, 0, 0, 0, 0
	BTCASE 4099, bt, 0, 1, 32, edx, edx, 2, 255, 3, 0, 0, 0, 0
	BTCASE 4100, bt, 0, 1, 32, esi, esi, 6, 255, 0, 1, 0, 0, 1
	BTCASE 4101, bt, 0, 1, 32, ecx, ecx, 1, 255, 1, 1, 0, 0, 15
	BTCASE 4102, bt, 0, 1, 32, ebp, ebp, 5, 255, 2, 1, 0, 0, 16
	BTCASE 4103, bt, 0, 1, 32, eax, eax, 0, 255, 3, 1, 0, 0, 31
	BTCASE 4104, bt, 0, 1, 32, ebx, ebx, 3, 255, 0, 2, 0, 0, 33
	BTCASE 4105, bt, 0, 1, 32, edi, edi, 7, 255, 1, 2, 0, 0, 47
	BTCASE 4106, bt, 0, 0, 16, edx, ebx, 2, 3, 2, 2, 0, 0, 0
	BTCASE 4107, bt, 0, 0, 16, esi, ecx, 6, 1, 3, 2, 0, 0, 0
	BTCASE 4108, bt, 0, 0, 16, ecx, edi, 1, 7, 0, 3, 0, 0, 0
	BTCASE 4109, bt, 0, 1, 16, ebp, ebp, 5, 255, 1, 3, 0, 0, 0
	BTCASE 4110, bt, 0, 1, 16, eax, eax, 0, 255, 2, 3, 0, 0, 1
	BTCASE 4111, bt, 0, 1, 16, ebx, ebx, 3, 255, 3, 3, 0, 0, 15
	BTCASE 4112, bt, 0, 1, 16, edi, edi, 7, 255, 0, 0, 1, 0, 16
	BTCASE 4113, bt, 0, 1, 16, edx, edx, 2, 255, 1, 0, 1, 0, 31
	BTCASE 4114, bt, 0, 1, 16, esi, esi, 6, 255, 2, 0, 1, 0, 33
	BTCASE 4115, bt, 0, 1, 16, ecx, ecx, 1, 255, 3, 0, 1, 0, 47
	BTCASE 4116, bts, 1, 0, 32, ebp, ebp, 5, 5, 0, 1, 1, 0, 0
	BTCASE 4117, bts, 1, 0, 32, eax, edx, 0, 2, 1, 1, 1, 0, 0
	BTCASE 4118, bts, 1, 0, 32, ebx, eax, 3, 0, 2, 1, 1, 0, 0
	BTCASE 4119, bts, 1, 1, 32, edi, edi, 7, 255, 3, 1, 1, 0, 0
	BTCASE 4120, bts, 1, 1, 32, edx, edx, 2, 255, 0, 2, 1, 0, 1
	BTCASE 4121, bts, 1, 1, 32, esi, esi, 6, 255, 1, 2, 1, 0, 15
	BTCASE 4122, bts, 1, 1, 32, ecx, ecx, 1, 255, 2, 2, 1, 0, 16
	BTCASE 4123, bts, 1, 1, 32, ebp, ebp, 5, 255, 3, 2, 1, 0, 31
	BTCASE 4124, bts, 1, 1, 32, eax, eax, 0, 255, 0, 3, 1, 0, 33
	BTCASE 4125, bts, 1, 1, 32, ebx, ebx, 3, 255, 1, 3, 1, 0, 47
	BTCASE 4126, bts, 1, 0, 16, edi, esi, 7, 6, 2, 3, 1, 0, 0
	BTCASE 4127, bts, 1, 0, 16, edx, edx, 2, 2, 3, 3, 1, 0, 0
	BTCASE 4128, bts, 1, 0, 16, esi, ecx, 6, 1, 0, 0, 0, 1, 0
	BTCASE 4129, bts, 1, 1, 16, ecx, ecx, 1, 255, 1, 0, 0, 1, 0
	BTCASE 4130, bts, 1, 1, 16, ebp, ebp, 5, 255, 2, 0, 0, 1, 1
	BTCASE 4131, bts, 1, 1, 16, eax, eax, 0, 255, 3, 0, 0, 1, 15
	BTCASE 4132, bts, 1, 1, 16, ebx, ebx, 3, 255, 0, 1, 0, 1, 16
	BTCASE 4133, bts, 1, 1, 16, edi, edi, 7, 255, 1, 1, 0, 1, 31
	BTCASE 4134, bts, 1, 1, 16, edx, edx, 2, 255, 2, 1, 0, 1, 33
	BTCASE 4135, bts, 1, 1, 16, esi, esi, 6, 255, 3, 1, 0, 1, 47
	BTCASE 4136, btr, 2, 0, 32, ecx, edi, 1, 7, 0, 2, 0, 1, 0
	BTCASE 4137, btr, 2, 0, 32, ebp, ebp, 5, 5, 1, 2, 0, 1, 0
	BTCASE 4138, btr, 2, 0, 32, eax, edx, 0, 2, 2, 2, 0, 1, 0
	BTCASE 4139, btr, 2, 1, 32, ebx, ebx, 3, 255, 3, 2, 0, 1, 0
	BTCASE 4140, btr, 2, 1, 32, edi, edi, 7, 255, 0, 3, 0, 1, 1
	BTCASE 4141, btr, 2, 1, 32, edx, edx, 2, 255, 1, 3, 0, 1, 15
	BTCASE 4142, btr, 2, 1, 32, esi, esi, 6, 255, 2, 3, 0, 1, 16
	BTCASE 4143, btr, 2, 1, 32, ecx, ecx, 1, 255, 3, 3, 0, 1, 31
	BTCASE 4144, btr, 2, 1, 32, ebp, ebp, 5, 255, 0, 0, 1, 1, 33
	BTCASE 4145, btr, 2, 1, 32, eax, eax, 0, 255, 1, 0, 1, 1, 47
	BTCASE 4146, btr, 2, 0, 16, ebx, eax, 3, 0, 2, 0, 1, 1, 0
	BTCASE 4147, btr, 2, 0, 16, edi, esi, 7, 6, 3, 0, 1, 1, 0
	BTCASE 4148, btr, 2, 0, 16, edx, ebx, 2, 3, 0, 1, 1, 1, 0
	BTCASE 4149, btr, 2, 1, 16, esi, esi, 6, 255, 1, 1, 1, 1, 0
	BTCASE 4150, btr, 2, 1, 16, ecx, ecx, 1, 255, 2, 1, 1, 1, 1
	BTCASE 4151, btr, 2, 1, 16, ebp, ebp, 5, 255, 3, 1, 1, 1, 15
	BTCASE 4152, btr, 2, 1, 16, eax, eax, 0, 255, 0, 2, 1, 1, 16
	BTCASE 4153, btr, 2, 1, 16, ebx, ebx, 3, 255, 1, 2, 1, 1, 31
	BTCASE 4154, btr, 2, 1, 16, edi, edi, 7, 255, 2, 2, 1, 1, 33
	BTCASE 4155, btr, 2, 1, 16, edx, edx, 2, 255, 3, 2, 1, 1, 47
	BTCASE 4156, btc, 3, 0, 32, esi, ecx, 6, 1, 0, 3, 1, 1, 0
	BTCASE 4157, btc, 3, 0, 32, ecx, edi, 1, 7, 1, 3, 1, 1, 0
	BTCASE 4158, btc, 3, 0, 32, ebp, ebp, 5, 5, 2, 3, 1, 1, 0
	BTCASE 4159, btc, 3, 1, 32, eax, eax, 0, 255, 3, 3, 1, 1, 0
	BTCASE 4160, btc, 3, 1, 32, ebx, ebx, 3, 255, 0, 0, 0, 0, 1
	BTCASE 4161, btc, 3, 1, 32, edi, edi, 7, 255, 1, 0, 0, 0, 15
	BTCASE 4162, btc, 3, 1, 32, edx, edx, 2, 255, 2, 0, 0, 0, 16
	BTCASE 4163, btc, 3, 1, 32, esi, esi, 6, 255, 3, 0, 0, 0, 31
	BTCASE 4164, btc, 3, 1, 32, ecx, ecx, 1, 255, 0, 1, 0, 0, 33
	BTCASE 4165, btc, 3, 1, 32, ebp, ebp, 5, 255, 1, 1, 0, 0, 47
	BTCASE 4166, btc, 3, 0, 16, eax, edx, 0, 2, 2, 1, 0, 0, 0
	BTCASE 4167, btc, 3, 0, 16, ebx, ebx, 3, 3, 3, 1, 0, 0, 0
	BTCASE 4168, btc, 3, 0, 16, edi, esi, 7, 6, 0, 2, 0, 0, 0
	BTCASE 4169, btc, 3, 1, 16, edx, edx, 2, 255, 1, 2, 0, 0, 0
	BTCASE 4170, btc, 3, 1, 16, esi, esi, 6, 255, 2, 2, 0, 0, 1
	BTCASE 4171, btc, 3, 1, 16, ecx, ecx, 1, 255, 3, 2, 0, 0, 15
	BTCASE 4172, btc, 3, 1, 16, ebp, ebp, 5, 255, 0, 3, 0, 0, 16
	BTCASE 4173, btc, 3, 1, 16, eax, eax, 0, 255, 1, 3, 0, 0, 31
	BTCASE 4174, btc, 3, 1, 16, ebx, ebx, 3, 255, 2, 3, 0, 0, 33
	BTCASE 4175, btc, 3, 1, 16, edi, edi, 7, 255, 3, 3, 0, 0, 47
	ret

phase_setreg:
	SETREG 8192, 0, {seto al}, eax, 0, 0, 0, 0, 0, 0
	SETREG 8193, 0, {seto cl}, ecx, 1, 0, 1, 0, 0, 0
	SETREG 8194, 0, {seto dl}, edx, 2, 0, 2, 0, 0, 0
	SETREG 8195, 0, {seto bl}, ebx, 3, 0, 3, 0, 0, 0
	SETREG 8196, 0, {seto ah}, eax, 0, 8, 0, 1, 0, 0
	SETREG 8197, 0, {seto ch}, ecx, 1, 8, 1, 1, 0, 0
	SETREG 8198, 0, {seto dh}, edx, 2, 8, 2, 1, 0, 0
	SETREG 8199, 0, {seto bh}, ebx, 3, 8, 3, 1, 0, 0
	SETREG 8200, 1, {setno al}, eax, 0, 0, 1, 2, 0, 0
	SETREG 8201, 1, {setno cl}, ecx, 1, 0, 2, 2, 0, 0
	SETREG 8202, 1, {setno dl}, edx, 2, 0, 3, 2, 0, 0
	SETREG 8203, 1, {setno bl}, ebx, 3, 0, 0, 3, 0, 0
	SETREG 8204, 1, {setno ah}, eax, 0, 8, 1, 3, 0, 0
	SETREG 8205, 1, {setno ch}, ecx, 1, 8, 2, 3, 0, 0
	SETREG 8206, 1, {setno dh}, edx, 2, 8, 3, 3, 0, 0
	SETREG 8207, 1, {setno bh}, ebx, 3, 8, 0, 0, 1, 0
	SETREG 8208, 2, {setb al}, eax, 0, 0, 2, 0, 1, 0
	SETREG 8209, 2, {setb cl}, ecx, 1, 0, 3, 0, 1, 0
	SETREG 8210, 2, {setb dl}, edx, 2, 0, 0, 1, 1, 0
	SETREG 8211, 2, {setb bl}, ebx, 3, 0, 1, 1, 1, 0
	SETREG 8212, 2, {setb ah}, eax, 0, 8, 2, 1, 1, 0
	SETREG 8213, 2, {setb ch}, ecx, 1, 8, 3, 1, 1, 0
	SETREG 8214, 2, {setb dh}, edx, 2, 8, 0, 2, 1, 0
	SETREG 8215, 2, {setb bh}, ebx, 3, 8, 1, 2, 1, 0
	SETREG 8216, 3, {setae al}, eax, 0, 0, 3, 2, 1, 0
	SETREG 8217, 3, {setae cl}, ecx, 1, 0, 0, 3, 1, 0
	SETREG 8218, 3, {setae dl}, edx, 2, 0, 1, 3, 1, 0
	SETREG 8219, 3, {setae bl}, ebx, 3, 0, 2, 3, 1, 0
	SETREG 8220, 3, {setae ah}, eax, 0, 8, 3, 3, 1, 0
	SETREG 8221, 3, {setae ch}, ecx, 1, 8, 0, 0, 0, 1
	SETREG 8222, 3, {setae dh}, edx, 2, 8, 1, 0, 0, 1
	SETREG 8223, 3, {setae bh}, ebx, 3, 8, 2, 0, 0, 1
	SETREG 8224, 4, {sete al}, eax, 0, 0, 0, 1, 0, 1
	SETREG 8225, 4, {sete cl}, ecx, 1, 0, 1, 1, 0, 1
	SETREG 8226, 4, {sete dl}, edx, 2, 0, 2, 1, 0, 1
	SETREG 8227, 4, {sete bl}, ebx, 3, 0, 3, 1, 0, 1
	SETREG 8228, 4, {sete ah}, eax, 0, 8, 0, 2, 0, 1
	SETREG 8229, 4, {sete ch}, ecx, 1, 8, 1, 2, 0, 1
	SETREG 8230, 4, {sete dh}, edx, 2, 8, 2, 2, 0, 1
	SETREG 8231, 4, {sete bh}, ebx, 3, 8, 3, 2, 0, 1
	SETREG 8232, 5, {setne al}, eax, 0, 0, 1, 3, 0, 1
	SETREG 8233, 5, {setne cl}, ecx, 1, 0, 2, 3, 0, 1
	SETREG 8234, 5, {setne dl}, edx, 2, 0, 3, 3, 0, 1
	SETREG 8235, 5, {setne bl}, ebx, 3, 0, 0, 0, 1, 1
	SETREG 8236, 5, {setne ah}, eax, 0, 8, 1, 0, 1, 1
	SETREG 8237, 5, {setne ch}, ecx, 1, 8, 2, 0, 1, 1
	SETREG 8238, 5, {setne dh}, edx, 2, 8, 3, 0, 1, 1
	SETREG 8239, 5, {setne bh}, ebx, 3, 8, 0, 1, 1, 1
	SETREG 8240, 6, {setbe al}, eax, 0, 0, 2, 1, 1, 1
	SETREG 8241, 6, {setbe cl}, ecx, 1, 0, 3, 1, 1, 1
	SETREG 8242, 6, {setbe dl}, edx, 2, 0, 0, 2, 1, 1
	SETREG 8243, 6, {setbe bl}, ebx, 3, 0, 1, 2, 1, 1
	SETREG 8244, 6, {setbe ah}, eax, 0, 8, 2, 2, 1, 1
	SETREG 8245, 6, {setbe ch}, ecx, 1, 8, 3, 2, 1, 1
	SETREG 8246, 6, {setbe dh}, edx, 2, 8, 0, 3, 1, 1
	SETREG 8247, 6, {setbe bh}, ebx, 3, 8, 1, 3, 1, 1
	SETREG 8248, 7, {seta al}, eax, 0, 0, 3, 3, 1, 1
	SETREG 8249, 7, {seta cl}, ecx, 1, 0, 0, 0, 0, 0
	SETREG 8250, 7, {seta dl}, edx, 2, 0, 1, 0, 0, 0
	SETREG 8251, 7, {seta bl}, ebx, 3, 0, 2, 0, 0, 0
	SETREG 8252, 7, {seta ah}, eax, 0, 8, 3, 0, 0, 0
	SETREG 8253, 7, {seta ch}, ecx, 1, 8, 0, 1, 0, 0
	SETREG 8254, 7, {seta dh}, edx, 2, 8, 1, 1, 0, 0
	SETREG 8255, 7, {seta bh}, ebx, 3, 8, 2, 1, 0, 0
	SETREG 8256, 8, {sets al}, eax, 0, 0, 0, 2, 0, 0
	SETREG 8257, 8, {sets cl}, ecx, 1, 0, 1, 2, 0, 0
	SETREG 8258, 8, {sets dl}, edx, 2, 0, 2, 2, 0, 0
	SETREG 8259, 8, {sets bl}, ebx, 3, 0, 3, 2, 0, 0
	SETREG 8260, 8, {sets ah}, eax, 0, 8, 0, 3, 0, 0
	SETREG 8261, 8, {sets ch}, ecx, 1, 8, 1, 3, 0, 0
	SETREG 8262, 8, {sets dh}, edx, 2, 8, 2, 3, 0, 0
	SETREG 8263, 8, {sets bh}, ebx, 3, 8, 3, 3, 0, 0
	SETREG 8264, 9, {setns al}, eax, 0, 0, 1, 0, 1, 0
	SETREG 8265, 9, {setns cl}, ecx, 1, 0, 2, 0, 1, 0
	SETREG 8266, 9, {setns dl}, edx, 2, 0, 3, 0, 1, 0
	SETREG 8267, 9, {setns bl}, ebx, 3, 0, 0, 1, 1, 0
	SETREG 8268, 9, {setns ah}, eax, 0, 8, 1, 1, 1, 0
	SETREG 8269, 9, {setns ch}, ecx, 1, 8, 2, 1, 1, 0
	SETREG 8270, 9, {setns dh}, edx, 2, 8, 3, 1, 1, 0
	SETREG 8271, 9, {setns bh}, ebx, 3, 8, 0, 2, 1, 0
	SETREG 8272, 10, {setp al}, eax, 0, 0, 2, 2, 1, 0
	SETREG 8273, 10, {setp cl}, ecx, 1, 0, 3, 2, 1, 0
	SETREG 8274, 10, {setp dl}, edx, 2, 0, 0, 3, 1, 0
	SETREG 8275, 10, {setp bl}, ebx, 3, 0, 1, 3, 1, 0
	SETREG 8276, 10, {setp ah}, eax, 0, 8, 2, 3, 1, 0
	SETREG 8277, 10, {setp ch}, ecx, 1, 8, 3, 3, 1, 0
	SETREG 8278, 10, {setp dh}, edx, 2, 8, 0, 0, 0, 1
	SETREG 8279, 10, {setp bh}, ebx, 3, 8, 1, 0, 0, 1
	SETREG 8280, 11, {setnp al}, eax, 0, 0, 3, 0, 0, 1
	SETREG 8281, 11, {setnp cl}, ecx, 1, 0, 0, 1, 0, 1
	SETREG 8282, 11, {setnp dl}, edx, 2, 0, 1, 1, 0, 1
	SETREG 8283, 11, {setnp bl}, ebx, 3, 0, 2, 1, 0, 1
	SETREG 8284, 11, {setnp ah}, eax, 0, 8, 3, 1, 0, 1
	SETREG 8285, 11, {setnp ch}, ecx, 1, 8, 0, 2, 0, 1
	SETREG 8286, 11, {setnp dh}, edx, 2, 8, 1, 2, 0, 1
	SETREG 8287, 11, {setnp bh}, ebx, 3, 8, 2, 2, 0, 1
	SETREG 8288, 12, {setl al}, eax, 0, 0, 0, 3, 0, 1
	SETREG 8289, 12, {setl cl}, ecx, 1, 0, 1, 3, 0, 1
	SETREG 8290, 12, {setl dl}, edx, 2, 0, 2, 3, 0, 1
	SETREG 8291, 12, {setl bl}, ebx, 3, 0, 3, 3, 0, 1
	SETREG 8292, 12, {setl ah}, eax, 0, 8, 0, 0, 1, 1
	SETREG 8293, 12, {setl ch}, ecx, 1, 8, 1, 0, 1, 1
	SETREG 8294, 12, {setl dh}, edx, 2, 8, 2, 0, 1, 1
	SETREG 8295, 12, {setl bh}, ebx, 3, 8, 3, 0, 1, 1
	SETREG 8296, 13, {setge al}, eax, 0, 0, 1, 1, 1, 1
	SETREG 8297, 13, {setge cl}, ecx, 1, 0, 2, 1, 1, 1
	SETREG 8298, 13, {setge dl}, edx, 2, 0, 3, 1, 1, 1
	SETREG 8299, 13, {setge bl}, ebx, 3, 0, 0, 2, 1, 1
	SETREG 8300, 13, {setge ah}, eax, 0, 8, 1, 2, 1, 1
	SETREG 8301, 13, {setge ch}, ecx, 1, 8, 2, 2, 1, 1
	SETREG 8302, 13, {setge dh}, edx, 2, 8, 3, 2, 1, 1
	SETREG 8303, 13, {setge bh}, ebx, 3, 8, 0, 3, 1, 1
	SETREG 8304, 14, {setle al}, eax, 0, 0, 2, 3, 1, 1
	SETREG 8305, 14, {setle cl}, ecx, 1, 0, 3, 3, 1, 1
	SETREG 8306, 14, {setle dl}, edx, 2, 0, 0, 0, 0, 0
	SETREG 8307, 14, {setle bl}, ebx, 3, 0, 1, 0, 0, 0
	SETREG 8308, 14, {setle ah}, eax, 0, 8, 2, 0, 0, 0
	SETREG 8309, 14, {setle ch}, ecx, 1, 8, 3, 0, 0, 0
	SETREG 8310, 14, {setle dh}, edx, 2, 8, 0, 1, 0, 0
	SETREG 8311, 14, {setle bh}, ebx, 3, 8, 1, 1, 0, 0
	SETREG 8312, 15, {setg al}, eax, 0, 0, 3, 1, 0, 0
	SETREG 8313, 15, {setg cl}, ecx, 1, 0, 0, 2, 0, 0
	SETREG 8314, 15, {setg dl}, edx, 2, 0, 1, 2, 0, 0
	SETREG 8315, 15, {setg bl}, ebx, 3, 0, 2, 2, 0, 0
	SETREG 8316, 15, {setg ah}, eax, 0, 8, 3, 2, 0, 0
	SETREG 8317, 15, {setg ch}, ecx, 1, 8, 0, 3, 0, 0
	SETREG 8318, 15, {setg dh}, edx, 2, 8, 1, 3, 0, 0
	SETREG 8319, 15, {setg bh}, ebx, 3, 8, 2, 3, 0, 0
	ret

phase_setmem:
	mov ax, 13h
	int 10h
	SETMEM 10240, 0, 0, 0, 0, 0, 0, {seto byte [di]}
	SETMEM 10241, 1, 0, 1, 0, 0, 0, {setno byte [di]}
	SETMEM 10242, 2, 0, 2, 0, 0, 0, {setb byte [di]}
	SETMEM 10243, 3, 0, 3, 0, 0, 0, {setae byte [di]}
	SETMEM 10244, 4, 0, 0, 1, 0, 0, {sete byte [di]}
	SETMEM 10245, 5, 0, 1, 1, 0, 0, {setne byte [di]}
	SETMEM 10246, 6, 0, 2, 1, 0, 0, {setbe byte [di]}
	SETMEM 10247, 7, 0, 3, 1, 0, 0, {seta byte [di]}
	SETMEM 10248, 8, 0, 0, 2, 0, 0, {sets byte [di]}
	SETMEM 10249, 9, 0, 1, 2, 0, 0, {setns byte [di]}
	SETMEM 10250, 10, 0, 2, 2, 0, 0, {setp byte [di]}
	SETMEM 10251, 11, 0, 3, 2, 0, 0, {setnp byte [di]}
	SETMEM 10252, 12, 0, 0, 3, 0, 0, {setl byte [di]}
	SETMEM 10253, 13, 0, 1, 3, 0, 0, {setge byte [di]}
	SETMEM 10254, 14, 0, 2, 3, 0, 0, {setle byte [di]}
	SETMEM 10255, 15, 0, 3, 3, 0, 0, {setg byte [di]}
	SETMEM 10256, 0, 2, 0, 0, 1, 0, {seto byte [di]}
	SETMEM 10257, 1, 2, 1, 0, 1, 0, {setno byte [di]}
	SETMEM 10258, 2, 2, 2, 0, 1, 0, {setb byte [di]}
	SETMEM 10259, 3, 2, 3, 0, 1, 0, {setae byte [di]}
	SETMEM 10260, 4, 2, 0, 1, 1, 0, {sete byte [di]}
	SETMEM 10261, 5, 2, 1, 1, 1, 0, {setne byte [di]}
	SETMEM 10262, 6, 2, 2, 1, 1, 0, {setbe byte [di]}
	SETMEM 10263, 7, 2, 3, 1, 1, 0, {seta byte [di]}
	SETMEM 10264, 8, 2, 0, 2, 1, 0, {sets byte [di]}
	SETMEM 10265, 9, 2, 1, 2, 1, 0, {setns byte [di]}
	SETMEM 10266, 10, 2, 2, 2, 1, 0, {setp byte [di]}
	SETMEM 10267, 11, 2, 3, 2, 1, 0, {setnp byte [di]}
	SETMEM 10268, 12, 2, 0, 3, 1, 0, {setl byte [di]}
	SETMEM 10269, 13, 2, 1, 3, 1, 0, {setge byte [di]}
	SETMEM 10270, 14, 2, 2, 3, 1, 0, {setle byte [di]}
	SETMEM 10271, 15, 2, 3, 3, 1, 0, {setg byte [di]}
	SETMEM 10272, 0, 1, 0, 0, 0, 1, {seto byte es:[di]}
	SETMEM 10273, 1, 1, 1, 0, 0, 1, {setno byte es:[di]}
	SETMEM 10274, 2, 1, 2, 0, 0, 1, {setb byte es:[di]}
	SETMEM 10275, 3, 1, 3, 0, 0, 1, {setae byte es:[di]}
	SETMEM 10276, 4, 1, 0, 1, 0, 1, {sete byte es:[di]}
	SETMEM 10277, 5, 1, 1, 1, 0, 1, {setne byte es:[di]}
	SETMEM 10278, 6, 1, 2, 1, 0, 1, {setbe byte es:[di]}
	SETMEM 10279, 7, 1, 3, 1, 0, 1, {seta byte es:[di]}
	SETMEM 10280, 8, 1, 0, 2, 0, 1, {sets byte es:[di]}
	SETMEM 10281, 9, 1, 1, 2, 0, 1, {setns byte es:[di]}
	SETMEM 10282, 10, 1, 2, 2, 0, 1, {setp byte es:[di]}
	SETMEM 10283, 11, 1, 3, 2, 0, 1, {setnp byte es:[di]}
	SETMEM 10284, 12, 1, 0, 3, 0, 1, {setl byte es:[di]}
	SETMEM 10285, 13, 1, 1, 3, 0, 1, {setge byte es:[di]}
	SETMEM 10286, 14, 1, 2, 3, 0, 1, {setle byte es:[di]}
	SETMEM 10287, 15, 1, 3, 3, 0, 1, {setg byte es:[di]}
	mov ax, 3
	int 10h
	ret

phase_sahf:
	SAHFCASE 12288, 0, 0, 0
	SAHFCASE 12289, 1, 0, 1
	SAHFCASE 12290, 2, 1, 0
	SAHFCASE 12291, 3, 1, 1
	SAHFCASE 12292, 0, 3, 0
	SAHFCASE 12293, 1, 2, 1
	ret

phase_lahf:
	LAHFCASE 12544, 0, 0, 0, 0
	LAHFCASE 12545, 1, 2, 0, 0
	LAHFCASE 12546, 2, 0, 1, 0
	LAHFCASE 12547, 3, 2, 1, 0
	LAHFCASE 12548, 0, 1, 0, 1
	LAHFCASE 12549, 1, 3, 0, 1
	LAHFCASE 12550, 2, 1, 1, 1
	LAHFCASE 12551, 3, 3, 1, 1
	ret

; ------------------------------------------------------------------------
; x87 compare idioms. Expected branch mask: ja=1 jb=2 je=4 jp=8
;   st0 > src: C3=C2=C0=0 -> ja ; st0 < src: C0=1 -> jb ; equal: C3=1 -> je ;
;   unordered: C3=C2=C0=1 -> jb, je, jp (all of ZF,PF,CF set)
%macro FPSAHF 5                 ; id, a, b, expected mask, variant
	mov word [curid], %1
	mov dword [fm], 0
	fninit
	fld dword [%2]
	fcomp dword [%3]
	fnstsw ax
	%if %5==1
		mov [gapscr], ax
	%endif
	sahf
	%if %5==2
		mov [gapscr], cx
	%endif
	jbe %%n1
	mov byte [fm], 1
%%n1:	jnc %%n2
	mov byte [fm+1], 1
%%n2:	jne %%n3
	mov byte [fm+2], 1
%%n3:	jnp %%n4
	mov byte [fm+3], 1
%%n4:	mov al, [fm+3]
	add al, al
	or al, [fm+2]
	add al, al
	or al, [fm+1]
	add al, al
	or al, [fm]
	cmp al, %4
	je %%ok
	call note_fail
%%ok:	inc dword [iters]
%endmacro

%macro FPSETE 5                 ; id, a, b, expected eax, variant
	mov word [curid], %1
	mov eax, 0CAFE0000h
	fninit
	fld dword [%2]
	fcomp dword [%3]
	fnstsw ax
	%if %5>=1
		mov [gapscr], ecx
	%endif
	and ah, 45h
	cmp ah, 40h
	%if %5==2
		mov [gapscr], ecx
	%endif
	sete al
	cmp eax, %4
	je %%ok
	call note_fail
%%ok:	inc dword [iters]
%endmacro

phase_x87:
%assign v 0
%rep 3
	FPSAHF 4000h+v*4+0, f_two, f_one, 1, v
	FPSAHF 4000h+v*4+1, f_one, f_two, 2, v
	FPSAHF 4000h+v*4+2, f_one, f_one, 4, v
	FPSAHF 4000h+v*4+3, f_nan, f_one, 0Eh, v
	FPSETE 4100h+v*4+0, f_two, f_one, 0CAFE0000h, v
	FPSETE 4100h+v*4+1, f_one, f_two, 0CAFE0100h, v
	FPSETE 4100h+v*4+2, f_one, f_one, 0CAFE4001h, v
	FPSETE 4100h+v*4+3, f_nan, f_one, 0CAFE4500h, v
%assign v v+1
%endrep
	ret

; ------------------------------------------------------------------------
; checkers: compute exp_tab/expfl from the SDM rules, then verify

exp_init:
	mov si, can_tab
	mov di, exp_tab
	mov cx, 8
	cld
	rep movsd
	ret

verify:
	inc dword [iters]
	mov byte [bad], 0
	xor si, si
.l:	mov eax, [res_tab+si]
	cmp eax, [exp_tab+si]
	jne .bad
	add si, 4
	cmp si, 32
	jb .l
	mov ax, [resfl]
	and ax, FLM
	cmp ax, [expfl]
	jne .bad
	cmp byte [rdmode], 0
	je .ok
	call chk_rdchain
	jc .bad
.ok:	ret
.bad:	mov byte [bad], 1
	jmp note_fail

; BT/BTS/BTR/BTC
chk_bt:
	call exp_init
	mov al, [dsel]
	cmp al, [ssel]
	jne .d
	mov eax, [curv]         ; bt eax,eax: the operand is also the index
	mov [curidx], eax
.d:	movzx ecx, byte [cursz]
	dec ecx
	mov edx, [curidx]
	and edx, ecx            ; bit position (mod 32 / mod 16)
	mov cl, dl
	mov eax, [curv]
	mov ebx, eax
	shr ebx, cl
	and ebx, 1              ; selected bit -> new CF
	mov edi, 1
	shl edi, cl             ; bit mask
	mov esi, eax
	movzx edx, byte [curop]
	test edx, edx
	jz .done                ; BT
	cmp edx, 1
	jne .n1
	or esi, edi             ; BTS
%ifdef NEG
	xor ebx, 1
%endif
	jmp .done
.n1:	cmp edx, 2
	jne .n2
	not edi                 ; BTR
	and esi, edi
	jmp .done
.n2:	xor esi, edi            ; BTC
.done:	movzx di, byte [dsel]
	shl di, 2
	mov [exp_tab+di], esi
	movzx di, byte [ssel]
	cmp di, 0FFh
	je .nos
	cmp dl, 0               ; (dx clobbered below; recompute via dsel)
	mov dl, [dsel]
	cmp dl, [ssel]
	je .nos
	shl di, 2
	mov eax, [curidx]
	mov [exp_tab+di], eax
.nos:	mov ax, [inflags]
	and ax, FLM & ~1        ; only CF changes
	or ax, bx
	mov [expfl], ax
	jmp verify

; condition code [curcc] evaluated on [inflags] -> AL (0/1)
calc_cond:
	mov ax, [inflags]
	mov bl, al
	and bl, 1               ; C
	mov bh, al
	shr bh, 2
	and bh, 1               ; P
	mov cl, al
	shr cl, 6
	and cl, 1               ; Z
	mov ch, al
	shr ch, 7               ; S
	mov dl, ah
	shr dl, 3
	and dl, 1               ; O
	movzx si, byte [curcc]
	mov di, si
	and di, 1               ; negate
	and si, 0FEh            ; (cc>>1)*2
	jmp word [pred_tab+si]
pO:	mov al, dl
	jmp pfin
pC:	mov al, bl
	jmp pfin
pZ:	mov al, cl
	jmp pfin
pBE:	mov al, bl
	or al, cl
	jmp pfin
pS:	mov al, ch
	jmp pfin
pP:	mov al, bh
	jmp pfin
pL:	mov al, ch
	xor al, dl
	jmp pfin
pLE:	mov al, ch
	xor al, dl
	or al, cl
pfin:	mov dx, di
	xor al, dl
	ret

chk_setreg:
	call exp_init
	call calc_cond
	movzx eax, al
	mov cl, [sshift]
	shl eax, cl
	mov edx, 0FFh
	shl edx, cl
	not edx
	movzx di, byte [dsel]
	shl di, 2
	mov ebx, [can_tab+di]
	and ebx, edx
	or ebx, eax
	mov [exp_tab+di], ebx
	mov ax, [inflags]
	and ax, FLM
	mov [expfl], ax
	jmp verify

chk_setmem:
	call exp_init
	call calc_cond
	mov [expb], al
	mov ax, [inflags]
	and ax, FLM
	mov [expfl], ax
	call verify
	cmp byte [bad], 0
	jne .r
	cmp byte [memb], 11h
	jne .f
	cmp byte [memb+2], 33h
	jne .f
	mov al, [expb]
	cmp al, [memb+1]
	je .r
.f:	jmp note_fail
.r:	ret

chk_sahf:
	call exp_init
	mov eax, [eaxin]
	mov [exp_tab], eax      ; EAX unchanged
	movzx ax, byte [ahv]
	and ax, 0D5h            ; SF,ZF,AF,PF,CF from AH
	mov bx, [inflags]
	and bx, 800h            ; OF preserved
	or ax, bx
	mov [expfl], ax
	jmp verify

chk_lahf:
	call exp_init
	mov bl, [inflags]
	and bl, 0D5h
	or bl, 2                ; AH = SF:ZF:0:AF:0:PF:1:CF
	mov eax, [eaxin]
	mov ah, bl
	mov [exp_tab], eax
	mov ax, [inflags]
	and ax, FLM
	mov [expfl], ax
	jmp verify

note_fail:
	inc word [devs]
	mov ax, [curid]
	cmp ax, [lastid]
	je .nd
	mov [lastid], ax
	mov bx, [nfail]
	cmp bx, 16
	jae .nd
	shl bx, 1
	mov [faillist+bx], ax
	inc word [nfail]
.nd:	cmp byte [havedet], 0
	jne .r
	mov byte [havedet], 1
	mov [det+0], ax
	mov ax, [inflags]
	mov [det+2], ax
	mov eax, [curv]
	mov [det+4], eax
	mov eax, [curidx]
	mov [det+8], eax
	mov ax, [resfl]
	mov [det+12], ax
	mov ax, [expfl]
	mov [det+14], ax
	movzx di, byte [dsel]
	shl di, 2
	mov eax, [res_tab+di]
	mov [det+16], eax
	mov eax, [exp_tab+di]
	mov [det+20], eax
.r:	ret

; ------------------------------------------------------------------------
report:
	mov dx, msg
	mov ah, 9
	int 21h
	mov ax, [devs]
	call hex16
	mov dx, msg2
	mov ah, 9
	int 21h
	mov eax, [iters]
	push eax
	shr eax, 16
	call hex16
	pop eax
	call hex16
	call crlf
	xor si, si
.l:	cmp si, [nfail]
	jae .d
	mov bx, si
	shl bx, 1
	mov ax, [faillist+bx]
	call hex16
	mov dl, ' '
	mov ah, 2
	int 21h
	inc si
	jmp .l
.d:	call crlf
	cmp byte [havedet], 0
	je .x
	mov si, det
	mov cx, 12
.dl:	mov ax, [si]
	call hex16
	mov dl, ' '
	mov ah, 2
	int 21h
	add si, 2
	loop .dl
	call crlf
.x:	mov ax, 4c00h
	int 21h

crlf:
	mov dl, 13
	mov ah, 2
	int 21h
	mov dl, 10
	int 21h
	ret

hex16:
	push ax
	mov al, ah
	call hex8
	pop ax
hex8:
	push ax
	shr al, 4
	call .nib
	pop ax
	and al, 0Fh
.nib:	add al, '0'
	cmp al, '9'
	jbe .out
	add al, 7
.out:	mov dl, al
	mov ah, 2
	int 21h
	ret

msg	db 'FLAGOPS deviations=$'
msg2	db ' iters=$'
align 4
pred_tab dw pO, pC, pZ, pBE, pS, pP, pL, pLE
f_one	dd 3f800000h
f_two	dd 40000000h
f_nan	dd 7fc00000h
valtab	dd 00000000h, 0FFFFFFFFh, 0A5C3965Ah, 5A3C69A5h
idxtab	dd 0, 3, 15, 16, 17, 31, 32, 33, 0FFFFFFFFh, 0123456Ah
eaxtab	dd 0DEAD5AC3h, 0BEEFFFC3h, 0CAFE00C3h
can_tab	dd 11111111h, 22222222h, 33333333h, 44444444h, 0, 66666666h, 77777777h, 88888888h

%assign i 0
flagtab:
%rep 64
	dw 0202h + (i&1)*1 + ((i>>1)&1)*4 + ((i>>2)&1)*10h + ((i>>3)&1)*40h + ((i>>4)&1)*80h + ((i>>5)&1)*800h
%assign i i+1
%endrep
%assign i 0
sahftab:
%rep 64
	dw (((i&1)*1 + ((i>>1)&1)*4 + ((i>>2)&1)*10h + ((i>>3)&1)*40h + ((i>>4)&1)*80h) << 8) + ((i>>5)&1)*7Fh
%assign i i+1
%endrep

align 4
res_tab	times 8 dd 0
exp_tab	times 8 dd 0
curv	dd 0
curidx	dd 0
eaxin	dd 0
iters	dd 0
det	times 12 dw 0
faillist times 16 dw 0
inflags	dw 0
sahfax	dw 0
resfl	dw 0
expfl	dw 0
curid	dw 0
lastid	dw 0FFFFh
devs	dw 0
nfail	dw 0
fi	dw 0
vi	dw 0
xi	dw 0
ahv	dw 0
pre	dw 0
pgoff	dw 0
memoff	dw 0
gapscr	dd 0
rd_c	db 0
rd_o	db 0
rd_s	db 0
rd_z	db 0
rd_p	db 0, 0, 0
fm	dd 0
curop	db 0
cursz	db 0
dsel	db 0
ssel	db 0
rdmode	db 0
curcc	db 0
sshift	db 0
bad	db 0
havedet	db 0
expb	db 0
memb	db 0, 0, 0
mbuf	db 0, 0, 0
pgend:
