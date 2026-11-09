bits 16
org 0x100
start:
 push cs
 pop ds
 finit
 ; Choose guest linear addresses ending ffd, ffe, fff, plus a same-page case.
 mov ax, cs
 shl ax, 4
 and ax, 0xfff
 mov bx, 0xffc
 sub bx, ax
 add bx, 0x3000
 mov bp, 4
.address_loop:
 mov si, values
 mov cx, 5
.value_loop:
 mov eax, [si]
 mov [bx], eax
 fld dword [bx]
 fstp dword [bx+8]
 cmp eax, [bx+8]
 jne fail
 fld dword [si]
 fstp dword [bx]
 cmp eax, [bx]
 jne fail
 add si, 4
 loop .value_loop
 inc bx
 dec bp
 jnz .address_loop
 fnstsw ax
 test ax, 1
 jnz fail
 mov dx, okname
 jmp output
fail:
 mov dx, badname
output:
 mov ah, 0x3c
 xor cx, cx
 int 0x21
 jc done
 mov bx, ax
 mov dx, payload
 mov cx, 2
 mov ah, 0x40
 int 0x21
 mov ah, 0x3e
 int 0x21
done:
 mov ax, 0x4c00
 int 0x21
values: dd 0x3f800000, 0xc0200000, 0x00000001, 0x80000000, 0x7f800000
okname: db 'FPU.OK',0
badname: db 'FPU.BAD',0
payload: db 'OK'
times (0x5000-($-$$)) db 0
