bits 16
org 0x100
start:
 push cs
 pop ds
 ; Save and replace INT 10h (#MF in protected mode) and IRQ13's INT 75h.
 mov ax,0x3510
 int 0x21
 mov [old10],bx
 mov [old10+2],es
 mov ax,0x3575
 int 0x21
 mov [old75],bx
 mov [old75+2],es
 mov dx,handler10
 mov ax,0x2510
 int 0x21
 mov dx,handler75
 mov ax,0x2575
 int 0x21
 fninit
 fldcw [unmasked]
 fld1
 fdiv dword [zero]
 fwait
 fnstsw [status]
 fnstcw [control]
 ; Restore vectors before using DOS or exiting.
 push ds
 lds dx,[old10]
 mov ax,0x2510
 int 0x21
 pop ds
 push ds
 lds dx,[old75]
 mov ax,0x2575
 int 0x21
 pop ds
 fninit
 mov dx,outfile
 mov ah,0x3c
 xor cx,cx
 int 0x21
 jc fail
 mov bx,ax
 mov dx,result
 mov cx,8
 mov ah,0x40
 int 0x21
 jc fail
 cmp ax,8
 jne fail
 mov ah,0x3e
 int 0x21
 mov ax,0x4c00
 int 0x21
fail: mov ax,0x4c01
 int 0x21
handler10:
 inc word [cs:count10]
 fnclex
 iret
handler75:
 inc word [cs:count75]
 fnclex
 mov al,0x20
 out 0xa0,al
 out 0x20,al
 iret
old10: dd 0
old75: dd 0
unmasked: dw 0x037b
zero: dd 0
outfile: db 'UNMASK.BIN',0
result:
status: dw 0
control: dw 0
count10: dw 0
count75: dw 0
