; SPDX-License-Identifier: GPL-2.0-or-later
;
; PFPREC.COM - what does a guest #PF handler observe when a memory operand
; of an integer or x87 instruction faults on a non-present page?
;
; For each case the handler records: fault count, pushed EIP, CR2, error code
; and the ESI value from its PUSHAD frame. The main code then classifies the
; pushed EIP as the faulting instruction (I), the start of the enclosing JIT
; block (B) or something else (X), and checks ESI and the loaded/stored value.
;
; Architectural expectation (Intel SDM vol. 3, 6.15 "#PF"): the saved CS:EIP
; points to the faulting instruction and the general registers hold the values
; written by preceding instructions.
;
; Output per case:  "<name> F=<faults> E=<I|B|X> R=<ok|stale> V=<ok|bad>"
; Build: nasm -f bin -o PFPREC.COM pfprec.asm

bits 16
org 100h

SEL_CODE32 equ 08h
SEL_FLAT   equ 10h
SEL_CODE16 equ 18h
SEL_DATA   equ 20h          ; same base as the COM image

start:
	cli
	push cs
	pop ds
	mov [save_ss], ss
	mov [save_sp], sp
	mov ax, cs
	mov [real_jump+3], ax
	sidt [old_idtr]
	xor eax, eax
	mov ax, cs
	shl eax, 4
	mov [base], eax
	mov [gdt_code32+2], ax
	mov [gdt_code16+2], ax
	mov [gdt_data+2], ax
	shr eax, 16
	mov [gdt_code32+4], al
	mov [gdt_code16+4], al
	mov [gdt_data+4], al
	mov [gdt_code32+7], ah
	mov [gdt_code16+7], ah
	mov [gdt_data+7], ah
	mov eax, [base]
	add eax, gdt
	mov [gdtr+2], eax
	mov eax, [base]
	add eax, idt
	mov [idtr+2], eax
	; IDT: every vector 0..31 to its own stub, #PF to pf_handler
	mov cx, 32
	mov di, idt
	mov bx, stubs
.idt_loop:
	mov [di], bx
	mov word [di+2], SEL_CODE32
	mov byte [di+5], 08eh
	mov word [di+6], 0
	add di, 8
	add bx, stub_size
	loop .idt_loop
	mov word [idt+14*8], pf_handler
	lgdt [gdtr]
	mov eax, cr0
	or eax, 1
	mov cr0, eax
	jmp dword SEL_CODE32:pm32

bits 32
pm32:
	mov ax, SEL_FLAT
	mov es, ax
	mov ss, ax
	mov ax, SEL_DATA
	mov ds, ax
	movzx esp, word [save_ss]
	shl esp, 4
	movzx eax, word [save_sp]
	add esp, eax
	; Page directory + one identity page table (first 4 MiB) in the pool.
	mov edi, [base]
	add edi, pool
	add edi, 4095
	and edi, 0fffff000h
	mov [pdir], edi
	push edi
	xor eax, eax
	mov ecx, 4096              ; directory + table, dwords
	rep stosd                  ; es:edi (flat)
	pop edi
	lea edx, [edi+4096]
	mov [ptab], edx
	lea eax, [edx+3]
	mov [es:edi], eax
	mov edi, edx
	mov eax, 3
	mov ecx, 1024
.fill:
	stosd
	add eax, 4096
	loop .fill
	; Two data pages after the tables: page A present, page B toggled.
	mov eax, [pdir]
	add eax, 2*4096
	mov [page_a], eax
	add eax, 4096
	mov [page_b], eax
	lidt [idtr]
	fninit
	mov eax, [pdir]
	mov cr3, eax
	mov eax, cr0
	or eax, 80000000h
	mov cr0, eax

	mov ebp, case_table
.next_case:
	mov eax, [ds:ebp]
	test eax, eax
	jz .done
	call eax
	add ebp, 4
	jmp .next_case
.done:
	mov eax, cr0
	and eax, 7fffffffh
	mov cr0, eax
	jmp word SEL_CODE16:pm16

; ---------------------------------------------------------------- helpers
; arm: EAX = linear address of the operand, EDX = dword to place there.
; Writes the value while present, then marks page B non-present.
arm:
	mov [es:eax], edx
	mov [operand], eax
	mov dword [faults], 0
	mov dword [seen_eip], 0
	mov dword [seen_esi], 0
	push eax
	mov eax, [page_b]
	shr eax, 12
	mov edx, [ptab]
	and dword [es:edx+eax*4], 0fffffffeh
	mov eax, [page_b]
	invlpg [es:eax]
	pop eax
	fninit
	ret

; record: classify the observation of the case just run.
; [exp_insn], [exp_blk] hold expected EIPs; AL = value check result (1 ok)
record:
	mov edi, [rec_ptr]
	mov ecx, [faults]
	mov [edi], cl
	mov ecx, [seen_eip]
	mov bl, 'X'
	cmp ecx, [exp_insn]
	jne .not_insn
	mov bl, 'I'
.not_insn:
	cmp ecx, [exp_blk]
	jne .not_blk
	mov bl, 'B'
.not_blk:
	mov [edi+1], bl
	mov bl, 0
	cmp dword [seen_esi], 0a5a5a5a5h
	jne .stale
	mov bl, 1
.stale:
	mov [edi+2], bl
	mov [edi+3], al
	add dword [rec_ptr], 4
	ret

; ------------------------------------------------------------------ cases
; Each case: arm, preload x87 if needed, jmp to a fresh block whose first
; instructions update ESI (so ESI lives in a host register) before the
; faulting instruction.

%macro CASE_BEGIN 3   ; label, operand offset in page B (may be negative), value
case_%1:
	mov eax, [page_b]
	add eax, %2
	mov edx, %3
	call arm
	mov esi, 0
	mov dword [exp_insn], %1_insn
	mov dword [exp_blk], %1_blk
	mov ebx, [operand]
	fld dword [two]
	jmp %1_blk
%1_blk:
	mov esi, 0a5a5a5a5h
	add ecx, ecx
%1_insn:
%endmacro

	CASE_BEGIN int_mov, 100h, 12345678h
	mov eax, [es:ebx]
	jmp .after
.after:
	cmp eax, 12345678h
	sete al
	jmp record

	CASE_BEGIN fld32, 100h, 3fc00000h        ; 1.5
	fld dword [es:ebx]
	jmp .after
.after:
	fstp dword [result]
	cmp dword [result], 3fc00000h
	sete al
	jmp record

	CASE_BEGIN fadd32, 100h, 3fc00000h       ; 2.0 + 1.5
	fadd dword [es:ebx]
	jmp .after
.after:
	fstp dword [result]
	cmp dword [result], 40600000h
	sete al
	jmp record

	CASE_BEGIN fild32, 100h, 7                ; 7
	fild dword [es:ebx]
	jmp .after
.after:
	fstp dword [result]
	cmp dword [result], 40e00000h
	sete al
	jmp record

	CASE_BEGIN fiadd32, 100h, 3               ; 2.0 + 3
	fiadd dword [es:ebx]
	jmp .after
.after:
	fstp dword [result]
	cmp dword [result], 40a00000h
	sete al
	jmp record

	CASE_BEGIN fld32x, -2, 3fc00000h          ; spans page A / page B
	fld dword [es:ebx]
	jmp .after
.after:
	fstp dword [result]
	cmp dword [result], 3fc00000h
	sete al
	jmp record

	CASE_BEGIN fld64, 100h, 0                 ; unchanged helper path (control)
	fld qword [es:ebx]
	jmp .after
.after:
	fstp dword [result]
	cmp dword [result], 0
	sete al
	jmp record

	CASE_BEGIN fstp32, 100h, 0                ; store path (control)
	fstp dword [es:ebx]
	jmp .after
.after:
	mov eax, [operand]
	cmp dword [es:eax], 40000000h
	sete al
	jmp record

; ---------------------------------------------------------------- handlers
pf_handler:
	pushad
	inc dword [faults]
	mov eax, [esp+36]          ; pushed EIP (after error code)
	mov [seen_eip], eax
	mov eax, [esp+4]           ; ESI slot of PUSHAD
	mov [seen_esi], eax
	cmp dword [faults], 4
	ja .give_up
	mov eax, [page_b]
	shr eax, 12
	mov edx, [ptab]
	or dword [es:edx+eax*4], 1
	mov eax, [page_b]
	invlpg [es:eax]
	popad
	add esp, 4
	iretd
.give_up:
	mov byte [unexpected], 14
	jmp bail

stubs:
%assign v 0
%rep 32
	mov byte [unexpected], v
	jmp near bail
%assign v v+1
%endrep
stub_size equ 12  ; mov byte [disp32],imm8 (7) + jmp near rel32 (5)
stubs_end:

bail:
	mov eax, [esp]
	mov [unexp_w0], eax
	mov eax, [esp+4]
	mov [unexp_w1], eax
	mov eax, cr0
	and eax, 7fffffffh
	mov cr0, eax
	jmp word SEL_CODE16:pm16

bits 16
pm16:
	mov eax, cr0
	and eax, 0fffffffeh
	mov cr0, eax
real_jump:
	jmp 0:real_mode
real_mode:
	mov ax, cs
	mov ds, ax
	mov es, ax
	mov ss, [save_ss]
	mov sp, [save_sp]
	lidt [old_idtr]
	sti
	cmp byte [unexpected], 0ffh
	je .print
	mov dx, msg_unexp
	mov ah, 9
	int 21h
	mov al, [unexpected]
	call print_hex8
	mov dl, ' '
	mov ah, 2
	int 21h
	mov eax, [unexp_w0]
	call print_hex32
	mov dl, ' '
	mov ah, 2
	int 21h
	mov eax, [unexp_w1]
	call print_hex32
	call crlf
.print:
	mov si, names
	mov bx, records
.print_loop:
	cmp bx, [rec_ptr_lo]
	jae .exit
	mov dx, si
	mov ah, 9
	int 21h
	add si, name_len
	mov dx, msg_f
	mov ah, 9
	int 21h
	mov al, [bx]
	call print_hex8
	mov dx, msg_e
	mov ah, 9
	int 21h
	mov dl, [bx+1]
	mov ah, 2
	int 21h
	mov dx, msg_r_ok
	cmp byte [bx+2], 1
	je .r
	mov dx, msg_r_stale
.r:
	mov ah, 9
	int 21h
	mov dx, msg_v_ok
	cmp byte [bx+3], 1
	je .v
	mov dx, msg_v_bad
.v:
	mov ah, 9
	int 21h
	call crlf
	add bx, 4
	jmp .print_loop
.exit:
	mov dx, msg_end
	mov ah, 9
	int 21h
	mov ax, 4c00h
	int 21h

print_hex32:
	mov cx, 4
.l:
	rol eax, 8
	push eax
	push cx
	call print_hex8
	pop cx
	pop eax
	loop .l
	ret
print_hex8:
	push ax
	shr al, 4
	call .nib
	pop ax
	and al, 0fh
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
crlf:
	mov dl, 13
	mov ah, 2
	int 21h
	mov dl, 10
	int 21h
	ret

; ------------------------------------------------------------------- data
align 4
case_table:
	dd case_int_mov, case_fld32, case_fadd32, case_fild32, case_fiadd32
	dd case_fld32x, case_fld64, case_fstp32, 0
name_len equ 10
%if (stubs_end - stubs) != 32*stub_size
%error stub size mismatch
%endif
names:
	db 'int_mov  $'
	db 'fld32    $'
	db 'fadd32   $'
	db 'fild32   $'
	db 'fiadd32  $'
	db 'fld32x   $'
	db 'fld64    $'
	db 'fstp32   $'
msg_f db ' F=$'
msg_e db ' E=$'
msg_r_ok db ' R=ok$'
msg_r_stale db ' R=stale$'
msg_v_ok db ' V=ok$'
msg_v_bad db ' V=bad$'
msg_unexp db 'UNEXPECTED_EXCEPTION $'
msg_end db 'PFPREC_DONE',13,10,'$'
align 8
gdt:
	dq 0
gdt_code32: dw 0ffffh, 0
	db 0, 09ah, 0cfh, 0
gdt_flat: dq 00cf92000000ffffh
gdt_code16: dw 0ffffh, 0
	db 0, 09ah, 000h, 0
gdt_data: dw 0ffffh, 0
	db 0, 092h, 0cfh, 0
gdt_end:
gdtr: dw gdt_end-gdt-1
	dd 0
idtr: dw 32*8-1
	dd 0
old_idtr: times 6 db 0
two dd 40000000h
base dd 0
pdir dd 0
ptab dd 0
page_a dd 0
page_b dd 0
operand dd 0
faults dd 0
seen_eip dd 0
seen_esi dd 0
exp_insn dd 0
exp_blk dd 0
result dd 0
rec_ptr: dd records
rec_ptr_lo equ rec_ptr
save_ss dw 0
save_sp dw 0
unexpected db 0ffh
align 4
unexp_w0 dd 0
unexp_w1 dd 0
align 4
records: times 64 db 0
align 8
idt: times 32*8 db 0
pool: times 6*4096 db 0
