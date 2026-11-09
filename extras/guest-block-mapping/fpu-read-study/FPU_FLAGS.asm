bits 16
org 100h
start:
 push cs
 pop ds
 fninit
 ; Calculate address ending at the last three bytes of a linear 4KiB page.
 mov ax, ds
 shl ax, 4
 add ax, buffer
 neg ax
 and ax, 0fffh
 mov bx, buffer
 add bx, ax
 add bx, 4093
 mov word [bx], 0000h
 mov word [bx+2], 3fc0h      ; +1.5, split over two pages
 mov word [bx+4], 0000h
 mov word [bx+6], 4010h      ; +2.25
 fld dword [bx]
 fstp dword [actual]
 cmp dword [actual], 3fc00000h
 jne fail
 fld dword [bx]
 fadd dword [bx+4]
 fstp dword [actual]
 cmp dword [actual], 40700000h ; 3.75
 jne fail
 fld dword [bx+4]
 fmul dword [bx]
 fstp dword [actual]
 cmp dword [actual], 40580000h ; 3.375
 jne fail
 fld dword [bx+4]
 fsub dword [bx]
 fstp dword [actual]
 cmp dword [actual], 3f400000h ; .75
 jne fail
 mov dword [bx], -7
 fild dword [bx]
 fstp dword [actual]
 cmp dword [actual], 0c0e00000h
 jne fail
 ; Repeated reads after data modification must use current bytes.
 mov dword [bx], 3f800000h
 fld dword [bx]
 mov dword [bx], 40000000h
 fadd dword [bx]
 fstp dword [actual]
 cmp dword [actual], 40400000h
 jne fail
 fnstcw [control]
 cmp word [control], 037fh
 jne fail
 ; SMC: rewrite an x87 memory displacement in a previously executed block.
 mov word [operand], val_one
 call patched_load
 fstp dword [actual]
 cmp dword [actual], 3f800000h
 jne fail
 mov word [operand], val_two
 call patched_load
 fstp dword [actual]
 cmp dword [actual], 40000000h
 jne fail
 ; VGA handler-backed memory read at a 4KiB crossing in mode 13h.
 mov ax, 0013h
 int 10h
 mov ax, 0a000h
 mov es, ax
 mov bx, 0ffdh
 mov dword [es:bx], 3fc00000h
 mov eax, [es:bx]
 cmp eax, 3fc00000h
 jne fail
 fld dword [es:bx]
 fstp dword [actual]
 cmp dword [actual], 3fc00000h
 jne fail
 mov ax, 0003h
 int 10h
 ; x87 instructions must preserve guest arithmetic flags.
 mov al, 095h
 sahf
 pushf
 pop dx
 fld dword [val_one]
 fadd dword [val_two]
 fstp dword [actual]
 pushf
 pop ax
 xor ax,dx
 test ax,08d5h
 jne fail
 cmp dword [actual],40400000h
 jne fail
 mov dx, pass_msg
 mov ah, 9
 int 21h
 mov ax, 4c00h
 int 21h
fail:
 mov dx, fail_msg
 mov ah, 9
 int 21h
 mov ax, 4c01h
 int 21h
patched_load:
 db 0d9h, 06h ; fld dword [disp16]
operand dw val_one
 ret
val_one dd 3f800000h
val_two dd 40000000h
pass_msg db 'FPU_ORACLE_PASS',13,10,'$'
fail_msg db 'FPU_ORACLE_FAIL',13,10,'$'
actual dd 0
control dw 0
buffer: times 8192 db 0
