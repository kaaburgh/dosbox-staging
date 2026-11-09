bits 16
org 100h
start:
 cli
 push cs
 pop ds
 mov [save_ss], ss
 mov ax,cs
 mov [real_jump+3],ax
 mov [save_sp], sp
 sidt [old_idtr]
 xor eax,eax
 mov ax,cs
 shl eax,4
 mov [base],eax
 ; Install the runtime base in both protected-mode code descriptors.
 mov [gdt_code32+2],ax
 mov [gdt_code16+2],ax
 shr eax,16
 mov [gdt_code32+4],al
 mov [gdt_code16+4],al
 mov [gdt_code32+7],ah
 mov [gdt_code16+7],ah
 mov eax,[base]
 add eax,gdt
 mov [gdtr+2],eax
 mov eax,[base]
 add eax,idt
 mov [idtr+2],eax
 mov word [idt+14*8], pf_handler
 mov word [idt+14*8+2], 8
 mov byte [idt+14*8+5], 08eh
 mov word [idt+14*8+6], 0
 lgdt [gdtr]
 mov eax,cr0
 or eax,1
 mov cr0,eax
 jmp dword 8:protected
bits 32
protected:
 mov ax,16
 mov ds,ax
 mov es,ax
 mov ss,ax
 movzx esp,word [cs:save_ss]
 shl esp,4
 movzx eax,word [cs:save_sp]
 add esp,eax
 ; A page directory, identity-mapping table, and two data pages.
 mov edi,[cs:base]
 add edi,pool
 add edi,4095
 and edi,0fffff000h
 mov [cs:directory],edi
 xor eax,eax
 mov ecx,2048
 rep stosd
 mov edi,[cs:directory]
 lea edx,[edi+4096]
 mov eax,edx
 or eax,3
 mov [edi],eax
 mov edi,edx
 mov eax,3
 mov ecx,1024
.fill:
 stosd
 add eax,4096
 loop .fill
 lea ebx,[edx+4096+4093]
 mov [cs:target],ebx
 mov dword [ebx],3fc00000h
 mov eax,ebx
 add eax,3
 shr eax,12
 mov [cs:pte_index],eax
 and dword [edx+eax*4],0fffffffeh
 lidt [cs:idtr]
 fninit
 fld dword [cs:sentinel]
 mov eax,[cs:directory]
 mov cr3,eax
 mov eax,cr0
 or eax,80000000h
 mov cr0,eax
 fadd dword [ebx]            ; must retry without corrupting previous x87 state
 fstp dword [cs:actual]
 mov eax,cr0
 and eax,7fffffffh
 mov cr0,eax
 cmp dword [cs:fault_count],1
 jne .bad
 cmp dword [cs:actual],40600000h
 jne .bad
 mov byte [cs:result],1
.bad:
 jmp word 24:pm16
pf_handler:
 pushad
 mov eax,cr2
 mov edx,[cs:target]
 add edx,3
 cmp eax,edx
 jne .unexpected
 inc dword [cs:fault_count]
 mov eax,[cs:directory]
 add eax,4096
 mov edx,[cs:pte_index]
 or dword [eax+edx*4],1
 mov eax,cr2
 invlpg [eax]
 popad
 add esp,4
 iretd
.unexpected:
 mov byte [cs:result],2
 hlt
 jmp .unexpected
bits 16
pm16:
 mov eax,cr0
 and eax,0fffffffeh
 mov cr0,eax
real_jump:
 jmp 0:real_segment
real_segment:
 ; Far jump segment is patched before use below.
 mov ax,cs
 mov ds,ax
 mov ax,[save_ss]
 mov ss,ax
 mov sp,[save_sp]
 lidt [old_idtr]
 sti
 cmp byte [result],1
 jne failed
 mov dx,pass_msg
 mov ah,9
 int 21h
 mov ax,4c00h
 int 21h
failed:
 mov dx,fail_msg
 mov ah,9
 int 21h
 mov ax,4c01h
 int 21h
align 8
gdt:
 dq 0
gdt_code32: dw 0ffffh,0
 db 0,09ah,040h,0
 dq 00cf92000000ffffh
gdt_code16: dw 0ffffh,0
 db 0,09ah,000h,0
gdt_end:
gdtr: dw gdt_end-gdt-1
 dd 0
idtr: dw 2047
 dd 0
old_idtr: times 6 db 0
base dd 0
directory dd 0
target dd 0
pte_index dd 0
fault_count dd 0
actual dd 0
actual2 dd 0
sentinel dd 40000000h
save_ss dw 0
save_sp dw 0
result db 0
pass_msg db 'FPU_ORACLE_PASS',13,10,'$'
fail_msg db 'FPU_ORACLE_FAIL',13,10,'$'
idt: times 2048 db 0
pool: times 24576 db 0
