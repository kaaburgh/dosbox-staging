; Synthetic 386 protected-mode paging + #PF restart test for x87 dword reads.
bits 16
org 0x100
start:
 cli
 push cs
 pop ds
 push cs
 pop es
 sidt [old_idtr]
 mov ax,cs
 mov [real_ptr+2],ax
 movzx eax,ax
 shl eax,4
 mov [base],eax
 ; flat base-0 code/data GDT and IDT, residing in COM memory
 mov ebx,eax
 add eax,gdt
 mov [gdtr+2],eax
 mov eax,ebx
 add eax,idt
 mov [idtr+2],eax
 mov eax,ebx
 add eax,pm_entry
 mov [pm_ptr],eax
 mov eax,ebx
 add eax,pf_handler
 mov [idt+14*8],ax
 shr eax,16
 mov [idt+14*8+6],ax
 mov word [idt+14*8+2],0x08
 mov word [idt+14*8+4],0x8e00
 mov eax,ebx
 add eax,mf_handler
 mov [idt+16*8],ax
 shr eax,16
 mov [idt+16*8+6],ax
 mov word [idt+16*8+2],0x08
 mov word [idt+16*8+4],0x8e00
 ; Five contiguous aligned pages: directory, identity PT, target PT, data 0/1.
 mov eax,ebx
 add eax,buffers+4095
 and eax,0xfffff000
 mov [pd_phys],eax
 mov edx,eax
 add edx,4096
 mov [pt0_phys],edx
 add edx,4096
 mov [pt2_phys],edx
 add edx,4096
 mov [data_phys],edx
 mov edi,eax
 sub edi,ebx
 xor eax,eax
 mov cx,5120
 rep stosd
 ; identity-map first 4MB read/write
 mov edi,[pt0_phys]
 sub edi,ebx
 xor edx,edx
 mov cx,1024
.fill_identity:
 mov eax,edx
 or eax,3
 stosd
 add edx,4096
 loop .fill_identity
 ; PD[0] -> identity, PD[2] -> test PT
 mov edi,[pd_phys]
 sub edi,ebx
 mov eax,[pt0_phys]
 or eax,3
 mov [es:di],eax
 mov eax,[pt2_phys]
 or eax,3
 mov [es:di+8],eax
 ; Target pages carry physical frame numbers but start not present.
 mov edi,[pt2_phys]
 sub edi,ebx
 mov eax,[data_phys]
 or eax,2
 mov [es:di],eax
 add eax,4096
 mov [es:di+4],eax
 ; target dword 1.0 and another 1.0 crossing the page boundary
 mov edi,[data_phys]
 sub edi,ebx
 mov dword [es:di],0x3f800000
 mov word [es:di+4094],0
 mov word [es:di+4096],0x3f80
 lgdt [gdtr]
 lidt [idtr]
 mov eax,cr0
 or eax,1
 mov cr0,eax
 jmp dword far [pm_ptr]
bits 32
pm_entry:
 mov ax,0x10
 mov ds,ax
 mov es,ax
 mov ss,ax
 mov ebp,ebx
 mov esp,ebp
 add esp,0xf000
 mov eax,[ebp+pd_phys]
 mov cr3,eax
 mov eax,cr0
 or eax,0x80000000
 mov cr0,eax
 jmp short .flush
.flush:
 fninit
 ; Initial missing page, then mapped first-page / absent second-page read.
 fld dword [0x00800000]
 fstp dword [ebp+value1]
 fld dword [0x00800ffe]
 fstp dword [ebp+value2]
 ; Enable CR0.NE, then execute unmasked divide-by-zero and wait.
 mov eax,cr0
 or eax,0x20
 mov cr0,eax
 mov eax,cr0
 mov [ebp+observed_cr0],eax
 fninit
 fldcw [ebp+unmask_cw]
 fld1
 fdiv dword [ebp+zero]
 fwait
 fnstsw [ebp+mf_status]
 fnstcw [ebp+mf_cw]
 fninit
 mov eax,cr0
 and eax,0x7fffffff
 mov cr0,eax
 jmp short .paging_off
.paging_off:
 mov eax,cr0
 and eax,0xfffffffe
 mov cr0,eax
 jmp word far [ebp+real_ptr]
mf_handler:
 inc dword [ebp+mf_count]
 fnclex
 iretd
pf_handler:
 pushad
 mov eax,cr2
 mov ecx,[esp+32]
 mov edx,[ebp+fault_count]
 cmp edx,0
 je .first
 cmp edx,1
 je .second
 mov dword [ebp+bad],3
 jmp .resume
.first:
 mov [ebp+cr2_first],eax
 mov [ebp+err_first],ecx
 cmp eax,0x00800000
 jne .bad
 mov edi,[ebp+pt2_phys]
 mov eax,[ebp+data_phys]
 or eax,3
 mov [edi],eax
 jmp .flush
.second:
 mov [ebp+cr2_second],eax
 mov [ebp+err_second],ecx
 cmp eax,0x00801000
 jne .bad
 mov edi,[ebp+pt2_phys]
 mov eax,[ebp+data_phys]
 add eax,4096
 or eax,3
 mov [edi+4],eax
 jmp .flush
.bad:
 mov dword [ebp+bad],1
 ; make both pages available so the test exits rather than faulting forever
 mov edi,[ebp+pt2_phys]
 mov eax,[ebp+data_phys]
 or eax,3
 mov [edi],eax
 add eax,4096
 mov [edi+4],eax
.flush:
 mov eax,cr3
 mov cr3,eax
 inc dword [ebp+fault_count]
.resume:
 popad
 add esp,4
 iretd
bits 16
real_entry:
 mov ax,cs
 mov ds,ax
 mov es,ax
 mov ss,ax
 mov sp,0xf000
 lidt [old_idtr]
 sti
 mov dx,outfile
 mov ah,0x3c
 xor cx,cx
 int 0x21
 jc failed
 mov bx,ax
 mov dx,results
 mov cx,44
 mov ah,0x40
 int 0x21
 jc failed
 cmp ax,44
 jne failed
 mov ah,0x3e
 int 0x21
 mov ax,0x4c00
 int 0x21
failed:
 mov ax,0x4c01
 int 0x21
align 8
gdt: dq 0,0x00cf9a000000ffff,0x00cf92000000ffff
gdt_end:
gdtr: dw gdt_end-gdt-1
 dd 0
idtr: dw 256*8-1
 dd 0
old_idtr: times 6 db 0
pm_ptr: dd 0
 dw 8
real_ptr: dw real_entry,0
base: dd 0
pd_phys: dd 0
pt0_phys: dd 0
pt2_phys: dd 0
data_phys: dd 0
outfile: db 'PMMF.BIN',0
unmask_cw: dw 0x037b
zero: dd 0
align 4
results:
value1: dd 0
value2: dd 0
fault_count: dd 0
cr2_first: dd 0
cr2_second: dd 0
err_first: dd 0
err_second: dd 0
bad: dd 0
mf_count: dd 0
mf_status: dw 0
mf_cw: dw 0
observed_cr0: dd 0
align 8
idt: times 256*8 db 0
buffers: times 24576 db 0
