; bit_test_semantics.asm — минимальный guest-тест корректности для семейства BT/BTS/BTR/BTC
; (0F A3 / 0F AB / 0F B3 / 0F BB / 0F BA), отсутствующего в dynrec-декодере DOSBox Staging.
;
; COM-программа: стартует в реальном режиме (CS=DS=ES=SS=PSP), позиционно
; независима (все адреса через DS=CS). Выполняет 16 тестов на 32-битном
; операнде, сверяет результат с ЗАРАНЕЕ ВЫЧИСЛЕННЫМИ ожидаемыми значениями
; (Intel SDM) и пишет результат в файл BTOUT.BIN через int 21h.
;
; Формат BTOUT.BIN:
;   [0]  число тестов (16)
;   [1]  число расхождений (0 = все ожидаемые значения совпали)
;   [2..3] резерв
;   [4..19]  CF каждого теста
;   [20..83] значение EAX каждого теста (32 бита)
;
; Сборка: nasm -f bin bit_test_semantics.asm -o BITSEM.COM
; Запуск: run_bit_test_semantics.py --dosbox <dosbox> --out <dir>

BITS 16
ORG 0x100

start:
    mov ax, cs
    mov ds, ax
    mov es, ax
    cld

; --- макросы тестов -------------------------------------------------
; TREG idx, op, bitindex, initial_value : регистровый индекс (0F A3/AB/B3/BB)
%macro TREG 4
    mov eax, %4
    mov ecx, %3
    %2 eax, ecx
    push eax
    setc al
    mov byte [res_cf + %1], al
    pop eax
    mov dword [res_val + %1*4], eax
%endmacro

; TIMM idx, op, imm8, initial_value : непосредственный индекс (0F BA /4../7)
%macro TIMM 4
    mov eax, %4
    %2 eax, %3
    push eax
    setc al
    mov byte [res_cf + %1], al
    pop eax
    mov dword [res_val + %1*4], eax
%endmacro

    TREG  0, bt, 0,   0x80000001   ; CF = бит 0  = 1
    TREG  1, bt, 31,  0x80000001   ; CF = бит 31 = 1
    TREG  2, bt, 1,   0x80000001   ; CF = бит 1  = 0
    TREG  3, bts, 0,  0x80000001   ; CF = 1, бит уже установлен
    TREG  4, bts, 1,  0x80000001   ; CF = 0, бит устанавливается
    TREG  5, btr, 31, 0x80000001   ; CF = 1, бит 31 сбрасывается
    TREG  6, btr, 0,  0x80000001   ; CF = 1, бит 0 сбрасывается
    TREG  7, btc, 0,  0x80000001   ; CF = 1, бит 0 инвертируется
    TREG  8, btc, 1,  0x80000001   ; CF = 0, бит 1 инвертируется
    TIMM  9, bt,  5,  0x80000001   ; CF = бит 5  = 0
    TIMM 10, bt,  31, 0x80000001   ; CF = бит 31 = 1
    TIMM 11, bts, 5,  0x80000001   ; CF = 0, бит 5 устанавливается
    TIMM 12, btr, 0,  0x80000001   ; CF = 1, бит 0 сбрасывается
    TIMM 13, btc, 31, 0x80000001   ; CF = 1, бит 31 инвертируется
    TREG 14, bt,  0x21, 0x80000001 ; индекс маскируется до 5 бит -> бит 1 = 0
    TREG 15, bts, 0x21, 0x80000001 ; индекс -> 1, бит устанавливается

; --- сверка с ожидаемыми значениями --------------------------------
    xor esi, esi
    xor edi, edi
cmp_loop:
    mov al, [res_cf + esi]
    cmp al, [exp_cf + esi]
    je cf_ok
    inc edi
cf_ok:
    mov eax, [res_val + esi*4]
    cmp eax, [exp_val + esi*4]
    je val_ok
    inc edi
val_ok:
    inc esi
    cmp esi, 16
    jl cmp_loop

; --- сборка выходного буфера ---------------------------------------
    mov byte [outbuf + 0], 16
    mov [outbuf + 1], di
    mov byte [outbuf + 2], 0
    mov byte [outbuf + 3], 0
    mov cx, 16
    mov si, res_cf
    mov di, outbuf + 4
    rep movsb
    mov cx, 16
    mov si, res_val
    mov di, outbuf + 20
    rep movsd

; --- запись файла через int 21h ------------------------------------
    mov ax, 0x3C00          ; создать/открыть файл (только запись)
    xor cx, cx
    mov dx, fname
    int 0x21
    mov bx, ax
    mov ah, 0x40             ; записать
    mov cx, outlen
    mov dx, outbuf
    int 0x21
    mov ah, 0x3E             ; закрыть
    int 0x21
    mov ax, 0x4C00          ; выход
    int 0x21
    hlt

; --- данные ---------------------------------------------------------
fname:       db 'BTOUT.BIN', 0
exp_cf:      db 1,1,0,1,0,1,1,1,0,0,1,0,1,1,0,0
exp_val:     dd 0x80000001, 0x80000001, 0x80000001, 0x80000001
             dd 0x80000003, 0x00000001, 0x80000000, 0x80000000
             dd 0x80000003, 0x80000001, 0x80000001, 0x80000021
             dd 0x80000000, 0x00000001, 0x80000001, 0x80000003
res_cf:      resb 16
res_val:     resd 16
outbuf:      resb 128
outlen       equ $ - outbuf
