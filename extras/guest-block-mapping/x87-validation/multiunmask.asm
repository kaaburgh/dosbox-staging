; Observe status/control and real-mode exception vectors; no assumptions about DOSBox support.
bits 16
org 0x100
start:
 push cs
 pop ds
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
 mov bx,0
 mov di,results
.case:
 fninit
 mov ax,[controls+bx]
 mov [temp_cw],ax
 fldcw [temp_cw]
 cmp bx,0
 jne .not_invalid
 fldz
 fdiv dword [zero]
 jmp .wait
.not_invalid:
 cmp bx,2
 jne .not_divzero
 fld1
 fdiv dword [zero]
 jmp .wait
.not_divzero:
 cmp bx,4
 jne .precision
 fld dword [maxfloat]
 fmul dword [maxfloat]
 fstp dword [scratch]
 jmp .wait
.precision:
 fld1
 fdiv dword [three]
.wait:
 fwait
 fnstsw [di]
 fnstcw [di+2]
 mov ax,[count10]
 mov [di+4],ax
 mov ax,[count75]
 mov [di+6],ax
 mov word [count10],0
 mov word [count75],0
 add di,8
 add bx,2
 cmp bx,8
 jne .case
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
 mov dx,results
 mov cx,32
 mov ah,0x40
 int 0x21
 jc fail
 cmp ax,32
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
count10: dw 0
count75: dw 0
controls: dw 0x037e,0x037b,0x0377,0x035f
temp_cw: dw 0
zero: dd 0
three: dd 0x40400000
maxfloat: dd 0x7f7fffff
scratch: dd 0
outfile: db 'MUNMASK.BIN',0
results: times 32 db 0
