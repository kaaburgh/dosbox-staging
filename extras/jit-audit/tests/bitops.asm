; SPDX-License-Identifier: GPL-2.0-or-later
;
; BITOPS.COM - guest semantics gate for the instructions the x86-64 dynamic
; core does not translate and hands to the normal core (BR_Opcode) in Quake
; 1.06: BT/BTS/BTR/BTC (0F A3/AB/B3/BB/BA), SETcc (0F 9x) and SAHF/LAHF,
; including the GCC x87 compare idioms "fnstsw ax; sahf" and
; "fnstsw ax; and ah,45h; cmp ah,40h; sete al".
;
; CF is captured with SBB (translated by the dynamic core), never with SETcc,
; so a future translation of either family is tested independently.
; Memory bit-string forms with register offsets beyond the dword and negative
; offsets are covered (Intel SDM vol. 2, BT: "bit string" addressing); the
; immediate form is masked to the operand size.
;
; Prints "BITOPS deviations=<hex>" followed by the failing case numbers.
; Expected values are from the SDM, not from either DOSBox core.
; Build: nasm -f bin -o BITOPS.COM bitops.asm

bits 16
org 100h

%macro CHECK 2          ; case-id, expected value in edx (compared with eax)
	cmp eax, %2
	je %%ok
	mov al, %1
	call fail
%%ok:
%endmacro

start:
	push cs
	pop ds
	push cs
	pop es

; ---- register forms, 32-bit (index masked to 5 bits) --------------------
; result in EAX[7:0] = CF mask (0xff/0), EAX[31:8] irrelevant -> pack
; we check CF and the operand separately.
%macro RCF 0            ; eax := 0xFFFFFFFF if CF else 0 (SBB, no SETcc)
	sbb eax, eax
%endmacro

	mov ebx, 80000001h
	mov ecx, 0
	bt ebx, ecx
	RCF
	CHECK 1, 0FFFFFFFFh
	mov ecx, 33              ; 33 mod 32 = 1 -> bit 1 = 0
	bt ebx, ecx
	RCF
	CHECK 2, 0
	mov ecx, 31
	bts ebx, ecx             ; CF=1, unchanged
	RCF
	CHECK 3, 0FFFFFFFFh
	mov eax, ebx
	CHECK 4, 80000001h
	mov ecx, 1
	bts ebx, ecx             ; CF=0, set bit 1
	RCF
	CHECK 5, 0
	mov eax, ebx
	CHECK 6, 80000003h
	mov ecx, 31
	btr ebx, ecx             ; CF=1, clear bit 31
	RCF
	CHECK 7, 0FFFFFFFFh
	mov eax, ebx
	CHECK 8, 00000003h
	mov ecx, 0
	btc ebx, ecx             ; CF=1, toggle bit 0
	RCF
	CHECK 9, 0FFFFFFFFh
	mov eax, ebx
	CHECK 10, 00000002h
	btc ebx, 4               ; immediate: CF=0, set bit 4
	RCF
	CHECK 11, 0
	mov eax, ebx
	CHECK 12, 00000012h
	bt ebx, 36               ; immediate 36 mod 32 = 4 -> CF=1
	RCF
	CHECK 13, 0FFFFFFFFh

; ---- register forms, 16-bit (index masked to 4 bits) --------------------
	mov bx, 8002h
	mov cx, 17               ; 17 mod 16 = 1 -> CF=1
	bt bx, cx
	RCF
	CHECK 14, 0FFFFFFFFh
	btr bx, 15               ; CF=1, clear bit 15
	RCF
	CHECK 15, 0FFFFFFFFh
	movzx eax, bx
	CHECK 16, 00000002h

; ---- memory bit string, register offset beyond the dword ---------------
	mov ecx, 37              ; dword 1, bit 5 of bitstr
	bt dword [bitstr], ecx
	RCF
	CHECK 17, 0FFFFFFFFh
	mov ecx, -1              ; dword -1 (guard), bit 31
	bt dword [bitstr], ecx
	RCF
	CHECK 18, 0FFFFFFFFh
	mov ecx, 40              ; bts: dword 1 bit 8
	bts dword [bitstr], ecx
	RCF
	CHECK 19, 0
	mov eax, [bitstr+4]
	CHECK 20, 00000120h
	mov ecx, -32             ; btr: dword -1 bit 0
	btr dword [bitstr], ecx
	RCF
	CHECK 21, 0FFFFFFFFh
	mov eax, [bitstr-4]
	CHECK 22, 80000000h
	bt dword [bitstr], 37    ; immediate: masked -> bit 5 of dword 0 = 0
	RCF
	CHECK 23, 0
	mov ecx, 2               ; 16-bit memory form, word string
	bts word [bitstr], cx
	RCF
	CHECK 24, 0
	mov eax, [bitstr]
	CHECK 25, 00000004h

; ---- SETcc (0F 9x) -------------------------------------------------------
	xor eax, eax
	mov ecx, 5
	cmp ecx, 5
	sete al                  ; ZF=1
	CHECK 26, 1
	cmp ecx, 7
	setb al                  ; CF=1
	CHECK 27, 1
	mov ecx, -3
	cmp ecx, 2
	setl al                  ; SF!=OF -> 1
	CHECK 28, 1
	setle al
	CHECK 29, 1
	setg al
	CHECK 30, 0

; ---- SAHF / LAHF ----------------------------------------------------------
	mov ah, 0D5h             ; SF ZF AF PF CF all set
	sahf
	mov eax, 0
	lahf
	and ah, 0D5h
	shr eax, 8
	CHECK 31, 0D5h
	mov ah, 01h              ; only CF
	sahf
	RCF
	CHECK 32, 0FFFFFFFFh

; ---- x87 compare idioms (GCC 2.x) ---------------------------------------
	fninit
	fld dword [two]
	fcomp dword [one]        ; 2 > 1 -> C0=C2=C3=0
	fnstsw ax
	sahf
	mov eax, 0
	ja .gt1
	jmp .after1
.gt1:
	mov eax, 1
.after1:
	CHECK 33, 1
	fld dword [one]
	fcomp dword [one]        ; equal -> C3=1
	fnstsw ax
	and ah, 45h
	cmp ah, 40h
	mov eax, 0
	sete al
	CHECK 34, 1

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

msg db 'BITOPS deviations=$'
align 4
one dd 3f800000h
two dd 40000000h
guard dd 80000001h         ; bitstr-4
bitstr dd 00000000h, 00000020h
fails db 0
faillist times 32 db 0

