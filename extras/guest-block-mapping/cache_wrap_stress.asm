; SPDX-License-Identifier: GPL-2.0-or-later
; Validate guest semantics when the x86 dynamic JIT native cache wraps.
;
; 16,000 unique basic blocks each increment BX once and jump to the
; following block. Their generated native code exceeds a 1-MiB JIT cache.
; The program verifies BX == 16000 and writes WRAP.OK only on success.
; This is deterministic, requires no Quake assets, and does not modify
; guest code while it runs.
;
; Build: nasm -f bin cache_wrap_stress.asm -o WRAP.COM
bits 16
org 0x100

start:
    push cs
    pop ds
    xor bx, bx

%rep 16000
    inc bx
    db 0xeb, 0x00      ; JMP SHORT $+2, ends each guest basic block
%endrep

    cmp bx, 16000
    jne fail

    mov dx, outfile
    mov ah, 0x3c
    xor cx, cx
    int 0x21
    jc fail
    mov bx, ax
    mov dx, payload
    mov cx, payload_len
    mov ah, 0x40
    int 0x21
    mov ah, 0x3e
    int 0x21
    mov ax, 0x4c00
    int 0x21

fail:
    mov ax, 0x4c01
    int 0x21

outfile: db 'WRAP.OK', 0
payload: db 'OK', 13, 10
payload_len equ $ - payload
