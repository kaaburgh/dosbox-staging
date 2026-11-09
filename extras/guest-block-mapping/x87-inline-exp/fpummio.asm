bits 16
org 0x100
start:
 mov ax,0x0013
 int 0x10
 push cs
 pop ds
 mov ax,0xa000
 mov es,ax
 mov di,0x0ffd
 mov bp,3
.next_offset:
 mov si,values
 mov cx,4
.next_value:
 mov eax,[si]
 mov [es:di],eax
 fld dword [es:di]
 fstp dword [actual]
 cmp eax,[actual]
 jne fail
 add si,4
 loop .next_value
 inc di
 dec bp
 jnz .next_offset
 mov dx,okname
 jmp output
fail: mov dx,badname
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
done:
 mov ax,0x4c00
 int 0x21
values: dd 0x3f800000,0xc0200000,0x80000000,0x7f800000
actual: dd 0
okname: db 'MMIO.OK',0
badname: db 'MMIO.BAD',0
payload: db 'OK'
