#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Insert research-only JitCounters hooks into a DOSBox Staging checkout.

Usage: apply_jit_counters.py <checkout>. The hooks are active only when
DOSBOX_JIT_COUNTERS is defined (this script defines it at the top of
core_dyn_x86.cpp, so only that translation unit is affected).
"""
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
here = Path(__file__).resolve().parent
shutil.copy(here / 'jit_counters.h', root / 'src/cpu/jit_counters.h')

edits = [
    ('src/cpu/core_dyn_x86.cpp', '#include "dyn_cache.h"\n',
     '#include "dyn_cache.h"\n#define DOSBOX_JIT_COUNTERS 1\n#include "jit_counters.h"\n'),
    ('src/cpu/core_dyn_x86.cpp', 'restart_core:\n\tPhysPt ip_point=SegPhys(cs)+reg_eip;\n',
     'restart_core:\n\tJIT_COUNT(DispatchLoop);\n\tPhysPt ip_point=SegPhys(cs)+reg_eip;\n'),
    ('src/cpu/core_dyn_x86.cpp', '\tif (MakeCodePage(ip_point, chandler)) {\n',
     '\tJIT_COUNT(MakeCodePageCall);\n\tif (MakeCodePage(ip_point, chandler)) {\n'),
    ('src/cpu/core_dyn_x86.cpp', 'run_block:\n',
     'run_block:\n\tJIT_COUNT(RunBlock);\n'),
    ('src/cpu/core_dyn_x86.cpp',
     'static Bits sync_dh_fpu_and_run_normal_core() noexcept\n{\n',
     'static Bits sync_dh_fpu_and_run_normal_core() noexcept\n{\n\tJIT_COUNT(NormalCoreRun);\n'),
    ('src/cpu/core_dyn_x86.cpp', '\t/* Init the generator */\n\tgen_init();\n',
     '\t/* Init the generator */\n\tgen_init();\n\tJitCounters::Init();\n'),
    ('src/cpu/core_dyn_x86/risc_x64.h',
     "\t\tcache_addb(0x9c);\t\t//PUSHFQ\n\t\topcode(4).set64().setea(4,-1,0,-(CALLSTACK)).Emit8(0x8D); // lea rsp, [rsp-8/40]\n",
     "\t\tcache_addb(0x9c);\t\t//PUSHFQ\n\t\topcode(4).set64().setea(4,-1,0,-(CALLSTACK)).Emit8(0x8D); // lea rsp, [rsp-8/40]\n\t\tJIT_COUNT_EMIT(PushfqExec);\n"),
    ('src/cpu/core_dyn_x86/risc_x64.h',
     "\t\tcache_addb(0x9d);\t\t//POPFQ\n",
     "\t\tcache_addb(0x9d);\t\t//POPFQ\n\t\tJIT_COUNT_EMIT(PopfqExec);\n"),
    ('src/cpu/core_dyn_x86/decoder.h',
     '\tif (decode.big_op) {\n\t\tgen_call_function((void *)&mem_writed_checked,"%Drd%Dd",DREG(STACK),dynreg);\n',
     '\tJIT_COUNT_EMIT(PushHelper);\n\tif (decode.big_op) {\n\t\tgen_call_function((void *)&mem_writed_checked,"%Drd%Dd",DREG(STACK),dynreg);\n'),
    ('src/cpu/core_dyn_x86/decoder.h',
     '\tif (checked) {\n\t\tif (decode.big_op) {\n',
     '\tif (checked) {\n\t\tJIT_COUNT_EMIT(PopHelper);\n\t\tif (decode.big_op) {\n'),
    ('src/cpu/core_dyn_x86/decoder.h',
     'static CacheBlock * CreateCacheBlock(CodePageHandler * codepage,PhysPt start,Bitu max_opcodes) {\n\tBits i;\n',
     'static CacheBlock * CreateCacheBlock(CodePageHandler * codepage,PhysPt start,Bitu max_opcodes) {\n\tBits i;\n\tJIT_COUNT(CreateBlock);\n'),
    ('src/cpu/core_dyn_x86/dyn_fpu_dh.h',
     'static void FPU_FLD_32(PhysPt addr) {\n',
     'static void FPU_FLD_32(PhysPt addr) {\n\tJIT_COUNT(FpuFld32);\n'),
    ('src/cpu/core_dyn_x86/dyn_fpu_dh.h',
     'static void FPU_FST_32(PhysPt addr) {\n',
     'static void FPU_FST_32(PhysPt addr) {\n\tJIT_COUNT(FpuFst32);\n'),
    ('src/cpu/core_dyn_x86/dyn_fpu_dh.h',
     'static void FPU_FLD_64(PhysPt addr) {\n',
     'static void FPU_FLD_64(PhysPt addr) {\n\tJIT_COUNT(FpuFld64);\n'),
]
for rel, old, new in edits:
    p = root / rel
    s = p.read_text()
    assert s.count(old) == 1, (rel, old[:60], s.count(old))
    p.write_text(s.replace(old, new))

# Slow paths of the inlined dyn_read_word/dyn_write_word: count in the C++
# helpers they call on a TLB miss. (Emitting a counter into the inline slow
# path would overflow the 8-bit short jump that skips it.)
p = root / 'src/cpu/core_dyn_x86/decoder.h'
s = p.read_text()
for fn, cid in (('static bool mem_readd_checked_dcx64(PhysPt address, uint32_t* dst) {\n', 'ReadSlowPath'),
                ('static bool mem_readw_checked_dcx64(PhysPt address, uint16_t* dst) {\n', 'ReadSlowPath'),
                ('static bool mem_writed_checked_dcx64(PhysPt address, Bitu val) {\n', 'WriteSlowPath'),
                ('static bool mem_writew_checked_dcx64(PhysPt address, Bitu val) {\n', 'WriteSlowPath')):
    assert s.count(fn) == 1, fn
    s = s.replace(fn, fn + f'\tJIT_COUNT({cid});\n')
p.write_text(s)
print('JitCounters hooks inserted')
