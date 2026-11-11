; SPDX-License-Identifier: GPL-2.0-or-later
;
; STACKOPS.COM - PUSH/POP/CALL/RET semantics of the dynamic core, including
; page-crossing accesses, #PF during stack operations and 16-bit stacks.
;
; Expected values come from the Intel SDM (vol. 2 PUSH, POP, CALL, RET; vol. 3
; 4.7 "Page-fault exceptions", "Interrupt 14"):
;   PUSH: ESP is decremented by the operand size and the value stored; PUSH ESP
;         pushes the value ESP had before the instruction.
;   POP:  value loaded from [ESP], then ESP incremented; POP ESP ends with the
;         popped value; POP [ESP+x] computes the address after the increment.
;   CALL/RET: push of the return address (next EIP) / pop into EIP; RET imm16
;         then adds imm16 to ESP.
;   o16 forms in 32-bit code push/pop 2 bytes; POP r16 leaves bits 31:16 of
;         the register alone.
;   #PF:  CR2 = faulting linear address, error code bit0=P (0 not present),
;         bit1=W/R (1 write), bit2=U/S (1 user); the saved CS:EIP is the
;         faulting instruction; the registers (ESP!) are those from before the
;         instruction, so the instruction can be restarted.
;
; The #PF handler is a separate task (task gate, own stack) so that it can run
; while the faulting stack page is not present. The faulting task's EIP/ESP/
; ECX/EAX are read from its TSS.
;
; Output: one line per check: "<name> [sub] got=... exp=... OK|FAIL" or
; "... got=... OBS" for observations that have no SDM expectation (16-bit
; stack wrap, partial writes before a fault: baseline behaviour is recorded
; and must be identical between builds). Final line:
; "STACKOPS fails=NN checks=NN".
; Build: nasm -f bin -o STACKOPS.COM stackops.asm   (-DNEG adds a failing check)

bits 16
org 100h

SEL_CODE32 equ 08h
SEL_FLAT   equ 10h
SEL_CODE16 equ 18h
SEL_DATA   equ 20h          ; same base as the COM image
SEL_TSSM   equ 28h
SEL_TSSP   equ 30h
SEL_SS16   equ 38h          ; 16-bit stack segment, base = test pages
SEL_CODEU  equ 48h|3
SEL_FLATU  equ 50h|3
SEL_DATAU  equ 58h|3

; ------------------------------------------------------------ log macros
%assign ck_n 0

%macro NAMEREG 1
	%xdefine CKNAME_%[ck_n] %1
	%assign ck_n ck_n+1
%endmacro

; CK name, got, expected   (32-bit code; expected must not be eax/edx)
%macro CK 3
	%assign ck_id ck_n
	NAMEREG %1
	push eax
	push edx
	mov eax, %2
	mov edx, %3
	push dword ck_id
	call ck_check
	pop edx
	pop eax
%endmacro

; OBS name, value (32-bit code)
%macro OBS 2
	%assign ck_id ck_n
	NAMEREG %1
	push eax
	push edx
	mov eax, %2
	push dword ck_id
	call ck_obs
	pop edx
	pop eax
%endmacro

; CKM name, linear address (reg/imm/mem), expected   - compare dword in memory
%macro CKM 3
	%assign ck_id ck_n
	NAMEREG %1
	push eax
	push edx
	mov eax, %2
	call mem32
	mov edx, %3
	push dword ck_id
	call ck_check
	pop edx
	pop eax
%endmacro

; CKM16 name, linear address, expected   - compare word in memory
%macro CKM16 3
	%assign ck_id ck_n
	NAMEREG %1
	push eax
	push edx
	mov eax, %2
	call mem16
	mov edx, %3
	push dword ck_id
	call ck_check
	pop edx
	pop eax
%endmacro

; CKG name, linear address of the dword   - bytes just outside must be A5
%macro CKG 2
	%assign ck_id ck_n
	NAMEREG %1
	push eax
	push edx
	mov eax, %2
	call guard32
	mov edx, 0A5A5h
	push dword ck_id
	call ck_check
	pop edx
	pop eax
%endmacro

%macro CKG16 2
	%assign ck_id ck_n
	NAMEREG %1
	push eax
	push edx
	mov eax, %2
	call guard16
	mov edx, 0A5A5h
	push dword ck_id
	call ck_check
	pop edx
	pop eax
%endmacro

; OBSM name, linear address - observe dword in memory
%macro OBSM 2
	%assign ck_id ck_n
	NAMEREG %1
	push eax
	push edx
	mov eax, %2
	call mem32
	push dword ck_id
	call ck_obs
	pop edx
	pop eax
%endmacro

; CKFAULT prefix: check the first recorded fault against x_cr2/x_err/x_eip/x_esp
%macro CKFAULT 1
	%strcat n_a %1,' nfault'
	CK n_a, [faults], 1
	%strcat n_a %1,' cr2'
	CK n_a, [frec+4], [x_cr2]
	%strcat n_a %1,' err'
	CK n_a, [frec], [x_err]
	%strcat n_a %1,' eip'
	CK n_a, [frec+8], [x_eip]
	%strcat n_a %1,' esp@fault'
	CK n_a, [frec+12], [x_esp]
%endmacro

; PTE helpers (page k of the test region)
%macro UNMAP 1
	push eax
	mov eax, [tp]
	add eax, (%1)*4096
	call unmap_page
	pop eax
%endmacro

%macro PGADDR 2     ; reg, page number
	mov %1, [tp]
	add %1, (%2)*4096
%endmacro

; ---------------------------------------------------------- start (real mode)
start:
	cli
	push cs
	pop ds
	mov sp, stack_top
	mov [save_ss], ss
	mov [save_sp], sp
	mov ax, cs
	mov [real_jump+3], ax
	sidt [old_idtr]
	xor eax, eax
	mov ax, cs
	shl eax, 4
	mov [base], eax
	; real-mode scratch stack segment: 64 KiB + spill, in bss after the pool
	mov eax, [base]
	add eax, scratch_bss
	add eax, 15
	shr eax, 4
	mov [rm_seg], ax
	call real_tests
%ifdef RMONLY
	jmp real_mode.print
%endif
	xor eax, eax
	mov ax, cs
	shl eax, 4
	mov [gdt_code32+2], ax
	mov [gdt_code16+2], ax
	mov [gdt_data+2], ax
	mov [gdt_codeu+2], ax
	mov [gdt_datau+2], ax
	shr eax, 16
	mov [gdt_code32+4], al
	mov [gdt_code16+4], al
	mov [gdt_data+4], al
	mov [gdt_codeu+4], al
	mov [gdt_datau+4], al
	mov [gdt_code32+7], ah
	mov [gdt_code16+7], ah
	mov [gdt_data+7], ah
	mov [gdt_codeu+7], ah
	mov [gdt_datau+7], ah
	; TSS descriptors
	mov eax, [base]
	add eax, tss_main
	mov [gdt_tssm+2], ax
	shr eax, 16
	mov [gdt_tssm+4], al
	mov [gdt_tssm+7], ah
	mov eax, [base]
	add eax, tss_pf
	mov [gdt_tssp+2], ax
	shr eax, 16
	mov [gdt_tssp+4], al
	mov [gdt_tssp+7], ah
	mov eax, [base]
	add eax, gdt
	mov [gdtr+2], eax
	mov eax, [base]
	add eax, idt
	mov [idtr+2], eax
	; IDT: every vector 0..31 to its own stub, #PF is a task gate
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
	mov word [idt+14*8], 0
	mov word [idt+14*8+2], SEL_TSSP
	mov byte [idt+14*8+4], 0
	mov byte [idt+14*8+5], 085h
	mov word [idt+80h*8], int80_handler
	mov word [idt+80h*8+2], SEL_CODE32
	mov byte [idt+80h*8+5], 0eeh
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
	mov [stk0_lin], esp
	; Page directory + identity page table (first 4 MiB) in the pool.
	mov edi, [base]
	add edi, pool
	add edi, 4095
	and edi, 0fffff000h
	mov [pdir], edi
	push edi
	xor eax, eax
	mov ecx, 2048              ; directory + table, dwords
	rep stosd                  ; es:edi (flat)
	pop edi
	lea edx, [edi+4096]
	mov [ptab], edx
	lea eax, [edx+7]
	mov [es:edi], eax
	mov edi, edx
	mov eax, 7                 ; present, writable, user
	mov ecx, 1024
.fill:
	stosd
	add eax, 4096
	loop .fill
	mov eax, [pdir]
	add eax, 2*4096
	mov [tp], eax              ; test pages: 24 pages from here
	; TSS setup
	mov eax, [pdir]
	mov [tss_main+1ch], eax
	mov [tss_pf+1ch], eax
	mov dword [tss_main+8], SEL_FLAT
	mov eax, [base]
	add eax, ring0_stack_top
	mov [tss_main+4], eax
	mov dword [tss_pf+20h], pf_task
	mov dword [tss_pf+24h], 2
	mov eax, [base]
	add eax, pf_stack_top
	mov [tss_pf+38h], eax
	mov dword [tss_pf+48h], SEL_FLAT
	mov dword [tss_pf+4ch], SEL_CODE32
	mov dword [tss_pf+50h], SEL_FLAT
	mov dword [tss_pf+54h], SEL_DATA
	mov dword [tss_pf+8], SEL_FLAT
	; 16-bit stack descriptor based at the test pages
	mov eax, [tp]
	mov [gdt_ss16+2], ax
	shr eax, 16
	mov [gdt_ss16+4], al
	mov [gdt_ss16+7], ah
	lidt [idtr]
	mov ax, SEL_TSSM
	ltr ax
%ifdef PMSTOP0
	jmp .done
%endif
	mov eax, [pdir]
	mov cr3, eax
	mov eax, cr0
	or eax, 80000000h
	mov cr0, eax

%ifdef PMSTOP
	jmp .done
%endif
	mov dword [case_ptr], case_table
.next_case:
%ifdef NCASES
	cmp dword [case_ptr], case_table+4*NCASES
	jae .done
%endif
	mov ebx, [case_ptr]
	mov eax, [ebx]
	test eax, eax
	jz .done
	mov dword [idx], 0
	call eax
	add dword [case_ptr], 4
	jmp .next_case
.done:
%ifdef NEG
	CK 'negative control (FAIL)', 1, 2
%endif
	mov eax, cr0
	and eax, 7fffffffh
	mov cr0, eax
	jmp word SEL_CODE16:pm16

; ---------------------------------------------------------------- helpers
ck_check:                       ; eax=got edx=exp [esp+4]=id
	push ebx
	push edi
	mov ebx, [esp+12]
	mov edi, [rec_ptr]
	mov [edi], bx
	mov byte [edi+2], 0
	mov bl, [idx]
	mov [edi+3], bl
	mov [edi+4], eax
	mov [edi+8], edx
	add dword [rec_ptr], 12
	pop edi
	pop ebx
	ret 4

ck_obs:                         ; eax=value [esp+4]=id
	push ebx
	push edi
	mov ebx, [esp+12]
	mov edi, [rec_ptr]
	mov [edi], bx
	mov byte [edi+2], 1
	mov bl, [idx]
	mov [edi+3], bl
	mov [edi+4], eax
	mov dword [edi+8], 0
	add dword [rec_ptr], 12
	pop edi
	pop ebx
	ret 4

mem32:                          ; eax = linear -> eax = dword (byte reads)
	push ebx
	movzx ebx, byte [es:eax+3]
	shl ebx, 8
	mov bl, [es:eax+2]
	shl ebx, 8
	mov bl, [es:eax+1]
	shl ebx, 8
	mov bl, [es:eax]
	mov eax, ebx
	pop ebx
	ret

mem16:
	push ebx
	movzx ebx, byte [es:eax+1]
	shl ebx, 8
	mov bl, [es:eax]
	mov eax, ebx
	pop ebx
	ret

guard32:                        ; (byte[p-1]<<8)|byte[p+4]
	push ebx
	movzx ebx, byte [es:eax-1]
	shl ebx, 8
	mov bl, [es:eax+4]
	mov eax, ebx
	pop ebx
	ret

guard16:                        ; (byte[p-1]<<8)|byte[p+2]
	push ebx
	movzx ebx, byte [es:eax-1]
	shl ebx, 8
	mov bl, [es:eax+2]
	mov eax, ebx
	pop ebx
	ret

unmap_page:                     ; eax = linear address
	pushad
	mov ebx, eax
	shr ebx, 12
	mov edx, [ptab]
	and dword [es:edx+ebx*4], 0fffffffeh
	invlpg [es:eax]
	popad
	ret

; Make pages 0..23 present+user, fill pages 0..4 with A5, clear fault log.
reset_pages:
	pushad
	mov edx, [tp]
	shr edx, 12
	shl edx, 2
	add edx, [ptab]
	mov eax, [tp]
	or eax, 7
	mov ecx, 24
.p:
	mov [es:edx], eax
	add edx, 4
	add eax, 4096
	loop .p
	mov eax, [tp]
	mov ecx, 24
.i:
	invlpg [es:eax]
	add eax, 4096
	loop .i
	mov edi, [tp]
	mov ecx, 5*1024
	mov eax, 0a5a5a5a5h
	rep stosd
	mov dword [faults], 0
	mov dword [snap_ptr], 0
	mov ecx, 32
	mov edi, frec
	xor eax, eax
.f:
	mov [edi], eax
	add edi, 4
	loop .f
	popad
	ret

; Fill page EAX (index) with A5 (for pages beyond 4)
fill_page:
	pushad
	shl eax, 12
	add eax, [tp]
	mov edi, eax
	mov ecx, 1024
	mov eax, 0a5a5a5a5h
	rep stosd
	popad
	ret

; ------------------------------------------------------------------ cases
; 1. 32-bit PUSH r32 / POP r32 with the dword at page offsets FFC..FFF
case_p32:
.lp:
	call reset_pages
	PGADDR eax, 2
	add eax, [idx]
	mov [t_esp0], eax
	mov [saved_esp], esp
	mov ebx, 11223344h
	mov esp, eax
	push ebx
	mov edx, esp
	pop ecx
	mov esi, esp
	mov esp, [saved_esp]
	mov edi, [t_esp0]
	sub edi, 4
	CK 'p32 esp after push', edx, edi
	mov edi, [t_esp0]
	CK 'p32 esp after pop', esi, edi
	CK 'p32 popped value', ecx, 11223344h
	mov edi, [t_esp0]
	sub edi, 4
	CKM 'p32 memory', edi, 11223344h
	CKG 'p32 guards', edi
	inc dword [idx]
	cmp dword [idx], 4
	jb .lp
	ret

; 2. misc forms; ESP start S = pg2+idx (the pushed dword lands at pg2-4+idx)
case_misc:
.lp:
	call reset_pages
	PGADDR eax, 2
	add eax, [idx]
	mov [t_esp0], eax          ; S
	PGADDR ebx, 1
	add ebx, 400h
	mov [pgz], ebx
	; --- PUSH imm8 (sign-extended), PUSH imm32
	mov [saved_esp], esp
	mov esp, eax
	push byte -128
	mov edx, esp
	push dword 89abcdefh
	mov esi, esp
	pop ecx
	pop eax
	mov edi, esp
	mov esp, [saved_esp]
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'imm8 esp', edx, ebx
	CKM 'imm8 mem', ebx, 0ffffff80h
	sub ebx, 4
	CK 'imm32 esp', esi, ebx
	CK 'imm32 value', ecx, 89abcdefh
	CK 'imm8 value', eax, 0ffffff80h
	mov ebx, [t_esp0]
	CK 'imm pair esp', edi, ebx
	; --- PUSH [mem], POP [mem]
	mov dword [pushsrc], 0cafebabeh
	mov dword [popdst], 0
	mov esp, ebx
	push dword [pushsrc]
	mov edx, esp
	pop dword [popdst]
	mov esi, esp
	mov esp, [saved_esp]
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'push [m] esp', edx, ebx
	CKM 'push [m] mem', ebx, 0cafebabeh
	CK 'pop [m] value', [popdst], 0cafebabeh
	CK 'pop [m] esp', esi, [t_esp0]
	; --- POP dword [esp]: address computed after the increment (SDM)
	mov esp, [t_esp0]
	push dword 12345678h
	pop dword [esp]
	mov edx, esp
	mov esp, [saved_esp]
	CK 'pop [esp] esp', edx, [t_esp0]
	CKM 'pop [esp] mem', [t_esp0], 12345678h
	; --- PUSH ESP pushes the old value
	mov esp, [t_esp0]
	push esp
	mov edx, esp
	pop eax
	mov esp, [saved_esp]
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'push esp esp', edx, ebx
	CK 'push esp value', eax, [t_esp0]
	; --- POP ESP ends with the popped value
	mov esp, [t_esp0]
	push dword [pgz]
	pop esp
	mov edx, esp
	mov esp, [saved_esp]
	CK 'pop esp', edx, [pgz]
	; --- PUSH [esp] reads before the decrement
	mov esp, [t_esp0]
	push dword 5aa5f00fh
	push dword [esp]
	mov edx, esp
	pop eax
	pop ecx
	mov esp, [saved_esp]
	mov ebx, [t_esp0]
	sub ebx, 8
	CK 'push [esp] esp', edx, ebx
	CK 'push [esp] v1', eax, 5aa5f00fh
	CK 'push [esp] v2', ecx, 5aa5f00fh
	; --- CALL rel32 / RET
	mov esp, [t_esp0]
	call .t1
.r1:
	mov esi, esp
	jmp .d1
.t1:
	mov edx, esp
	ret
.d1:
	mov esp, [saved_esp]
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'call rel32 esp', edx, ebx
	CKM 'call rel32 retaddr', ebx, .r1
	CK 'ret esp', esi, [t_esp0]
	; --- CALL reg
	mov esp, [t_esp0]
	mov ebx, .t2
	call ebx
.r2:
	mov esi, esp
	jmp .d2
.t2:
	mov edx, esp
	ret
.d2:
	mov esp, [saved_esp]
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'call reg esp', edx, ebx
	CKM 'call reg retaddr', ebx, .r2
	CK 'call reg ret esp', esi, [t_esp0]
	; --- CALL [mem]
	mov dword [calltgt], .t3
	mov esp, [t_esp0]
	call dword [calltgt]
.r3:
	mov esi, esp
	jmp .d3
.t3:
	mov edx, esp
	ret
.d3:
	mov esp, [saved_esp]
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'call [m] esp', edx, ebx
	CKM 'call [m] retaddr', ebx, .r3
	CK 'call [m] ret esp', esi, [t_esp0]
	; --- RET imm16: pops EIP then ESP += 8
	mov esp, [t_esp0]
	push dword 1
	push dword 2
	push dword .land
	ret 8
	mov esp, [saved_esp]
	jmp .bad
.land:
	mov edx, esp
	mov esp, [saved_esp]
	CK 'ret 8 esp', edx, [t_esp0]
	jmp .after_ret
.bad:
	CK 'ret 8 landed', 0, 1
.after_ret:
	; --- PUSH Sreg (32-bit): low word is the selector; upper half observed
	mov esp, [t_esp0]
	push es
	mov edx, esp
	pop eax
	push cs
	pop ecx
	mov esp, [saved_esp]
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'push es esp', edx, ebx
	and eax, 0ffffh
	CK 'push es sel', eax, SEL_FLAT
	and ecx, 0ffffh
	CK 'push cs sel', ecx, SEL_CODE32
	mov ebx, [t_esp0]
	sub ebx, 4
	OBSM 'push cs dword (obs)', ebx
	; --- PUSHFD / POPFD (via helper functions in the core)
	mov esp, [t_esp0]
	xor ecx, ecx               ; ZF=1 PF=1, CF=AF=SF=OF=0
	pushfd
	pop eax
	push dword 0d7h            ; CF PF AF ZF SF
	popfd
	pushfd
	pop edx
	mov esp, [saved_esp]
	and eax, 8d5h
	CK 'pushfd flags', eax, 44h
	and edx, 8d5h
	CK 'popfd flags', edx, 0d5h
	inc dword [idx]
	cmp dword [idx], 4
	jb .lp
	ret

; 3. o16 forms in 32-bit code; ESP start S = pg2+idx
case_o16:
.lp:
	call reset_pages
	PGADDR eax, 2
	add eax, [idx]
	mov [t_esp0], eax
	mov [saved_esp], esp
	mov ebx, 0a1b2c3d4h
	mov ecx, 0aabbccddh
	mov esp, eax
	push bx
	mov edx, esp
	pop cx
	mov esi, esp
	mov esp, [saved_esp]
	mov edi, [t_esp0]
	sub edi, 2
	CK 'o16 esp after push', edx, edi
	CK 'o16 esp after pop', esi, [t_esp0]
	CK 'o16 pop keeps upper', ecx, 0aabbc3d4h
	CKM16 'o16 mem', edi, 0c3d4h
	CKG16 'o16 guards', edi
	; push word imm, push byte imm (o16), pop word [m], push sp, pop sp
	mov dword [popdst], 0ffffffffh
	mov esp, [t_esp0]
	push word 1234h
	mov edx, esp
	push word -1
	mov esi, esp
	pop word [popdst]
	pop word [popdst+2]
	mov esp, [saved_esp]
	CK 'o16 imm16 esp', edx, edi
	sub edi, 2
	CK 'o16 imm8 esp', esi, edi
	CK 'o16 pop [m] x2', [popdst], 1234ffffh
	mov esp, [t_esp0]
	push sp
	mov edx, esp
	pop word [popdst]
	mov esp, [saved_esp]
	mov edi, [t_esp0]
	and edi, 0ffffh
	mov eax, [popdst]
	and eax, 0ffffh
	CK 'o16 push sp', eax, edi
	mov esp, [t_esp0]
	push word 0900h
	pop sp
	mov edx, esp
	mov esp, [saved_esp]
	mov edi, [t_esp0]
	sub edi, 2
	and edi, 0ffff0000h
	or edi, 900h
	CK 'o16 pop sp', edx, edi
	; o16 call / ret
	mov esp, [t_esp0]
	call word .t1
.r1:
	mov esi, esp
	jmp .d1
.t1:
	mov edx, esp
	o16 ret
.d1:
	mov esp, [saved_esp]
	mov ebx, [t_esp0]
	sub ebx, 2
	CK 'o16 call esp', edx, ebx
	CKM16 'o16 call retaddr', ebx, .r1
	CK 'o16 ret esp', esi, [t_esp0]
	inc dword [idx]
	cmp dword [idx], 4
	jb .lp
	ret

; 4. #PF on stack operations (ring 0). Handler maps the page; instruction is
;    restarted. pg1 is below the boundary, pg2 above it.
case_pf_push:                       ; in-page push, page not present
	call reset_pages
	PGADDR eax, 2
	add eax, 800h
	mov [t_esp0], eax
	lea edx, [eax-4]
	mov [x_cr2], edx
	mov dword [x_err], 2
	mov dword [x_eip], .fi
	mov [x_esp], eax
	UNMAP 2
	mov [saved_esp], esp
	mov ebx, 0deadbeefh
	mov esp, eax
.fi:
	push ebx
	mov edx, esp
	mov esp, [saved_esp]
	CKFAULT 'pf push'
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'pf push esp after', edx, ebx
	CKM 'pf push mem', ebx, 0deadbeefh
	ret

case_pf_pop:
	call reset_pages
	PGADDR eax, 2
	add eax, 800h
	mov [t_esp0], eax
	mov dword [es:eax], 0cafef00dh
	mov [x_cr2], eax
	mov dword [x_err], 0
	mov dword [x_eip], .fi
	mov [x_esp], eax
	UNMAP 2
	mov [saved_esp], esp
	mov ecx, 0deadbeefh
	mov esp, eax
.fi:
	pop ecx
	mov edx, esp
	mov esp, [saved_esp]
	CKFAULT 'pf pop'
	CK 'pf pop ecx@fault', [frec+16], 0deadbeefh
	CK 'pf pop ecx', ecx, 0cafef00dh
	mov ebx, [t_esp0]
	add ebx, 4
	CK 'pf pop esp after', edx, ebx
	ret

case_pf_call:
	call reset_pages
	PGADDR eax, 2
	add eax, 800h
	mov [t_esp0], eax
	lea edx, [eax-4]
	mov [x_cr2], edx
	mov dword [x_err], 2
	mov dword [x_eip], .fi
	mov [x_esp], eax
	UNMAP 2
	mov [saved_esp], esp
	mov esp, eax
.fi:
	call .t
.r:
	mov esi, esp
	jmp .d
.t:
	mov edx, esp
	ret
.d:
	mov esp, [saved_esp]
	CKFAULT 'pf call'
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'pf call esp in callee', edx, ebx
	CKM 'pf call retaddr', ebx, .r
	CK 'pf call esp after ret', esi, [t_esp0]
	ret

case_pf_ret:
	call reset_pages
	PGADDR eax, 2
	add eax, 800h
	mov [t_esp0], eax
	mov dword [es:eax], .land
	mov [x_cr2], eax
	mov dword [x_err], 0
	mov dword [x_eip], .fi
	mov [x_esp], eax
	UNMAP 2
	mov [saved_esp], esp
	mov esp, eax
.fi:
	ret
	mov esp, [saved_esp]
	CK 'pf ret landed', 0, 1
	ret
.land:
	mov edx, esp
	mov esp, [saved_esp]
	CKFAULT 'pf ret'
	mov ebx, [t_esp0]
	add ebx, 4
	CK 'pf ret esp after', edx, ebx
	ret

case_pf_popesp:                      ; POP ESP faulting: ESP must stay
	call reset_pages
	PGADDR eax, 2
	add eax, 800h
	mov [t_esp0], eax
	PGADDR ebx, 1
	add ebx, 400h
	mov [pgz], ebx
	mov [es:eax], ebx
	mov [x_cr2], eax
	mov dword [x_err], 0
	mov dword [x_eip], .fi
	mov [x_esp], eax
	UNMAP 2
	mov [saved_esp], esp
	mov esp, eax
.fi:
	pop esp
	mov edx, esp
	mov esp, [saved_esp]
	CKFAULT 'pf pop esp'
	CK 'pf pop esp after', edx, [pgz]
	ret

case_pf_o16:                         ; o16 push/pop faults, in-page
	call reset_pages
	PGADDR eax, 2
	add eax, 800h
	mov [t_esp0], eax
	lea edx, [eax-2]
	mov [x_cr2], edx
	mov dword [x_err], 2
	mov dword [x_eip], .fi
	mov [x_esp], eax
	UNMAP 2
	mov [saved_esp], esp
	mov ebx, 0a1b2c3d4h
	mov esp, eax
.fi:
	push bx
	mov edx, esp
	mov esp, [saved_esp]
	CKFAULT 'pf o16 push'
	mov ebx, [t_esp0]
	sub ebx, 2
	CK 'pf o16 push esp after', edx, ebx
	CKM16 'pf o16 push mem', ebx, 0c3d4h
	; o16 pop fault, upper half of ECX preserved
	call reset_pages
	PGADDR eax, 2
	add eax, 800h
	mov word [es:eax], 5678h
	mov [x_cr2], eax
	mov dword [x_err], 0
	mov dword [x_eip], .fi2
	mov [x_esp], eax
	UNMAP 2
	mov ecx, 0aabbccddh
	mov esp, eax
.fi2:
	pop cx
	mov edx, esp
	mov esp, [saved_esp]
	CKFAULT 'pf o16 pop'
	CK 'pf o16 pop ecx@fault', [frec+16], 0aabbccddh
	CK 'pf o16 pop ecx', ecx, 0aabb5678h
	mov ebx, [t_esp0]
	add ebx, 2
	CK 'pf o16 pop esp', edx, ebx
	ret

; 5. page-crossing pushes/pops where one page is not present.
; Baseline writes/reads byte-wise (mem_unaligned*_checked), so bytes on the
; present page are written before the fault. Real hardware writes nothing
; (SDM: faulting access has no architectural effect); recorded as observation.
case_pf_cross:
	; push dword, second (higher) page missing: bytes pg2-2, pg2-1 present
	call reset_pages
	PGADDR eax, 2
	add eax, 2
	mov [t_esp0], eax
	PGADDR edx, 2
	mov [x_cr2], edx
	mov dword [x_err], 2
	mov dword [x_eip], .fi1
	mov [x_esp], eax
	mov [snap_ptr], edx
	sub dword [snap_ptr], 4
	UNMAP 2
	mov [saved_esp], esp
	mov ebx, 11223344h
	mov esp, eax
.fi1:
	push ebx
	mov edx, esp
	mov esp, [saved_esp]
	CKFAULT 'x push hi-miss'
	OBS 'x push hi-miss snap(obs)', [frec+24]
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'x push hi-miss esp', edx, ebx
	CKM 'x push hi-miss mem', ebx, 11223344h
	inc dword [idx]
	; push dword, first (lower) page missing
	call reset_pages
	PGADDR eax, 2
	add eax, 2
	mov [t_esp0], eax
	PGADDR edx, 2
	sub edx, 2
	mov [x_cr2], edx
	mov dword [x_err], 2
	mov dword [x_eip], .fi2
	mov [x_esp], eax
	PGADDR edx, 2
	mov [snap_ptr], edx
	UNMAP 1
	mov [saved_esp], esp
	mov ebx, 11223344h
	mov esp, eax
.fi2:
	push ebx
	mov edx, esp
	mov esp, [saved_esp]
	CKFAULT 'x push lo-miss'
	OBS 'x push lo-miss snap(obs)', [frec+24]
	mov ebx, [t_esp0]
	sub ebx, 4
	CK 'x push lo-miss esp', edx, ebx
	CKM 'x push lo-miss mem', ebx, 11223344h
	inc dword [idx]
	; pop dword, second page missing
	call reset_pages
	PGADDR eax, 2
	sub eax, 2
	mov [t_esp0], eax
	mov dword [es:eax], 0
	mov byte [es:eax], 0aah
	mov byte [es:eax+1], 0bbh
	PGADDR edx, 2
	mov [x_cr2], edx
	mov dword [x_err], 0
	mov dword [x_eip], .fi3
	mov [x_esp], eax
	mov [snap_ptr], edx
	sub dword [snap_ptr], 4
	UNMAP 2
	mov [saved_esp], esp
	mov ecx, 0deadbeefh
	mov esp, eax
.fi3:
	pop ecx
	mov edx, esp
	mov esp, [saved_esp]
	CKFAULT 'x pop hi-miss'
	CK 'x pop hi-miss ecx@fault', [frec+16], 0deadbeefh
	; mem at pg2 now mapped by handler, its first two bytes are the A5 fill
	mov ebx, [t_esp0]
	add ebx, 4
	CK 'x pop hi-miss esp', edx, ebx
	OBS 'x pop hi-miss ecx(obs)', ecx
	inc dword [idx]
	; o16 push, word crossing, second page missing: cr2 = pg2
	call reset_pages
	PGADDR eax, 2
	inc eax
	mov [t_esp0], eax
	PGADDR edx, 2
	mov [x_cr2], edx
	mov dword [x_err], 2
	mov dword [x_eip], .fi4
	mov [x_esp], eax
	mov [snap_ptr], edx
	sub dword [snap_ptr], 4
	UNMAP 2
	mov [saved_esp], esp
	mov ebx, 0a1b2c3d4h
	mov esp, eax
.fi4:
	push bx
	mov edx, esp
	mov esp, [saved_esp]
	CKFAULT 'x push16 hi-miss'
	OBS 'x push16 snap(obs)', [frec+24]
	mov ebx, [t_esp0]
	sub ebx, 2
	CKM16 'x push16 mem', ebx, 0c3d4h
	ret

; 6. live registers and flags across pushes/pops, in-page and page-crossing
case_live:
.lp:
	call reset_pages
	PGADDR eax, 2
	mov ebx, [idx]
	add eax, [soff+ebx*4]
	mov [t_esp0], eax
	mov [saved_esp], esp
	mov esp, eax
	mov eax, 01010101h
	mov ecx, 02020202h
	mov edx, 03030303h
	mov ebx, 04040404h
	mov ebp, 05050505h
	mov esi, 06060606h
	mov edi, 07070707h
	cmp eax, 1                 ; NZ NC NS NO, PF=1, AF=0
	push eax
	push ecx
	push edx
	push ebx
	push ebp
	push esi
	push edi
	pop eax
	pop ecx
	pop edx
	pop ebx
	pop ebp
	pop esi
	pop edi
	mov [r_eax], eax
	mov [r_ecx], ecx
	mov [r_edx], edx
	mov [r_ebx], ebx
	mov [r_ebp], ebp
	mov [r_esi], esi
	mov [r_edi], edi
	mov [r_esp], esp
	lahf
	mov [r_ah], ah
	seto al
	mov [r_of], al
	mov esp, [saved_esp]
	CK 'live eax', [r_eax], 07070707h
	CK 'live ecx', [r_ecx], 06060606h
	CK 'live edx', [r_edx], 05050505h
	CK 'live ebx', [r_ebx], 04040404h
	CK 'live ebp', [r_ebp], 03030303h
	CK 'live esi', [r_esi], 02020202h
	CK 'live edi', [r_edi], 01010101h
	CK 'live esp', [r_esp], [t_esp0]
	movzx eax, byte [r_ah]
	CK 'live flags ah', eax, 06h
	movzx eax, byte [r_of]
	CK 'live of', eax, 0
	; memory content: the 7 dwords hold the pushed values (lowest = edi)
	mov esi, [t_esp0]
	sub esi, 28
	CKM 'live mem[0] edi', esi, 07070707h
	add esi, 12
	CKM 'live mem[3] ebx', esi, 04040404h
	add esi, 12
	CKM 'live mem[6] eax', esi, 01010101h
	inc dword [idx]
	cmp dword [idx], 5
	jb .lp
	ret

; 7. 16-bit stack segment (B=0) in 32-bit code: SP wraps through the mask.
; SDM: with B=0 only SP is used; a word access at offset FFFF (or dword at
; FFFD..FFFF) violates the segment limit (#SS). DOSBox performs no limit
; check, so those cases are baseline observations, not SDM expectations.
%macro S16 3        ; name, initial SP, ops
	call s16_prep
	mov [saved_esp], esp
	mov ecx, 0aabbccddh
	mov ebx, 0a1b2c3d4h
	mov ax, SEL_SS16
	mov ss, ax
	mov esp, 77770000h+(%2)
	%3
	mov edx, esp
	mov ax, SEL_FLAT
	mov ss, ax
	mov esp, [saved_esp]
	%strcat s16n %1,' esp'
	OBS s16n, edx
	%strcat s16n %1,' ecx'
	OBS s16n, ecx
	PGADDR esi, 0
	add esi, 0fffch
	%strcat s16n %1,' m[FFFC]'
	OBSM s16n, esi
	add esi, 4
	%strcat s16n %1,' m[10000]'
	OBSM s16n, esi
	PGADDR esi, 0
	%strcat s16n %1,' m[0]'
	OBSM s16n, esi
%endmacro

s16_prep:
	call reset_pages
	pushad
	mov eax, 15
	call fill_page
	mov eax, 16
	call fill_page
	mov eax, [tp]
	mov dword [es:eax+0fff8h], 44332211h
	mov dword [es:eax+0fffch], 88776655h
	mov dword [es:eax+10000h], 0bbaa99ffh
	mov dword [es:eax], 0ddccbba9h
	mov dword [es:eax+4], 0f1e0cebdh
	popad
	ret

case_s16:
	S16 's16 push16 sp2', 2, push bx
	S16 's16 push16 sp1', 1, push bx
	S16 's16 push16 sp0', 0, push bx
	S16 's16 push32 sp4', 4, push ebx
	S16 's16 push32 sp2', 2, push ebx
	S16 's16 push32 sp0', 0, push ebx
	S16 's16 pop16 spFFFE', 0fffeh, pop cx
	S16 's16 pop16 spFFFF', 0ffffh, pop cx
	S16 's16 pop32 spFFFE', 0fffeh, pop ecx
	S16 's16 pop32 spFFFC', 0fffch, pop ecx
	S16 's16 pop32 spFFFD', 0fffdh, pop ecx
	S16 's16 push sp(66)', 2, push sp
	S16 's16 push esp', 4, push esp
	ret

case_s16call:
	; CALL/RET through the 16-bit stack
	call s16_prep
	mov [saved_esp], esp
	mov ax, SEL_SS16
	mov ss, ax
	mov esp, 77770002h
	call .t
.r:
	mov esi, esp
	jmp .d
.t:
	mov edx, esp
	ret
.d:
	mov ax, SEL_FLAT
	mov ss, ax
	mov esp, [saved_esp]
	OBS 's16 call esp', edx
	OBS 's16 ret esp', esi
	PGADDR esi, 0
	add esi, 0fffch
	OBSM 's16 call m[FFFC]', esi
	ret

; 8. CPL3: user push/pop on a supervisor page and on a non-present page
%macro R3EPISODE 1  ; entry label
	mov dword [r3_entry], %1
	jmp enter_ring3
%endmacro

case_ring3_push:                    ; U write to supervisor page: error 7
	call reset_pages
	PGADDR eax, 3
	mov ecx, [tp]
	mov [r3_esp], eax
	add dword [r3_esp], 800h
	mov edx, [ptab]
	mov ebx, eax
	shr ebx, 12
	and dword [es:edx+ebx*4], 0fffff000h
	or dword [es:edx+ebx*4], 3         ; present, writable, supervisor
	invlpg [es:eax]
	mov eax, [r3_esp]
	lea edx, [eax-4]
	mov [x_cr2], edx
	mov dword [x_err], 7
	mov dword [x_eip], r3_push_fi
	mov [x_esp], eax
	R3EPISODE r3_push
r3_back_push:
	CKFAULT 'r3 push'
	mov ebx, [r3_esp]
	sub ebx, 4
	CK 'r3 push esp after', [r3_ecx], ebx
	CKM 'r3 push mem', ebx, 13572468h
	ret

case_ring3_pop:                     ; U read of supervisor page: error 5
	call reset_pages
	PGADDR eax, 3
	mov [r3_esp], eax
	add dword [r3_esp], 800h
	mov edx, [r3_esp]
	mov dword [es:edx], 0c0ffee11h
	mov edx, [ptab]
	mov ebx, eax
	shr ebx, 12
	and dword [es:edx+ebx*4], 0fffff000h
	or dword [es:edx+ebx*4], 3
	invlpg [es:eax]
	mov eax, [r3_esp]
	mov [x_cr2], eax
	mov dword [x_err], 5
	mov dword [x_eip], r3_pop_fi
	mov [x_esp], eax
	R3EPISODE r3_pop
r3_back_pop:
	CKFAULT 'r3 pop'
	CK 'r3 pop eax@fault', [frec+20], 0deadbeefh
	CK 'r3 pop eax', [r3_eax], 0c0ffee11h
	mov ebx, [r3_esp]
	add ebx, 4
	CK 'r3 pop esp after', [r3_ecx], ebx
	ret

case_ring3_np:                      ; U write to non-present page: error 6
	call reset_pages
	PGADDR eax, 3
	mov [r3_esp], eax
	add dword [r3_esp], 800h
	UNMAP 3
	mov eax, [r3_esp]
	lea edx, [eax-4]
	mov [x_cr2], edx
	mov dword [x_err], 6
	mov dword [x_eip], r3_push_fi
	mov [x_esp], eax
	R3EPISODE r3_push
r3_back_np:
	CKFAULT 'r3 np push'
	mov ebx, [r3_esp]
	sub ebx, 4
	CK 'r3 np esp after', [r3_ecx], ebx
	CKM 'r3 np mem', ebx, 13572468h
	ret

enter_ring3:
	mov [saved_esp], esp
	mov ax, SEL_DATAU
	mov ds, ax
	mov ax, SEL_FLATU
	mov es, ax
	push dword SEL_FLATU
	push dword [r3_esp]
	push dword 2
	push dword SEL_CODEU
	push dword [r3_entry]
	iretd

r3_push:
	mov ebx, 13572468h
r3_push_fi:
	push ebx
	mov ecx, esp
	int 80h

r3_pop:
	mov eax, 0deadbeefh
r3_pop_fi:
	pop eax
	mov ecx, esp
	int 80h

int80_handler:                      ; ring 0, stack from TSS
	mov [r3_eax_tmp], eax
	mov ax, SEL_DATA
	mov ds, ax
	mov ax, SEL_FLAT
	mov es, ax
	mov [r3_ecx], ecx
	mov eax, [r3_eax_tmp]
	mov [r3_eax], eax
	mov esp, [saved_esp]
	mov eax, [case_ptr]
	mov eax, [eax]
	cmp eax, case_ring3_push
	je r3_back_push
	cmp eax, case_ring3_pop
	je r3_back_pop
	jmp r3_back_np

; ---------------------------------------------------------------- handlers
; #PF task: error code is on its stack; faulting task state is in tss_main.
pf_task:
	pop eax
	mov edi, [faults]
	cmp edi, 4
	jae .give_up
	shl edi, 5
	add edi, frec
	mov [edi], eax
	mov eax, cr2
	mov [edi+4], eax
	mov eax, [tss_main+20h]
	mov [edi+8], eax
	mov eax, [tss_main+38h]
	mov [edi+12], eax
	mov eax, [tss_main+2ch]
	mov [edi+16], eax
	mov eax, [tss_main+28h]
	mov [edi+20], eax
	mov eax, [snap_ptr]
	test eax, eax
	jz .nosnap
	mov eax, [es:eax]
	mov [edi+24], eax
.nosnap:
	inc dword [faults]
	mov eax, cr2
	shr eax, 12
	cmp eax, 1024
	jae .give_up
	mov edx, [ptab]
	or dword [es:edx+eax*4], 7
	mov eax, cr2
	invlpg [es:eax]
	iretd
	jmp pf_task
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
	mov bx, records
	xor di, di                  ; fail count
.print_loop:
	cmp bx, [rec_ptr_lo]
	jae .exit
	mov ax, [bx]
	mov cx, 33
	mul cx
	add ax, names
	mov dx, ax
	mov ah, 9
	int 21h
	mov dl, ' '
	mov ah, 2
	int 21h
	mov dl, '['
	int 21h
	mov al, [bx+3]
	add al, '0'
	mov dl, al
	int 21h
	mov dl, ']'
	int 21h
	mov dx, msg_got
	mov ah, 9
	int 21h
	mov eax, [bx+4]
	call print_hex32
	cmp byte [bx+2], 1
	je .obs
	mov dx, msg_exp
	mov ah, 9
	int 21h
	mov eax, [bx+8]
	call print_hex32
	mov dx, msg_ok
	mov eax, [bx+4]
	cmp eax, [bx+8]
	je .res
	mov dx, msg_fail
	inc di
.res:
	mov ah, 9
	int 21h
	jmp .nl
.obs:
	mov dx, msg_obs
	mov ah, 9
	int 21h
.nl:
	call crlf
	add bx, 12
	jmp .print_loop
.exit:
	mov dx, msg_end
	mov ah, 9
	int 21h
	movzx eax, di
	call print_hex32
	mov dx, msg_checks
	mov ah, 9
	int 21h
	movzx eax, word [nchecks]
	call print_hex32
	call crlf
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

; ---------------------------------------------------- real-mode stack tests
; Dynamic core in real mode: 16-bit code, SS = scratch segment, SP near 0.
; 8086/286 behaviour for SP=1 differs (SDM: shutdown/#SS); baseline recorded.
%macro OBS16 3      ; name, suffix, 32-bit source
	%strcat o16n %1,%2
	%assign ck_id ck_n
	NAMEREG o16n
	push eax
	push dx
	mov eax, %3
	mov dx, ck_id
	call obs16
	pop dx
	pop eax
%endmacro

%macro R16 3        ; name, initial SP, ops
	call rm_prep
	mov [rsave_esp], esp
	mov [rsave_ss], ss
	mov ecx, 0aabbccddh
	mov ebx, 0a1b2c3d4h
	mov ax, [rm_seg]
	mov ss, ax
	mov esp, %2
	%3
	mov edx, esp
	mov ss, [rsave_ss]
	mov esp, [rsave_esp]
	OBS16 %1,' esp', edx
	OBS16 %1,' ecx', ecx
	mov ax, [rm_seg]
	mov fs, ax
	add ax, 1000h
	mov gs, ax
	OBS16 %1,' m[FFFC]', [fs:0fffch]
	OBS16 %1,' m[10000]', [gs:0]
	OBS16 %1,' m[0]', [fs:0]
%endmacro

obs16:                          ; eax=value dx=id
	push bx
	mov bx, [rec_ptr]
	mov [bx], dx
	mov byte [bx+2], 1
	mov byte [bx+3], 0
	mov [bx+4], eax
	mov dword [bx+8], 0
	add word [rec_ptr], 12
	pop bx
	ret

rm_prep:
	push eax
	push es
	mov ax, [rm_seg]
	mov es, ax
	mov dword [es:0fff8h], 44332211h
	mov dword [es:0fffch], 88776655h
	mov dword [es:0], 0ddccbba9h
	mov dword [es:4], 0f1e0cebdh
	add ax, 1000h
	mov es, ax
	mov dword [es:0], 0bbaa99ffh
	pop es
	pop eax
	ret

real_tests:
	R16 'rm push16 sp2', 2, push bx
	R16 'rm push16 sp1', 1, push bx
	R16 'rm push16 sp0', 0, push bx
	R16 'rm push32 sp4', 4, push ebx
	R16 'rm push32 sp2', 2, push ebx
	R16 'rm push32 sp1', 1, push ebx
	R16 'rm push32 sp0', 0, push ebx
	R16 'rm pop16 spFFFE', 0fffeh, pop cx
	R16 'rm pop16 spFFFF', 0ffffh, pop cx
	R16 'rm pop32 spFFFE', 0fffeh, pop ecx
	R16 'rm pop32 spFFFC', 0fffch, pop ecx
	R16 'rm pop32 spFFFD', 0fffdh, pop ecx
	R16 'rm push sp', 2, push sp
	; call/ret near
	call rm_prep
	mov [rsave_esp], esp
	mov [rsave_ss], ss
	mov ax, [rm_seg]
	mov ss, ax
	mov esp, 2
	call .t
.r:
	mov esi, esp
	jmp .d
.t:
	mov edx, esp
	ret
.d:
	mov ss, [rsave_ss]
	mov esp, [rsave_esp]
	OBS16 'rm call esp',' ', edx
	OBS16 'rm ret esp',' ', esi
	ret

; ------------------------------------------------------------------- data
align 4
case_table:
	dd case_p32, case_misc, case_o16
	dd case_pf_push, case_pf_pop, case_pf_call, case_pf_ret, case_pf_popesp
	dd case_pf_o16, case_pf_cross
	dd case_live, case_s16, case_s16call
	dd case_ring3_push, case_ring3_pop, case_ring3_np, 0
%if (stubs_end - stubs) != 32*stub_size
%error stub size mismatch
%endif
msg_got db ' got=$'
msg_exp db ' exp=$'
msg_ok db ' OK$'
msg_fail db ' FAIL$'
msg_obs db ' OBS$'
msg_unexp db 'UNEXPECTED_EXCEPTION $'
msg_end db 'STACKOPS fails=$'
msg_checks db ' checks=$'
soff dd 8, 9, 10, 11, 800h
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
gdt_tssm: dw 67h, 0
	db 0, 089h, 0, 0
gdt_tssp: dw 67h, 0
	db 0, 089h, 0, 0
gdt_ss16: dw 0ffffh, 0
	db 0, 092h, 0, 0
gdt_pad40: dq 0
gdt_codeu: dw 0ffffh, 0
	db 0, 0fah, 0cfh, 0
gdt_flatu: dq 00cff2000000ffffh
gdt_datau: dw 0ffffh, 0
	db 0, 0f2h, 0cfh, 0
gdt_end:
gdtr: dw gdt_end-gdt-1
	dd 0
idtr: dw 81h*8-1
	dd 0
old_idtr: times 6 db 0
base dd 0
pdir dd 0
ptab dd 0
tp dd 0
idx dd 0
case_ptr dd 0
saved_esp dd 0
stk0_lin dd 0
t_esp0 dd 0
pgz dd 0
pushsrc dd 0
popdst dd 0
calltgt dd 0
faults dd 0
snap_ptr dd 0
x_cr2 dd 0
x_err dd 0
x_eip dd 0
x_esp dd 0
frec times 32 dd 0               ; 4 records * 32 bytes
r_eax dd 0
r_ecx dd 0
r_edx dd 0
r_ebx dd 0
r_ebp dd 0
r_esi dd 0
r_edi dd 0
r_esp dd 0
r_ah db 0
r_of db 0
align 4
r3_esp dd 0
r3_entry dd 0
r3_ecx dd 0
r3_eax dd 0
r3_eax_tmp dd 0
rsave_esp dd 0
rsave_ss dw 0
rm_seg dw 0
rec_ptr: dd records
rec_ptr_lo equ rec_ptr
save_ss dw 0
save_sp dw 0
unexpected db 0ffh
align 4
unexp_w0 dd 0
unexp_w1 dd 0
align 8
idt: times 81h*8 db 0
tss_main: times 68h db 0
tss_pf: times 68h db 0
times 2048 db 0
stack_top:
times 2048 db 0
ring0_stack_top:
times 2048 db 0
pf_stack_top:

; names table (33 bytes per entry: 32 chars + '$')
names:
%assign i 0
%rep ck_n
	%strlen nl CKNAME_%[i]
	db CKNAME_%[i]
	times 32-nl db ' '
	db '$'
%assign i i+1
%endrep
nchecks dw ck_n

section .bss nobits
records resb 16384
pool resb 28*4096
scratch_bss resb 140000
