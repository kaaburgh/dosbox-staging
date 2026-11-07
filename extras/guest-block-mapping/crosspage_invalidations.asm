; SPDX-License-Identifier: GPL-2.0-or-later
; 16-bit DOS program with a generated instruction straddling a 4 KiB
; physical page boundary, invalidated and recompiled after a code write.
bits 16
org 0x100

start:
    push cs
    pop ds

    ; Compute offset B so (CS*16 + B) mod 4096 == 4095.
    ; Keep B inside this COM allocation: 0x2000 <= B <= 0x2fff.
    mov ax, cs
    shl ax, 4
    and ax, 0x0fff
    mov bx, 0x0fff
    sub bx, ax
    add bx, 0x2000

    ; Assemble MOV AX,1234h; RET directly into writable DOS memory.
    ; The MOV immediate crosses the physical 4 KiB page boundary.
    mov byte [bx], 0xb8
    mov byte [bx+1], 0x34
    mov byte [bx+2], 0x12
    mov byte [bx+3], 0xc3

    call bx
    cmp ax, 0x1234
    jne .fail

    ; Overwrite the *next page* bytes of the guest instruction.
    mov byte [bx+1], 0x78
    mov byte [bx+2], 0x56
    call bx
    cmp ax, 0x5678
    jne .fail

    mov dx, outfile
    mov ah, 0x3c
    xor cx, cx
    int 0x21
    jc .fail
    mov bx, ax
    mov dx, payload
    mov cx, payload_len
    mov ah, 0x40
    int 0x21
    mov ah, 0x3e
    int 0x21
    mov ax, 0x4c00
    int 0x21
.fail:
    mov ax, 0x4c01
    int 0x21
outfile: db 'XPG.OK', 0
payload: db 'OK', 13, 10
payload_len equ $ - payload

; Reserve backing bytes for the runtime-generated code.
times (0x3100 - ($-$$)) db 0
