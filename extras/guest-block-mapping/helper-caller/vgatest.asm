bits 16
org 0x100
start:
 mov ax,0x0013
 int 0x10
 push cs
 pop ds
 mov ax,0xa000
 mov es,ax
 mov bp,256
.copy_loop:
 mov esi, source
 mov edi, 0x0ffd
 mov ecx, 8
 a32 rep movsd
 dec bp
 jnz .copy_loop
 mov si,source
 mov di,0x0ffd
 mov cx,8
.compare_loop:
 mov eax,[si]
 cmp eax,[es:di]
 jne fail
 add si,4
 add di,4
 loop .compare_loop
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
source: dd 0x01234567,0x89abcdef,0x10203040,0x50607080,0xdeadbeef,0xa5a5a5a5,0x5a5a5a5a,0xff00ff00
okname: db 'VGA.OK',0
badname: db 'VGA.BAD',0
payload: db 'OK'
