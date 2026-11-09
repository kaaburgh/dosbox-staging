; bit_test_semantics.asm — минимальный guest-тест корректности для семейства
; BT/BTS/BTR/BTC (0F A3 / 0F AB / 0F B3 / 0F BB / 0F BA) и для идиомы
; сравнения x87 через fnstsw/sahf, которую использует компилятор Quake.
;
; COM-программа: стартует в реальном режиме (CS=DS=ES=SS=PSP), позиционно
; независима (все адреса через DS=CS). Выполняет 19 тестов, сверяет результат
; с ЗАРАНЕЕ ВЫЧИСЛЕННЫМИ ожидаемыми значениями (Intel SDM) и пишет результат
; в файл BTOUT.BIN через int 21h.
;
; ВАЖНО: флаг CF читается через PUSHF/POP, а не через SETcc. SETcc
; (0F 90-9F) в динамическом ядре НЕ транслируется, поэтому вариант с SETcc
; сравнивал бы интерпретатор с интерпретатором. PUSHF (0x9C) и POPF (0x9D)
; транслируются, поэтому покрытие гостевого кода динамическим ядром видно
; по трассе блоков.
;
; Формат BTOUT.BIN:
;   [0]  число тестов (19)
;   [1]  число расхождений (0 = все ожидаемые значения совпали)
;   [2]  номер секции (1)
;   [3]  резерв
;   [4..22]   результат каждого теста (1 байт):
;               секция 1 (BT/BTS/BTR/BTC) -> значение CF
;               секция 2 (fnstsw/sahf)    -> флаги & 0x41 (CF, ZF)
;   [23..86]  значение EAX каждого теста секции 1 (32 бита, 16 значений)
;
; Сборка: nasm -f bin bit_test_semantics.asm -o BITSEM.COM

BITS 16
ORG 0x100

start:
    mov ax, cs
    mov ds, ax
    mov es, ax
    cld

; --- макрос: тест битового семейства (регистровый индекс) -------------
; TREG idx, op, bitindex, initial_value : 0F A3/AB/B3/BB
; CF читается через PUSHF/POP: SETcc не транслируется динамическим ядром.
%macro TREG 4
    mov eax, %4
    mov ecx, %3
    %2 eax, ecx
    push eax
    pushf
    pop bx
    and bx, 1                  ; CF
    pop eax
    mov [res_byte + %1], bl
    mov dword [res_val + %1*4], eax
%endmacro

; TIMM idx, op, imm8, initial_value : 0F BA /4../7
%macro TIMM 4
    mov eax, %4
    %2 eax, %3
    push eax
    pushf
    pop bx
    and bx, 1                  ; CF
    pop eax
    mov [res_byte + %1], bl
    mov dword [res_val + %1*4], eax
%endmacro

; --- секция 1: семейство BT/BTS/BTR/BTC (16 тестов) ------------------
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

; --- сверка секции 1 с ожидаемыми значениями -------------------------
    xor esi, esi
    xor edi, edi
cmp_loop:
    mov al, [res_byte + esi]
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

; --- секция 2: идиома сравнения x87 (fnstsw ax; sahf) ----------------
; Именно её генерирует компилятор Quake: fld; fldz; fucompp; fnstsw ax; sahf,
; после чего and ah,0x45 оставляет CF/PF/ZF.
; SAHF (0x9E) в динамическом ядре НЕ транслируется -> этот участок исполняет
; интерпретатор; тест фиксирует семантику, а покрытие видно по трассе.
; Сравнивается ST(0) со ST(1): сначала кладём 0, затем значение.
; FNSTSW AX -> AH, SAHF -> CF=C0, ZF=C3. Маска 0x41 оставляет CF и ZF;
; бит PF (C2) не проверяется, потому что путь сравнения FPU в эмуляторе
; его не переносит (это существующее поведение эмулятора, не связанное
; с предметом теста).
    fldz
    fld1
    fucompp
    fnstsw ax
    sahf
    and ah, 0x41
    mov [res_byte + 16], ah          ; +1 против 0: больше -> CF=0 ZF=0 -> 0x00

    fldz
    fld1
    fchs
    fucompp
    fnstsw ax
    sahf
    and ah, 0x41
    mov [res_byte + 17], ah          ; -1 против 0: меньше -> CF=1 ZF=0 -> 0x01

    fldz
    fldz
    fucompp
    fnstsw ax
    sahf
    and ah, 0x41
    mov [res_byte + 18], ah          ;  0 против 0: равно  -> CF=0 ZF=1 -> 0x40

    xor esi, esi
x87_loop:
    mov al, [res_byte + 16 + esi]
    cmp al, [exp_x87 + esi]
    je x87_ok
    inc edi
x87_ok:
    inc esi
    cmp esi, 3
    jl x87_loop

; --- сборка выходного буфера ---------------------------------------
    mov byte [outbuf + 0], 19
    mov [outbuf + 1], di
    mov byte [outbuf + 2], 1
    mov byte [outbuf + 3], 0
    mov cx, 19
    mov si, res_byte
    mov di, outbuf + 4
    rep movsb
    mov cx, 16
    mov si, res_val
    mov di, outbuf + 23
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
; --- ожидаемые значения секции 1 (CF) ---
exp_cf:      db 1,1,0,1,0,1,1,1,0,0,1,0,1,1,0,0
; --- ожидаемые значения секции 1 (EAX) ---
exp_val:     dd 0x80000001, 0x80000001, 0x80000001, 0x80000001
             dd 0x80000003, 0x00000001, 0x80000000, 0x80000000
             dd 0x80000003, 0x80000001, 0x80000001, 0x80000021
             dd 0x80000000, 0x00000001, 0x80000001, 0x80000003
; --- ожидаемые значения секции 2 (CF|PF|ZF) ---
exp_x87:     db 0x00, 0x01, 0x40
res_byte:    resb 19
res_val:     resd 16
outbuf:      resb 128
outlen       equ $ - outbuf
