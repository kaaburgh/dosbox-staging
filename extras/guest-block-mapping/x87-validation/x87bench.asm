bits 16
org 0x100
start:
 push cs
 pop ds
 fninit
 fldz
 mov ecx,20000000
.loop:
 fadd dword [one]
 fmul dword [one]
 dec ecx
 jnz .loop
 fstp dword [answer]
 cmp dword [answer],0x4b989680
 jne fail
 mov dx,okname
 jmp output
fail:mov dx,badname
output:
 mov ah,0x3c
 xor cx,cx
 int 0x21
 jc done
 mov bx,ax
 mov dx,payload
 mov cx,2
 mov ah,0x40
 int 0x21
 mov ah,0x3e
 int 0x21
done:mov ax,0x4c00
 int 0x21
one: dd 0x3f800000
answer: dd 0
okname: db 'X87.OK',0
badname: db 'X87.BAD',0
payload: db 'OK'
