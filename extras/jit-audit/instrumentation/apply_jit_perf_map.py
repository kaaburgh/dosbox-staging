#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Insert the research-only JitPerfMap hooks into a DOSBox Staging checkout.

Usage: apply_jit_perf_map.py <checkout>. Build with
-DCMAKE_CXX_FLAGS=-DDOSBOX_JIT_PERF_MAP=1 to activate; without the define the
hooks compile to nothing.
"""
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
here = Path(__file__).resolve().parent
shutil.copy(here / 'jit_perf_map.h', root / 'src/cpu/jit_perf_map.h')

edits = [
    ('src/cpu/core_dyn_x86.cpp',
     '#include "dyn_cache.h"\n',
     '#include "dyn_cache.h"\n#ifdef DOSBOX_JIT_PERF_MAP\n#include "jit_perf_map.h"\n'
     'void JitPerfMap_Cleared(const uint8_t* native) { JitPerfMap::BlockCleared(native); }\n#endif\n'),
    ('src/cpu/dyn_cache.h',
     'void CacheBlock::Clear()\n{\n',
     '#ifdef DOSBOX_JIT_PERF_MAP\nvoid JitPerfMap_Cleared(const uint8_t* native);\n#endif\n'
     'void CacheBlock::Clear()\n{\n#ifdef DOSBOX_JIT_PERF_MAP\n\tJitPerfMap_Cleared(cache.start);\n#endif\n'),
    ('src/cpu/core_dyn_x86/decoder.h',
     'static CacheBlock * CreateCacheBlock(CodePageHandler * codepage,PhysPt start,Bitu max_opcodes) {\n\tBits i;\n',
     'static CacheBlock * CreateCacheBlock(CodePageHandler * codepage,PhysPt start,Bitu max_opcodes) {\n\tBits i;\n'
     '#ifdef DOSBOX_JIT_PERF_MAP\n\tconst auto jpm_cs = static_cast<uint16_t>(SegValue(cs));\n'
     '\tconst auto jpm_eip = static_cast<uint32_t>(reg_eip);\n#endif\n'),
    ('src/cpu/core_dyn_x86/decoder.h',
     '\tassert(decode.block->cache.size <= cache_bytes);\n',
     '\tassert(decode.block->cache.size <= cache_bytes);\n#ifdef DOSBOX_JIT_PERF_MAP\n'
     '\tJitPerfMap::BlockCreated(decode.block->cache.start,\n'
     '\t                         static_cast<uint32_t>(decode.block->cache.size),\n'
     '\t                         static_cast<uint32_t>(start),\n'
     '\t                         static_cast<uint32_t>(decode.code), jpm_cs, jpm_eip);\n#endif\n'),
]
for rel, old, new in edits:
    p = root / rel
    s = p.read_text()
    assert s.count(old) == 1, (rel, s.count(old))
    p.write_text(s.replace(old, new))
print('JitPerfMap hooks inserted')
