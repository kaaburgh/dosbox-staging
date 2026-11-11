; SPDX-License-Identifier: GPL-2.0-or-later
;
; FLAGRST.COM - guest-visible behaviour of every path on which the x86-64
; dynamic core reloads saved guest flags into the host flags
; (gen_needflags / gen_needflags_protected): CMC/STC/CLC, Jcc (all 16
; conditions), LOOPE/LOOPNE, SALC, shift by CL=0, ROL by 1, IMUL, SHLD -
; for all 64 combinations of CF PF AF ZF SF OF, with the flags coming from
; (a) a saved image written by POPF and (b) live host flags saved by PUSHFQ
; around a memory access, and with all general registers live.
;
; The program only records raw observations, one line per flag combination:
;   <flags> w0 w1 ... w21          (hex)
; check_flagrst.py derives the expected values from the Intel SDM instruction
; definitions and compares. Flags are read with PUSHF, which reads the saved
; image directly, so every observed value has passed through the restore.
;
; Build: nasm -f bin -o FLAGRST.COM flagrst.asm
; Check: run_dos_test.py ... --com FLAGRST.COM | python3 check_flagrst.py

bits 16
org 100h

ARITH equ 08D5h
NWORDS equ 22

%macro SETF 0           ; guest flags := [cur]
	mov ax, [cur]
	push ax
	popf
%endmacro

%macro GETF 1           ; word %1 := arithmetic flags
	pushf
	pop ax
	and ax, ARITH
	mov [w + 2*%1], ax
%endmacro

%macro DIFF 1           ; word %1 |= (arithmetic flags ^ [cur])
	pushf
	pop ax
	xor ax, [cur]
	and ax, ARITH
	or [w + 2*%1], ax
%endmacro

start:
	push cs
	pop ds
	push cs
	pop es
	xor bp, bp
	mov word [val], 0
next_val:
	mov bx, [val]
	test bx, 0F72Ah             ; only the six arithmetic bits may be set
	jnz near skip_val
	or bx, 0202h                ; IF stays set, bit 1 is always 1
	mov [cur], bx
	mov di, w
	mov cx, NWORDS
	xor ax, ax
	rep stosw

; w0: read back
	SETF
	GETF 0
; w1..w3: CMC / STC / CLC straight after the image was written
	SETF
	cmc
	GETF 1
	SETF
	stc
	GETF 2
	SETF
	clc
	GETF 3

; w4: Jcc taken mask, w5: flags unchanged by Jcc
%assign i 0
%rep 16
	SETF
	db 70h + i, (j1t %+ i) - ($ + 2)
	DIFF 5
	jmp short j1e %+ i
j1t %+ i:
	DIFF 5
	or word [w + 2*4], 1 << i
j1e %+ i:
%assign i i+1
%endrep

; w6: flags live in the host, saved around a memory read, then CMC
	SETF
	cmc
	cmc
	mov edx, [memval]
	cmc
	GETF 6

; w7/w8: same, consumed by Jcc
%assign i 0
%rep 16
	SETF
	cmc
	cmc
	mov edx, [memval]
	db 70h + i, (j2t %+ i) - ($ + 2)
	DIFF 8
	jmp short j2e %+ i
j2t %+ i:
	DIFF 8
	or word [w + 2*7], 1 << i
j2e %+ i:
%assign i i+1
%endrep

; w9: shift by CL = 0 leaves all flags unchanged
	SETF
	mov ebx, 12345678h
	mov cl, 0
	shl ebx, cl
	GETF 9
; w10: ROL r32,1 changes only CF and OF
	SETF
	mov ebx, 80000001h
	rol ebx, 1
	GETF 10
; w11/w12: SALC
	SETF
	mov ax, 5555h
	db 0D6h
	mov [w + 2*11], ax          ; MOV leaves the flags alone
	GETF 12
	and word [w + 2*11], 00FFh
; w13..w15: LOOPE / LOOPNE
	SETF
	mov cx, 2
	loope le_t
	DIFF 15
	jmp short le_e
le_t:
	DIFF 15
	mov word [w + 2*13], 1
le_e:
	SETF
	mov cx, 2
	loopne ln_t
	DIFF 15
	jmp short ln_e
ln_t:
	DIFF 15
	mov word [w + 2*14], 1
ln_e:

; w16/w17: every general register live and modified across the restore
	SETF
	mov eax, 0A1A2A3A4h
	mov ecx, 0C1C2C3C4h
	mov edx, 0D1D2D3D4h
	mov ebx, 0B1B2B3B4h
	mov esi, 051525354h
	mov edi, 0E1E2E3E4h
	cmc
	mov [regs], eax
	mov [regs+4], ecx
	mov [regs+8], edx
	mov [regs+12], ebx
	mov [regs+16], esi
	mov [regs+20], edi
	GETF 16
	mov si, regs
	mov di, regs_a
	call cmpregs
	mov [w + 2*17], ax
; w18/w19: other allocation order, restore for a Jcc
	SETF
	mov ebx, 0B1B2B3B4h
	mov edx, 0D1D2D3D4h
	mov ecx, 0C1C2C3C4h
	mov eax, 0A1A2A3A4h
	mov edi, 0E1E2E3E4h
	mov esi, 051525354h
	jo short ro_t
	mov word [w + 2*18], 0
	jmp short ro_e
ro_t:
	mov word [w + 2*18], 1
ro_e:
	mov [regs], eax
	mov [regs+4], ecx
	mov [regs+8], edx
	mov [regs+12], ebx
	mov [regs+16], esi
	mov [regs+20], edi
	mov si, regs
	mov di, regs_a
	call cmpregs
	mov [w + 2*19], ax
; w20: IMUL r32,r32,imm8 (CF/OF defined, the rest compared between builds)
	SETF
	mov eax, 3
	imul eax, eax, 5
	GETF 20
; w21: SHLD r32,r32,1 (AF undefined, compared between builds)
	SETF
	mov ebx, 80000000h
	xor edx, edx                ; clobbers flags: reload them
	SETF
	shld ebx, edx, 1
	GETF 21

; ---- print the line
	mov di, line
	mov ax, [cur]
	and ax, ARITH
	call hex16
	mov si, w
	mov cx, NWORDS
.pw:
	mov al, ' '
	stosb
	lodsw
	call hex16
	loop .pw
	mov ax, 0A0Dh
	stosw
	mov dx, line
	mov cx, di
	sub cx, dx
	mov bx, 1
	mov ah, 40h
	int 21h
skip_val:
	inc word [val]
	cmp word [val], 1000h
	jb near next_val
	mov dx, done
	mov ah, 9
	int 21h
	mov ax, 4c00h
	int 21h

cmpregs:                        ; ax := 0 if 6 dwords at si equal those at di
	mov cx, 12
	xor ax, ax
.l:
	mov dx, [si]
	xor dx, [di]
	or ax, dx
	add si, 2
	add di, 2
	loop .l
	ret

hex16:                          ; ax -> 4 hex digits at es:di
	push cx
	mov cx, 4
.d:
	rol ax, 4
	push ax
	and al, 0Fh
	add al, '0'
	cmp al, '9'
	jbe .o
	add al, 7
.o:
	stosb
	pop ax
	loop .d
	pop cx
	ret

align 4
memval dd 0CAFEBABEh
regs_a dd 0A1A2A3A4h, 0C1C2C3C4h, 0D1D2D3D4h, 0B1B2B3B4h, 051525354h, 0E1E2E3E4h
regs times 6 dd 0
val dw 0
cur dw 0
w times NWORDS dw 0
done db 'FLAGRST_DONE', 13, 10, '$'
line times 160 db 0
