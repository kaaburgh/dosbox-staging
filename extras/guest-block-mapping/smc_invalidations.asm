; SPDX-License-Identifier: GPL-2.0-or-later
; Minimal 16-bit DOS COM program that repeatedly rewrites a code byte after
; it has been executed. Intended to exercise CacheBlock::Clear(), not Quake.
bits 16
org 0x100

start:
    push cs
    pop ds
    mov cx, 96
.repeat:
    mov byte [target], 0x90   ; NOP
    call target
    mov byte [target], 0x40   ; INC AX
    call target
    loop .repeat

    mov dx, outfile
    mov ah, 0x3c             ; DOS create/truncate
    xor cx, cx
    int 0x21
    jc .fail
    mov bx, ax
    mov dx, payload
    mov cx, payload_len
    mov ah, 0x40             ; DOS write
    int 0x21
    mov ah, 0x3e             ; DOS close
    int 0x21
    mov ax, 0x4c00           ; success
    int 0x21

.fail:
    mov ax, 0x4c01
    int 0x21

target:
    inc ax
    ret

outfile: db 'SMC.OK', 0
payload: db 'OK', 13, 10
payload_len equ $-payload
