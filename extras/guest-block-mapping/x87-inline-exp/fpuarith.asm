bits 16
org 0x100
start:
 push cs
 pop ds
 mov di,results
 mov bp,controls
 mov dx,4
.round_loop:
 mov bx,0
.operation_loop:
 mov si,values
 mov cx,8
.value_loop:
 fninit
 fldcw [bp]
 fld1
 cmp bx,0
 je .add
 cmp bx,1
 je .sub
 cmp bx,2
 je .mul
 fdiv dword [si]
 jmp .store
.add: fadd dword [si]
 jmp .store
.sub: fsub dword [si]
 jmp .store
.mul: fmul dword [si]
.store:
 fstp dword [di]
 fnstsw ax
 mov [di+4],ax
 add di,6
 add si,4
 loop .value_loop
 inc bx
 cmp bx,4
 jne .operation_loop
 add bp,2
 dec dx
 jnz .round_loop
 mov dx,filename
 mov ah,0x3c
 xor cx,cx
 int 0x21
 jc .fail
 mov bx,ax
 mov dx,results
 mov cx,768
 mov ah,0x40
 int 0x21
 jc .fail
 cmp ax,768
 jne .fail
 mov ah,0x3e
 int 0x21
 mov ax,0x4c00
 int 0x21
.fail:
 mov ax,0x4c01
 int 0x21
controls: dw 0x037f,0x077f,0x0b7f,0x0f7f
values: dd 0x3f800000,0xc0200000,0,0x80000000,1,0x7f800000,0x7fc12345,0x7f800001
filename: db 'ARITH.BIN',0
results: times 768 db 0
