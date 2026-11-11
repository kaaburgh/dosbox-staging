#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 validation runner for the x86-64 dynamic core experiments.

One command builds the Phase 2 variants of DOSBox Staging, runs the guest
correctness tests on the normal and dynamic cores, runs the interleaved
Quake 1.06 benchmark with an A/A placebo, checks determinism, and packs
sanitised CSV/JSON/text results into a ZIP for independent review.

Supported hosts: Linux x86-64 (verified) and Windows x86-64 (implemented;
see README). On other architectures the code under test (core_dyn_x86) is not
compiled at all, so the runner explains this and stops.

Nothing in the user's checkout is modified: sources are fetched into a
private bare repository under --work and built in runner-owned worktrees.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime
import getpass
import hashlib
import json
import os
import platform
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIT = HERE.parent
TOOLS = AUDIT / 'tools'
TESTS = AUDIT / 'tests'
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(HERE))
import hostplat  # noqa: E402
import pooled_compare  # noqa: E402
import quake_files  # noqa: E402

RUNNER_VERSION = 1
CHILD_ENV = dict(os.environ, PYTHONIOENCODING='utf-8')
DEFAULT_REMOTE = 'https://github.com/kaaburgh/dosbox-staging.git'
BASE_SHA = '27b1b7ca719c21ef3444f7ad1cf2d9cd439527cc'  # main at the Phase 2 baseline


class Variant:
    def __init__(self, label, title, pr, branch, phase2_sha, optional):
        self.label, self.title, self.pr = label, title, pr
        self.branch, self.phase2_sha, self.optional = branch, phase2_sha, optional
        self.sha = None
        self.ref = None
        self.binary = None


VARIANTS = [
    Variant('base', 'main at the Phase 2 baseline (A/A placebo: base and base2)', None, None, BASE_SHA, False),
    Variant('c4', 'PR #14 C4: guest flags restored without POPFQ', 14,
            'phase2/c4-popfq-free-flag-restore', '5bacbd99f1358f4096069313e16586f48cf6917f', False),
    Variant('c1a', 'PR #15 C1a: inline x87 m32 load', 15,
            'phase2/c1a-x87-inline-load-v2', '22ad052dfe80a33778ebccf04f19ca8baf447792', False),
    Variant('c2', 'PR #16 C2: inline PUSH/POP', 16,
            'phase2/c2-stack-inline', '4619d41d6b8f58180b4faf37c358c616a71c8c0f', False),
    Variant('c6', 'PR #17 C6: BT/SETcc/SAHF/LAHF translated', 17,
            'phase2/c6-decode-bt-setcc-sahf', 'f8e39504ca4e394ee7742b7cc370a8744336b281', False),
    Variant('all4', 'C4+C1a+C2+C6 integration (measurement only)', None,
            'phase2/integration-local', '911ccc0526281235169ad147cb64c7f2fa831dd3', False),
    Variant('c3', 'PR #18 C3: near-return linking (neutral in Phase 2)', 18,
            'phase2/c3-return-target-cache', 'c861a8b02e4685d3c3bd4f245e0417d502a5a149', True),
    Variant('c1b', 'PR #19 C1b: inline x87 m32 store (neutral in Phase 2)', 19,
            'phase2/c1b-x87-inline-store', 'dfed705a3607271a710e298087c13d9e19769d7f', True),
]

# Guest result of Quake 1.06 "timedemo demo1" at cpu_cycles_protected=1000000
# measured in Phase 2. C6 (and builds containing it) charge one emulated cycle
# less per formerly interpreted instruction, see PR #17.
REFERENCE_GUEST = {'default': (969, 3.0, 326.7), 'c6': (969, 3.0, 326.9), 'all4': (969, 3.0, 326.9)}
# With MSVC or clang-cl, PR #17's LAHF/SAHF detection (<cpuid.h>, __GNUC__ only)
# is compiled out, so the 0.18 M SAHF of the demo stay in the normal-core
# fallback; fewer emulated cycles are saved and the result rounds to 326.8
# (observed on GitHub's windows-2025-vs2026 runner, clang-cl 22).
REFERENCE_GUEST_MSVC_ABI = {'c6': (969, 3.0, 326.8), 'all4': (969, 3.0, 326.8)}
PHASE2_SPEEDUP = {'c4': '+11.1% [+8.8, +13.4]', 'c1a': '+9.1% [+7.0, +11.3]',
                  'c6': '+8.8% [+6.8, +11.0]', 'c2': '+3.4% [+1.2, +5.8]',
                  'all4': '+37.1% [+29.5, +44.2]', 'c3': '+2.0% [-0.1, +4.2]',
                  'c1b': '+1.6% [-0.4, +3.5]'}

# name -> (source, nasm defines, kind)
TESTS_SPEC = {
    'FLAGRST': ('flagrst.asm', [], 'flagrst'),
    'FLAGOPS': ('flagops.asm', [], 'deviations'),
    'BITOPS': ('bitops.asm', [], 'deviations'),
    'X87LOAD': ('x87load.asm', [], 'deviations'),
    'X87STORE': ('x87store.asm', [], 'deviations'),
    'STACKOPS': ('stackops.asm', [], 'stackops'),
    'JITSIZE': ('jitsize.asm', [], 'jitsize'),
    'JITSIZED': ('jitsize.asm', ['-DDIRTY'], 'jitsize'),
    'PFPREC': ('pfprec.asm', [], 'pfprec'),
    'PFREGS': ('pfregs.asm', [], 'pfprec'),
}
NEGATIVE_SPEC = {  # must report a failure on every build
    'FLAGOPSN': ('flagops.asm', ['-DNEG'], 'deviations'),
    'X87LNEG': ('x87load.asm', ['-DNEG'], 'deviations'),
    'X87SNEG': ('x87store.asm', ['-DNEG'], 'deviations'),
    'STACKNEG': ('stackops.asm', ['-DNEG'], 'stackops'),
}
CORES = ('normal', 'dynamic')

MODES = {'quick': {'blocks': 3, 'warmups': 1}, 'full': {'blocks': 20, 'warmups': 2}}

COMPAT_SHIMS = [  # (file, original text, replacement) - not CPU code
    ('src/gui/mapper.cpp',
     '\tSDL_SetDefaultTextureScaleMode(mapper.renderer, SDL_SCALEMODE_NEAREST);\n',
     '\t/* validation compat shim: older SDL3 without SDL_SetDefaultTextureScaleMode */\n'),
    ('src/midi/fluidsynth.cpp',
     '\tfluid_synth_set_portamento_time_mode(fluid_synth.get(),\n'
     '\t                                     FLUID_PORTAMENTO_TIME_MODE_XG_GS);\n',
     '\t/* validation compat shim: older FluidSynth without portamento time mode */\n'),
]

NOT_APPLICABLE = """\
Phase 2 validation is not applicable on this host ({system} {machine}).

PRs #14-#17 and #19 change only src/cpu/core_dyn_x86.cpp and
src/cpu/core_dyn_x86/, the x86 dynamic recompiler. DOSBox Staging's
CMakeLists.txt enables it (C_DYNAMIC_X86) only when the target processor is
AMD64/x86_64; on arm64/aarch64 it sets C_DYNAMIC_X86=OFF and C_DYNREC=ON, and
src/cpu/CMakeLists.txt compiles core_dyn_x86.cpp only if C_DYNAMIC_X86 is
set. A native ARM64 build therefore does not contain the code these PRs
modify: all variants build the same emulator, and any benchmark difference
would be noise.

The optional PR #18 additionally refactors src/cpu/dyn_cache.h, which the
dynrec core also includes (CacheBlock::Unlink split out of Clear, plus a field
dynrec does not use). That refactor is compiled on ARM64, but the feature it
serves (linking near returns) exists only in core_dyn_x86, so there is
nothing to measure there; checking dynrec's behaviour with the refactor would
need a separate correctness test plan.

Running an x86-64 build under Rosetta 2 or Windows-on-ARM emulation would
measure the translator's handling of POPFQ/PUSHFQ, not a real x86-64 CPU, so
it is not a meaningful substitute either.

Run the validation on an x86-64 Linux or Windows machine instead.
"""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
class Ctx:
    pass


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def sha256_file(path: Path) -> str:
    return quake_files.sha256_file(path)


def normalized_sha256(path: Path) -> str:
    """SHA-256 of a binary with link-time metadata removed. MSVC-style PE
    linkers embed a timestamp, a checksum and debug-directory data (PDB
    GUID/age) that change on every link even when the code is identical;
    those fields are zeroed. Other formats are hashed unchanged."""
    data = bytearray(path.read_bytes())

    def u16(o):
        return int.from_bytes(data[o:o + 2], 'little')

    def u32(o):
        return int.from_bytes(data[o:o + 4], 'little')

    if data[:2] == b'MZ' and len(data) > 0x40:
        pe = u32(0x3C)
        if data[pe:pe + 4] == b'PE\0\0':
            coff = pe + 4
            nsect, opt_size = u16(coff + 2), u16(coff + 16)
            data[coff + 4:coff + 8] = bytes(4)            # TimeDateStamp
            opt = coff + 20
            plus = u16(opt) == 0x20B
            data[opt + 64:opt + 68] = bytes(4)            # CheckSum
            ndirs = u32(opt + (108 if plus else 92))
            dirs = opt + (112 if plus else 96)
            sects = [(u32(s + 12), max(u32(s + 8), u32(s + 16)), u32(s + 20))
                     for s in range(opt + opt_size, opt + opt_size + 40 * nsect, 40)]

            def rva_to_off(rva):
                for va, size, raw in sects:
                    if va <= rva < va + size:
                        return raw + rva - va
                return None

            if ndirs > 6:
                drva, dsize = u32(dirs + 48), u32(dirs + 52)
                off = rva_to_off(drva) if drva else None
                for i in range((dsize // 28) if off is not None else 0):
                    e = off + 28 * i
                    data[e + 4:e + 8] = bytes(4)          # entry TimeDateStamp
                    size, ptr = u32(e + 16), u32(e + 24)
                    data[ptr:ptr + size] = bytes(size)    # CodeView/POGO/... payload
    return hashlib.sha256(data).hexdigest()


def run(cmd, cwd=None, env=None, log: Path | None = None, check=True, timeout=None) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('ab') as f:
            f.write(('\n$ ' + ' '.join(cmd) + '\n').encode())
            f.flush()
            p = subprocess.run(cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    else:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        tail = ''
        if log and log.is_file():
            tail = log.read_text(errors='replace')[-4000:]
        elif not log:
            tail = (p.stdout or '') + (p.stderr or '')
        raise RuntimeError(f'command failed ({p.returncode}): {" ".join(cmd)}\n{tail}')
    return p


def tool_version(cmd) -> str | None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = (p.stdout or p.stderr or '').strip().splitlines()
        return out[0] if out else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=False, default=str) + '\n', encoding='utf-8')


def say(ctx, msg: str) -> None:
    line = f'[{datetime.datetime.now().strftime("%H:%M:%S")}] {msg}'
    print(line, flush=True)
    with (ctx.results / 'console.log').open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def set_stage(ctx, stage: str, status: str, **extra) -> None:
    ctx.status['stages'][stage] = {'status': status, 'time': now_utc(), **extra}
    write_json(ctx.results / 'status.json', ctx.status)


def default_work() -> Path:
    if hostplat.IS_WINDOWS:
        base = os.environ.get('LOCALAPPDATA') or str(Path.home())
        return Path(base) / 'dosbox-phase2-validation'
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'dosbox-phase2-validation'


def find_nasm() -> str | None:
    found = shutil.which('nasm')
    if found or not hostplat.IS_WINDOWS:
        return found
    for base in (os.environ.get('ProgramFiles', r'C:\Program Files'),
                 os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                 os.path.join(os.environ.get('LOCALAPPDATA', ''), 'bin')):
        cand = Path(base) / 'NASM' / 'nasm.exe'
        if cand.is_file():
            return str(cand)
    return None


def default_preset() -> str:
    if hostplat.IS_WINDOWS:
        vswhere = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / \
            'Microsoft Visual Studio' / 'Installer' / 'vswhere.exe'
        if vswhere.is_file():
            v = tool_version([str(vswhere), '-latest', '-property', 'catalog_productLineVersion'])
            if v and v.strip() == '2022':
                return 'release-windows-vs2022'
        return 'release-windows'
    return 'release-linux-vcpkg' if os.environ.get('VCPKG_ROOT') else 'release-linux'


# --------------------------------------------------------------------------
# platform gate and prerequisites
# --------------------------------------------------------------------------
def platform_gate() -> int | None:
    system, machine = platform.system(), platform.machine().lower()
    x86_64 = machine in ('x86_64', 'amd64')
    if system in ('Linux', 'Windows') and x86_64:
        ident = os.environ.get('PROCESSOR_IDENTIFIER', '') + os.environ.get('PROCESSOR_ARCHITEW6432', '')
        if hostplat.IS_WINDOWS and 'ARM' in ident.upper():
            print(NOT_APPLICABLE.format(system=system, machine='x86-64 emulation on ARM64'))
            return 3
        return None
    if system == 'Darwin' and x86_64:
        if tool_version(['sysctl', '-n', 'sysctl.proc_translated']) == '1':
            print(NOT_APPLICABLE.format(system=system, machine='x86-64 under Rosetta 2 on Apple silicon'))
            return 3
        print('macOS on Intel (x86-64) runs core_dyn_x86, but this runner has no '
              'per-thread CPU-time backend for macOS; it supports Linux and Windows x86-64.')
        return 4
    print(NOT_APPLICABLE.format(system=system, machine=machine))
    return 3


def check_prereqs(ctx) -> dict:
    tools = {'git': shutil.which('git'), 'cmake': shutil.which('cmake'), 'nasm': find_nasm()}
    if not hostplat.IS_WINDOWS:
        tools['ninja'] = shutil.which('ninja')
    missing = [k for k, v in tools.items() if not v]
    if 'vcpkg' in ctx.preset or hostplat.IS_WINDOWS:
        if not os.environ.get('VCPKG_ROOT'):
            missing.append('VCPKG_ROOT (environment variable pointing to a vcpkg checkout)')
    versions = {'git': tool_version(['git', '--version']),
                'cmake': tool_version(['cmake', '--version']),
                'nasm': tool_version([tools['nasm'], '-v']) if tools['nasm'] else None,
                'ninja': tool_version(['ninja', '--version']) if tools.get('ninja') else None,
                'python': sys.version.split()[0]}
    if missing:
        hint = ('Linux: install git, cmake, ninja-build, nasm, a C++20 compiler and the DOSBox '
                'Staging build dependencies (docs/build-linux.md).\n'
                'Windows: install Visual Studio 2026/2022 with "C++ Clang tools for Windows", '
                'CMake, Git, NASM (winget install NASM.NASM), vcpkg with VCPKG_ROOT set '
                '(docs/build-windows.md).')
        raise SystemExit(f'missing prerequisites: {", ".join(missing)}\n{hint}')
    ctx.nasm = tools['nasm']
    return versions


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------
def git(ctx, *args, check=True, cwd=None):
    return run(['git', '-C', cwd or ctx.store, *args], check=check, log=ctx.results / 'logs' / 'git.log')


def git_out(ctx, *args, cwd=None) -> str:
    p = subprocess.run(['git', '-C', str(cwd or ctx.store), *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ''


def has_commit(ctx, rev: str) -> str:
    return git_out(ctx, 'rev-parse', '--verify', '--quiet', rev + '^{commit}')


def prepare_sources(ctx, variants: list[Variant]) -> dict:
    store = ctx.store
    if not (store / 'HEAD').is_file():
        store.mkdir(parents=True, exist_ok=True)
        run(['git', 'init', '--bare', '--quiet', store])
    # local copy of the user's refs (read-only use of the checkout)
    git(ctx, 'fetch', '--no-tags', '--quiet', '--update-shallow', str(ctx.source),
        '+refs/heads/*:refs/src/heads/*', '+refs/remotes/*:refs/src/remotes/*', check=False)
    fetch_log = {}
    if not ctx.args.offline:
        specs = ['+refs/heads/main:refs/up/heads/main']
        for v in variants:
            if v.pr:
                specs.append(f'+refs/pull/{v.pr}/head:refs/up/pull/{v.pr}')
            if v.branch:
                specs.append(f'+refs/heads/{v.branch}:refs/up/heads/{v.branch}')
        for spec in specs:
            p = git(ctx, 'fetch', '--no-tags', '--quiet', ctx.args.remote, spec, check=False)
            fetch_log[spec] = 'ok' if p.returncode == 0 else 'failed'
    overrides = dict(o.split('=', 1) for o in ctx.args.ref)
    report = {}
    for v in variants:
        cands = []
        if v.label in overrides:
            cands.append(overrides[v.label])
            if not ctx.args.offline:
                git(ctx, 'fetch', '--no-tags', '--quiet', ctx.args.remote, overrides[v.label], check=False)
                cands.append('FETCH_HEAD')
        elif v.label == 'base':
            cands.append(BASE_SHA)
        else:
            if v.pr:
                cands.append(f'refs/up/pull/{v.pr}')
            if v.branch:
                cands += [f'refs/up/heads/{v.branch}', f'refs/src/remotes/origin/{v.branch}',
                          f'refs/src/heads/{v.branch}']
        for c in cands:
            sha = has_commit(ctx, c)
            if sha:
                v.sha, v.ref = sha, c
                break
        if not v.sha:
            report[v.label] = {'error': f'cannot resolve any of {cands}'}
            continue
        report[v.label] = {
            'title': v.title, 'pr': v.pr, 'branch': v.branch, 'resolved_from': v.ref,
            'sha': v.sha, 'phase2_sha': v.phase2_sha,
            'matches_phase2': v.sha == v.phase2_sha,
            'based_on_phase2_baseline': subprocess.run(
                ['git', '-C', str(store), 'merge-base', '--is-ancestor', BASE_SHA, v.sha]).returncode == 0,
            'files_changed_vs_base': git_out(ctx, 'diff', '--name-only', BASE_SHA, v.sha).splitlines(),
            'commits_since_base': git_out(ctx, 'log', '--format=%h %s', f'{BASE_SHA}..{v.sha}').splitlines(),
        }
    main_now = has_commit(ctx, 'refs/up/heads/main') or has_commit(ctx, 'refs/src/heads/main')
    return {'remote': ctx.args.remote, 'offline': ctx.args.offline, 'fetch': fetch_log,
            'main_now': main_now, 'baseline': BASE_SHA, 'variants': report}


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------
def apply_shims(src: Path) -> list[str]:
    applied = []
    for rel, old, new in COMPAT_SHIMS:
        f = src / rel
        text = f.read_text()
        if old in text:
            f.write_text(text.replace(old, new), encoding='utf-8')
            applied.append(rel)
    return applied


def cmake_cache(build_dir: Path) -> dict:
    out = {}
    cache = build_dir / 'CMakeCache.txt'
    if not cache.is_file():
        return out
    keep = re.compile(r'^(CCACHE_PROGRAM|CMAKE_BUILD_TYPE|CMAKE_CXX_COMPILER|CMAKE_C_COMPILER|CMAKE_CXX_FLAGS[A-Z_]*|'
                      r'CMAKE_GENERATOR|CMAKE_GENERATOR_TOOLSET|CMAKE_GENERATOR_PLATFORM|'
                      r'CMAKE_TOOLCHAIN_FILE|VCPKG_TARGET_TRIPLET|USE_SYSTEM_LIBS|OPT_[A-Z_]+|'
                      r'C_DYNAMIC_X86|C_DYNREC|CMAKE_SYSTEM_PROCESSOR)(:[A-Z]+)?=(.*)$')
    for line in cache.read_text(errors='replace').splitlines():
        m = keep.match(line)
        if m:
            out[m.group(1)] = m.group(3)
    for f in build_dir.glob('CMakeFiles/*/CMakeCXXCompiler.cmake'):
        t = f.read_text(errors='replace')
        for key in ('CMAKE_CXX_COMPILER_ID', 'CMAKE_CXX_COMPILER_VERSION', 'CMAKE_CXX_COMPILER_FRONTEND_VARIANT',
                    'CMAKE_CXX_SIMULATE_ID'):
            m = re.search(rf'set\({key} "([^"]*)"\)', t)
            if m:
                out[key] = m.group(1)
    return out


def dyn_compile_command(build_dir: Path) -> str | None:
    cc = build_dir / 'compile_commands.json'
    if not cc.is_file():
        return None
    for e in json.loads(cc.read_text()):
        if e.get('file', '').replace('\\', '/').endswith('src/cpu/core_dyn_x86.cpp'):
            return e.get('command') or ' '.join(e.get('arguments', []))
    return None


def find_binary(build_dir: Path) -> Path | None:
    names = ['dosbox.exe'] if hostplat.IS_WINDOWS else ['dosbox']
    for sub in ('', 'Release', 'RelWithDebInfo'):
        for n in names:
            p = build_dir / sub / n
            if p.is_file():
                return p
    return None


def collect_binary(ctx, label: str, binary: Path) -> Path:
    dest = ctx.work / 'bins' / label
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if hostplat.IS_WINDOWS:
        for f in binary.parent.iterdir():
            if f.is_file() and f.suffix.lower() in ('.exe', '.dll'):
                shutil.copy2(f, dest / f.name)
    else:
        shutil.copy2(binary, dest / binary.name)
    return dest / binary.name


def build_one(ctx, src: Path, v: Variant, log: Path, configure: bool) -> dict:
    t0 = time.monotonic()
    preset = ctx.preset
    build_dir = src / 'build' / preset
    info = {'label': v.label, 'sha': v.sha, 'preset': preset, 'build_mode': ctx.args.build_mode}
    if configure or not (build_dir / 'CMakeCache.txt').is_file():
        cmd = ['cmake', '--preset', preset, '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON', *ctx.args.cmake_arg]
        # vcpkg downloads dependency sources during configure; retry on the
        # transient network failures it does not retry itself
        for attempt in range(ctx.args.configure_retries + 1):
            try:
                run(cmd, cwd=src, log=log)
                break
            except RuntimeError:
                if attempt == ctx.args.configure_retries:
                    raise
                info.setdefault('configure_retries_used', 0)
                info['configure_retries_used'] += 1
                say(ctx, f'  configure failed, retrying in 30 s ({attempt + 1}/{ctx.args.configure_retries})')
                time.sleep(30)
    jobs = ['--parallel', str(ctx.args.jobs)] if ctx.args.jobs else []
    run(['cmake', '--build', '--preset', preset, *jobs], cwd=src, log=log)
    binary = find_binary(build_dir)
    if binary is None:
        raise RuntimeError(f'built binary not found under {build_dir}')
    v.binary = collect_binary(ctx, v.label, binary)
    info.update({'binary_sha256': sha256_file(v.binary),
                 'binary_sha256_normalized': normalized_sha256(v.binary),
                 'binary_size': v.binary.stat().st_size,
                 'build_seconds': round(time.monotonic() - t0, 1),
                 'cmake_cache': cmake_cache(build_dir),
                 'core_dyn_x86_compile_command': dyn_compile_command(build_dir)})
    cache = info['cmake_cache']
    compiled = info['core_dyn_x86_compile_command'] is not None or any(
        'core_dyn_x86.cpp' in f.read_text(errors='replace') for f in build_dir.glob('*.vcxproj'))
    if not compiled:
        raise RuntimeError('core_dyn_x86.cpp is not part of this build (C_DYNAMIC_X86 off?): '
                           'the code under test would not be compiled')
    info['core_dyn_x86_compiled'] = True
    # CMakeLists.txt sets ccache as compiler launcher whenever it finds it;
    # launchers only take effect with the Ninja and Makefile generators
    ccache = cache.get('CCACHE_PROGRAM', '')
    gen = cache.get('CMAKE_GENERATOR', '')
    info['compiler_launcher'] = ('ccache' if ccache and not ccache.endswith('-NOTFOUND')
                                 and ('Ninja' in gen or 'Makefiles' in gen) else None)
    comp = cache.get('CMAKE_CXX_COMPILER')
    if comp and not hostplat.IS_WINDOWS:
        info['compiler_version'] = tool_version([comp, '--version'])
    return info


def checkout(ctx, src: Path, sha: str, log: Path) -> list[str]:
    run(['git', '-C', src, 'checkout', '--quiet', '--force', '--detach', sha], log=log)
    return apply_shims(src) if ctx.args.compat_shims else []


def build_all(ctx, variants: list[Variant]) -> dict:
    report = {'preset': ctx.preset, 'cmake_args': ctx.args.cmake_arg,
              'compat_shims': ctx.args.compat_shims, 'mode': ctx.args.build_mode, 'variants': {}}
    logs = ctx.results / 'logs'
    if ctx.args.build_mode == 'incremental':
        src = ctx.work / 'src'
        if not (src / '.git').exists():
            run(['git', '-C', ctx.store, 'worktree', 'prune'], check=False)
            run(['git', '-C', ctx.store, 'worktree', 'add', '--force', '--detach', src, BASE_SHA],
                log=logs / 'git.log')
        first = True
        for v in variants:
            log = logs / f'build-{v.label}.log'
            say(ctx, f'build {v.label} ({v.sha[:12]}) ...')
            try:
                shims = checkout(ctx, src, v.sha, log)
                info = build_one(ctx, src, v, log, configure=first)
                info['compat_shims_applied'] = shims
                report['variants'][v.label] = info
                say(ctx, f'  ok in {info["build_seconds"]} s, sha256 {info["binary_sha256"][:16]}')
                first = False
            except Exception as e:  # keep going with the other variants
                v.binary = None
                report['variants'][v.label] = {'error': str(e)[-3000:]}
                say(ctx, f'  BUILD FAILED: {str(e).splitlines()[0]}')
            write_json(ctx.results / 'builds.json', report)
        base = variants[0]
        if base.binary is not None and len(variants) > 1:
            # incremental rebuild check: going back to the baseline must give
            # the same binary as the first baseline build
            say(ctx, 'rebuild check: baseline again after all variants ...')
            log = logs / 'build-base-rebuild.log'
            try:
                checkout(ctx, src, base.sha, log)
                build_dir = src / 'build' / ctx.preset
                run(['cmake', '--build', '--preset', ctx.preset] +
                    (['--parallel', str(ctx.args.jobs)] if ctx.args.jobs else []), cwd=src, log=log)
                again_bin = find_binary(build_dir)
                first = report['variants']['base']
                again_raw, again_norm = sha256_file(again_bin), normalized_sha256(again_bin)
                report['rebuild_check'] = {
                    'first': first['binary_sha256'], 'again': again_raw,
                    'raw_identical': again_raw == first['binary_sha256'],
                    'identical': again_norm == first['binary_sha256_normalized'],
                    'note': 'compared with link timestamp, checksum and debug-directory data '
                            'zeroed (PE); ELF binaries are compared byte for byte'}
            except Exception as e:
                report['rebuild_check'] = {'error': str(e)[-2000:]}
        runtime_src = src
    else:
        runtime_src = None
        for v in variants:
            src = ctx.work / f'src-{v.label}'
            log = logs / f'build-{v.label}.log'
            say(ctx, f'build {v.label} ({v.sha[:12]}) in its own worktree ...')
            try:
                if not (src / '.git').exists():
                    run(['git', '-C', ctx.store, 'worktree', 'add', '--force', '--detach', src, v.sha], log=log)
                shims = checkout(ctx, src, v.sha, log)
                info = build_one(ctx, src, v, log, configure=True)
                info['compat_shims_applied'] = shims
                report['variants'][v.label] = info
                say(ctx, f'  ok in {info["build_seconds"]} s, sha256 {info["binary_sha256"][:16]}')
            except Exception as e:
                v.binary = None
                report['variants'][v.label] = {'error': str(e)[-3000:]}
                say(ctx, f'  BUILD FAILED: {str(e).splitlines()[0]}')
            if runtime_src is None and v.label == 'base':
                runtime_src = src
            write_json(ctx.results / 'builds.json', report)
    # runtime directory with the (variant-independent) resources
    runtime = ctx.work / 'runtime'
    if runtime_src is not None and (runtime_src / 'resources').is_dir():
        if runtime.exists():
            shutil.rmtree(runtime)
        shutil.copytree(runtime_src / 'resources', runtime / 'resources')
    runtime.mkdir(parents=True, exist_ok=True)
    ctx.runtime = runtime
    write_json(ctx.results / 'builds.json', report)
    return report


# --------------------------------------------------------------------------
# guest tests
# --------------------------------------------------------------------------
def assemble_tests(ctx) -> dict:
    out_dir = ctx.work / 'tests'
    out_dir.mkdir(parents=True, exist_ok=True)
    info = {}
    for name, (srcname, defines, _) in {**TESTS_SPEC, **NEGATIVE_SPEC}.items():
        com = out_dir / f'{name}.COM'
        run([ctx.nasm, '-f', 'bin', *defines, '-o', com, TESTS / srcname],
            log=ctx.results / 'logs' / 'nasm.log')
        info[name] = {'source': f'extras/jit-audit/tests/{srcname}', 'defines': defines,
                      'sha256': sha256_file(com)}
    return info


PF_LINE = re.compile(r'^(\S+)\s+F=(\d+)\s+E=([A-Z])\s+R=(\w+)\s+V=(\w+)')


def evaluate(name: str, kind: str, core: str, text: str, base_text: str | None, flagrst_out: str | None) -> dict:
    """Architectural verdict: PASS, KNOWN (documented deviation of the
    unmodified emulator, must be identical to the baseline) or FAIL."""
    res = {'verdict': 'FAIL', 'detail': ''}
    if text.startswith(('TIMEOUT', 'NO_OUTPUT')) or not text.strip():
        res['detail'] = text.strip()[:80] or 'empty output'
        return res
    same = base_text is not None and text == base_text
    res['same_as_base'] = same if base_text is not None else None
    if kind == 'deviations':
        m = re.search(r'deviations=([0-9A-Fa-f]+)', text)
        if m and int(m.group(1), 16) == 0:
            res['verdict'] = 'PASS'
        res['detail'] = m.group(0) if m else 'no deviations line'
    elif kind == 'flagrst':
        m = re.search(r'deviations=(\d+)', flagrst_out or '')
        if m and int(m.group(1)) == 0 and 'incomplete' not in (flagrst_out or ''):
            res['verdict'] = 'PASS'
        res['detail'] = (flagrst_out or '').strip().splitlines()[0] if flagrst_out else 'checker not run'
    elif kind == 'jitsize':
        res['verdict'] = 'PASS' if 'JITSIZE_PASS' in text else 'FAIL'
        res['detail'] = text.strip().splitlines()[-1][:80]
    elif kind == 'stackops':
        m = re.search(r'STACKOPS fails=([0-9A-Fa-f]+) checks=([0-9A-Fa-f]+)', text)
        fails = [l for l in text.splitlines() if l.rstrip().endswith('FAIL')]
        if m and int(m.group(1), 16) == 0:
            res['verdict'] = 'PASS'
        elif m and core == 'normal' and len(fails) == 1 and fails[0].startswith('pf call eip'):
            # pre-existing normal-core defect (faulting CALL records the next EIP)
            res['verdict'] = 'KNOWN' if (same or base_text is None) else 'FAIL'
        res['detail'] = (m.group(0) if m else 'no summary line') + (f'; {fails[0][:60]}' if fails else '')
    elif kind == 'pfprec':
        lines = [PF_LINE.match(l) for l in text.splitlines()]
        lines = [m for m in lines if m]
        if not lines:
            res['detail'] = 'no case lines'
            return res
        arch_ok, known = True, []
        for m in lines:
            case, faults, e, r, val = m.groups()
            if faults != '01' or val != 'ok':
                arch_ok = False
                known = None
                break
            if (e, r) != ('I', 'ok'):
                # legacy x87 helper faults in the dynamic core: pushed EIP is
                # the block start, registers are stale (documented in Phase 1)
                if core == 'dynamic' and case.startswith('f') and (e, r) == ('B', 'stale'):
                    known.append(case)
                else:
                    arch_ok = False
                    known = None
                    break
        if arch_ok and not known:
            res['verdict'] = 'PASS'
        elif arch_ok and known is not None:
            res['verdict'] = 'KNOWN' if (same or base_text is None) else 'FAIL'
            res['detail'] = 'legacy x87 #PF imprecision: ' + ','.join(known)
        if not res['detail']:
            res['detail'] = f'{len(lines)} cases'
    if res['verdict'] in ('PASS', 'KNOWN') and base_text is not None and not same:
        res['note'] = 'architecturally correct, but output differs from the baseline'
    return res


def run_guest_test(ctx, binary: Path, com: Path, core: str, out: Path) -> tuple[str, list]:
    """Run one guest test. A harness-level failure (no output, crash of the
    wrapper, timeout) is retried up to twice; every attempt is recorded."""
    keep = out.with_suffix('.dosbox.txt')
    cmd = [sys.executable, TOOLS / 'run_dos_test.py', '--dosbox', binary, '--com', com,
           '--core', core, '--cycles', 'max', '--runtime-cwd', ctx.runtime,
           '--timeout', str(ctx.args.test_timeout), '--keep', keep]
    out.parent.mkdir(parents=True, exist_ok=True)
    attempts = []
    for attempt in range(3):
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding='utf-8',
                           errors='replace', env=CHILD_ENV)
        text = p.stdout
        harness_ok = p.returncode == 0 and text.strip() != ''
        attempts.append({'returncode': p.returncode, 'stdout_bytes': len(text),
                         'stderr_tail': p.stderr[-600:] if not harness_ok else ''})
        if harness_ok:
            break
        err = out.with_suffix(f'.attempt{attempt + 1}.stderr.txt')
        err.write_text(f'returncode {p.returncode}\n--- stdout ---\n{text}\n--- stderr ---\n{p.stderr}', encoding='utf-8')
    out.write_text(text, encoding='utf-8')
    return text, attempts


def run_tests(ctx, variants: list[Variant]) -> dict:
    tdir = ctx.results / 'tests'
    built = [v for v in variants if v.binary]
    jobs = []
    for v in built:
        for core in CORES:
            for name in TESTS_SPEC:
                jobs.append((v, core, name))
    base = next((v for v in built if v.label == 'base'), None)
    if base:
        for core in CORES:
            for name in NEGATIVE_SPEC:
                jobs.append((base, core, name))
    workers = max(1, min(ctx.args.test_jobs or (os.cpu_count() or 2) - 1, 8))
    say(ctx, f'guest tests: {len(jobs)} runs on {workers} workers ...')
    outputs = {}

    attempts_by_job = {}

    def one(job):
        v, core, name = job
        out = tdir / v.label / core / f'{name}.txt'
        return job, run_guest_test(ctx, v.binary, ctx.work / 'tests' / f'{name}.COM', core, out)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for (v, core, name), (text, attempts) in ex.map(one, jobs):
            outputs[(v.label, core, name)] = text
            attempts_by_job[(v.label, core, name)] = attempts
    results = {'cycles': 'max', 'variants': {}, 'negative_controls': {}}
    for (label, core, name), text in sorted(outputs.items()):
        if name in NEGATIVE_SPEC:
            kind = NEGATIVE_SPEC[name][2]
            r = evaluate(name, kind, core, text, None, None)
            detected = r['verdict'] == 'FAIL' and not text.startswith(('TIMEOUT', 'NO_OUTPUT'))
            results['negative_controls'].setdefault(core, {})[name] = {
                'detected': detected, 'detail': r['detail']}
            continue
        kind = TESTS_SPEC[name][2]
        flagrst_out = None
        if kind == 'flagrst':
            p = subprocess.run([sys.executable, str(TESTS / 'check_flagrst.py')], input=text,
                               capture_output=True, text=True, encoding='utf-8', errors='replace',
                               env=CHILD_ENV)
            flagrst_out = p.stdout + p.stderr
            (tdir / label / core / f'{name}.check.txt').write_text(flagrst_out, encoding='utf-8')
        base_text = outputs.get(('base', core, name)) if label != 'base' else None
        verdict = evaluate(name, kind, core, text, base_text, flagrst_out)
        att = attempts_by_job.get((label, core, name), [])
        if len(att) > 1:
            verdict['harness_attempts'] = att
        results['variants'].setdefault(label, {}).setdefault(core, {})[name] = verdict
    # keep DOSBox logs only where something failed
    for label, cores in results['variants'].items():
        for core, tests in cores.items():
            for name, r in tests.items():
                log = tdir / label / core / f'{name}.dosbox.txt'
                if r['verdict'] == 'FAIL' and log.is_file():
                    dst = ctx.results / 'diagnostics' / 'tests' / f'{label}-{core}-{name}.dosbox.txt'
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(log, dst)
    for log in tdir.rglob('*.dosbox.txt'):
        log.unlink()
    write_json(ctx.results / 'tests' / 'verdicts.json', results)
    return results


# --------------------------------------------------------------------------
# benchmark
# --------------------------------------------------------------------------
def bench(ctx, variants: list[Variant], game: Path) -> dict:
    built = [v for v in variants if v.binary]
    if not any(v.label == 'base' for v in built):
        raise RuntimeError('baseline binary missing; benchmark skipped')
    labels = []
    for v in built:
        labels.append((v.label, v.binary))
        if v.label == 'base':
            labels.append(('base2', v.binary))
    params = dict(MODES[ctx.args.mode])
    if ctx.args.blocks:
        params['blocks'] = ctx.args.blocks
    if ctx.args.warmups is not None:
        params['warmups'] = ctx.args.warmups
    seed = ctx.args.seed if ctx.args.seed is not None else random.SystemRandom().randrange(2**31)
    bdir = ctx.results / 'bench'
    bdir.mkdir(parents=True, exist_ok=True)
    runlogs = ctx.work / 'runlogs' / ctx.run_id
    tmp = ctx.work / 'tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, TOOLS / 'bench_quake.py', '--game', game, '--runtime-cwd', ctx.runtime,
           '--cycles', '1000000', '--blocks', params['blocks'], '--warmups', params['warmups'],
           '--seed', seed, '--tmp', tmp, '--run-logs', runlogs, '--sensors',
           '--timeout', ctx.args.bench_timeout, '--out', bdir / 'runs.csv']
    if ctx.cpus:
        cmd += ['--affinity', ','.join(map(str, ctx.cpus))]
    for label, binary in labels:
        cmd += ['--variant', f'{label}={binary}']
    total = params['warmups'] + params['blocks'] * len(labels)
    say(ctx, f'benchmark: {len(labels)} labels x {params["blocks"]} blocks + {params["warmups"]} warm-ups '
             f'= {total} runs (seed {seed}, CPUs {ctx.cpus or "unpinned"})')
    t0 = time.monotonic()
    proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace', env=CHILD_ENV)
    with (ctx.results / 'logs' / 'bench.log').open('w', encoding='utf-8') as blog:
        for line in proc.stdout:
            blog.write(line)
            try:
                row = json.loads(line)
                el = time.monotonic() - t0
                eta = el / row['number'] * (total - row['number'])
                print(f'  run {row["number"]}/{total} {row["label"]:6s} {row["status"]:8s} '
                      f'fps={row["fps"]} demo_cpu={row["demo_cpu_s"]}s  eta {eta/60:.1f} min', flush=True)
            except (ValueError, KeyError, ZeroDivisionError):
                pass
    rc = proc.wait()
    manifest = json.loads((bdir / 'runs.json').read_text()) if (bdir / 'runs.json').is_file() else {}
    write_json(bdir / 'manifest.json', manifest)
    if (bdir / 'runs.json').is_file():
        (bdir / 'runs.json').unlink()
    return {'returncode': rc, 'params': params, 'seed': seed, 'labels': [l for l, _ in labels],
            'runlogs': runlogs}


def analyse_bench(ctx, info: dict) -> dict:
    csv_path = ctx.results / 'bench' / 'runs.csv'
    if not csv_path.is_file():
        return {'error': 'no runs.csv'}
    rows = list(csv.DictReader(csv_path.open(encoding='utf-8', errors='replace')))
    det = {'labels': {}, 'deviating_runs': [], 'failed_runs': []}
    for lab in info['labels']:
        lr = [r for r in rows if r['label'] == lab]
        ok = [r for r in lr if r['status'] == 'ok']
        results = [(r['frames'], r['guest_seconds'], r['fps']) for r in ok]
        modal = max(set(results), key=results.count) if results else None
        key = 'base' if lab == 'base2' else lab
        ref = REFERENCE_GUEST.get(key, REFERENCE_GUEST['default'])
        if ctx.msvc_abi and key in REFERENCE_GUEST_MSVC_ABI:
            ref = REFERENCE_GUEST_MSVC_ABI[key]
        ref_s = (str(ref[0]), str(ref[1]), str(ref[2]))
        dev = [r for r in ok if (r['frames'], r['guest_seconds'], r['fps']) != modal]
        det['labels'][lab] = {'runs': len(lr), 'ok': len(ok), 'modal_result': modal,
                              'deviating': len(dev), 'phase2_reference': ref_s,
                              'matches_phase2_reference': modal == ref_s}
        det['deviating_runs'] += [{'number': r['number'], 'label': lab,
                                   'result': (r['frames'], r['guest_seconds'], r['fps']),
                                   'demo_cpu_s': r['demo_cpu_s']} for r in dev]
        det['failed_runs'] += [{'number': r['number'], 'label': lab, 'status': r['status']}
                               for r in lr if r['status'] != 'ok']
    det['reference_set'] = 'MSVC ABI (clang-cl/MSVC): c6/all4 326.8' if ctx.msvc_abi else 'GCC/Clang: c6/all4 326.9'
    det['deterministic'] = not det['deviating_runs'] and not det['failed_runs']
    det['all_match_phase2_reference'] = all(v['matches_phase2_reference'] for v in det['labels'].values())
    # diagnostics: every deviating/failed run plus one normal run of the same label
    runlogs = info['runlogs']
    keep = {int(d['number']) for d in det['deviating_runs'] + det['failed_runs']}
    for d in det['deviating_runs'] + det['failed_runs']:
        normal = next((r for r in rows if r['label'] == d['label'] and r['status'] == 'ok'
                       and int(r['number']) not in keep), None)
        if normal:
            keep.add(int(normal['number']))
    for n in sorted(keep):
        for src in runlogs.glob(f'run-{n:04d}-*'):
            shutil.copytree(src, ctx.results / 'diagnostics' / 'bench' / src.name, dirs_exist_ok=True)
    write_json(ctx.results / 'bench' / 'determinism.json', det)

    # statistics (pooled_compare drops blocks with a deviating guest result)
    import io
    import contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        blocks, labels = pooled_compare.blocks_of(str(csv_path))
    dropped = [l for l in err.getvalue().splitlines() if l.strip()]
    rng = random.Random(1)
    stats = {'metric': 'demo_cpu_s', 'reference': ['base', 'base2'], 'blocks_used': len(blocks),
             'blocks_dropped': [re.sub(r'^.*?: ', '', l) for l in dropped]}
    if len(blocks) >= 2:
        stats['speedup_vs_base'] = pooled_compare.analyse(blocks, labels, ['base', 'base2'],
                                                          'demo_cpu_s', rng, boot=10000)
        aa = pooled_compare.analyse(blocks, ['base'], ['base2'], 'demo_cpu_s', rng, boot=10000)['base']
        stats['aa_base_vs_base2'] = aa
        lo, hi = aa['boot95_pct']
        stats['aa_ok'] = lo <= 0 <= hi
        stats['aa_resolution_pct'] = round((hi - lo) / 2, 2)
        stats['timing_conclusive'] = stats['aa_ok'] and stats['aa_resolution_pct'] <= ctx.args.max_aa_resolution
        if hostplat.IS_WINDOWS and all(r.get('demo_thread_cycles') for r in rows if r['status'] == 'ok'):
            stats['speedup_vs_base_thread_cycles'] = pooled_compare.analyse(
                blocks, labels, ['base', 'base2'], 'demo_thread_cycles', rng, boot=10000)
    else:
        stats['note'] = 'fewer than 2 usable blocks; no statistics'
    per_label = {}
    for lab in info['labels']:
        vals = [float(r['demo_cpu_s']) for r in rows if r['label'] == lab and r['status'] == 'ok'
                and r['warmup'] == '0' and r['demo_cpu_s']]
        if vals:
            per_label[lab] = {'n': len(vals), 'median_s': round(statistics.median(vals), 4),
                              'cv_pct': round(100 * statistics.pstdev(vals) / statistics.fmean(vals), 2)}
    stats['per_label'] = per_label
    write_json(ctx.results / 'bench' / 'stats.json', stats)
    return {'determinism': det, 'stats': stats}


# --------------------------------------------------------------------------
# sanitising and packing
# --------------------------------------------------------------------------
def build_redactions(ctx) -> list[tuple[re.Pattern, str]]:
    pairs = []
    paths = [(ctx.results, '<RESULTS>'), (ctx.work, '<WORK>'), (ctx.source, '<REPO>'),
             (Path(tempfile.gettempdir()), '<TMP>'), (Path.home(), '<HOME>')]
    for p, tag in paths:
        try:
            s = str(Path(p).resolve())
        except OSError:
            s = str(p)
        for variant in {s, s.replace('\\', '/'), s.replace('/', '\\'),
                        s.replace('/', '\\').replace('\\', '\\\\')}:
            if len(variant) > 3:
                pairs.append((re.compile(re.escape(variant), re.I if hostplat.IS_WINDOWS else 0), tag))
    user = getpass.getuser()
    if user and len(user) >= 2:
        pairs.append((re.compile(r'(?<=[\\/])' + re.escape(user) + r'(?=[\\/])', re.I), '<USER>'))
    host = socket.gethostname()
    if host and host.lower() not in ('localhost',) and len(host) >= 3:
        pairs.append((re.compile(r'\b' + re.escape(host) + r'\b', re.I), '<HOST>'))
    return pairs


def sanitize_tree(ctx) -> dict:
    pairs = build_redactions(ctx)
    counts = {}
    for f in ctx.results.rglob('*'):
        if not f.is_file():
            continue
        data = f.read_bytes()
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('latin1')
        new = text
        for pat, tag in pairs:
            new, n = pat.subn(tag, new)
            if n:
                counts[tag] = counts.get(tag, 0) + n
        if new != text:
            f.write_text(new, encoding='utf-8')
    return counts


def make_zip(ctx) -> Path:
    allowed = {'.json', '.csv', '.txt', '.md', '.log', '.conf'}
    out = ctx.out_dir / f'{ctx.run_id}.zip'
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(ctx.results.rglob('*')):
            if f.is_file() and f.suffix.lower() in allowed and f.stat().st_size < 20 * 1024 * 1024:
                z.write(f, f'{ctx.run_id}/{f.relative_to(ctx.results).as_posix()}')
    return out


def residual_check(ctx, zpath: Path) -> list[str]:
    home = str(Path.home())
    needles = {home, home.replace('\\', '\\\\'), getpass.getuser() and f'/{getpass.getuser()}/',
               getpass.getuser() and f'\\{getpass.getuser()}\\'}
    needles = {n for n in needles if n and len(n) > 3}
    hits = []
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            data = z.read(name).decode('utf-8', 'replace')
            for n in needles:
                if n.lower() in data.lower():
                    hits.append(f'{name}: contains a local path fragment')
    return hits


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------
def summarize(ctx) -> None:
    s = ctx.summary
    lines = [f'# Phase 2 validation — {ctx.run_id}', '',
             f'Mode **{ctx.args.mode}**, runner v{RUNNER_VERSION}, '
             f'{s.get("host", {}).get("os")} {s.get("host", {}).get("machine")}, '
             f'CPU: {s.get("host", {}).get("cpu_model")}', '']
    overall = []
    builds = s.get('builds', {}).get('variants', {})
    if builds:
        lines += ['## Builds', '', '| Variant | Commit | Phase 2 head | Binary SHA-256 | Build |', '|---|---|---|---|---|']
        srcs = s.get('sources', {}).get('variants', {})
        for lab, b in builds.items():
            sv = srcs.get(lab, {})
            ok = 'ok' if 'binary_sha256' in b else 'FAILED'
            if ok != 'ok':
                overall.append(f'build of {lab} failed')
            lines.append(f'| {lab} | {str(sv.get("sha", ""))[:12]} | '
                         f'{"same" if sv.get("matches_phase2") else "DIFFERENT"} | '
                         f'{b.get("binary_sha256", "")[:16]} | {ok} |')
        rc = s.get('builds', {}).get('rebuild_check')
        if rc:
            if rc.get('identical'):
                desc = 'identical' if rc.get('raw_identical') else \
                    'identical code (differs only in link timestamp/checksum/debug directory)'
            else:
                desc = f'DIFFERENT: {rc}'
            lines += ['', f'Incremental rebuild check (baseline rebuilt last): {desc}']
            if rc.get('identical') is False:
                overall.append('baseline rebuild produced a different binary')
        lines.append('')
    tests = s.get('tests')
    if tests:
        lines += ['## Guest tests (architectural expectations)', '',
                  'PASS = SDM-derived expectation met; KNOWN = documented pre-existing deviation, identical to '
                  'the baseline; FAIL = unexpected.', '',
                  '| Variant | Core | PASS | KNOWN | FAIL | differs from base |', '|---|---|---|---|---|---|']
        for lab, cores in tests['variants'].items():
            for core, res in cores.items():
                c = {k: sum(1 for r in res.values() if r['verdict'] == k) for k in ('PASS', 'KNOWN', 'FAIL')}
                diff = [n for n, r in res.items() if r.get('same_as_base') is False]
                fails = [n for n, r in res.items() if r['verdict'] == 'FAIL']
                if fails:
                    overall.append(f'tests failed: {lab}/{core}: {", ".join(fails)}')
                if diff:
                    overall.append(f'output differs from baseline: {lab}/{core}: {", ".join(diff)}')
                lines.append(f'| {lab} | {core} | {c["PASS"]} | {c["KNOWN"]} | {c["FAIL"]}'
                             f'{" (" + ", ".join(fails) + ")" if fails else ""} | {", ".join(diff) or "-"} |')
        retried = [f'{lab}/{core}/{n}' for lab, cores in tests['variants'].items()
                   for core, res in cores.items() for n, r in res.items() if r.get('harness_attempts')]
        if retried:
            lines += ['', 'Harness retries (wrapper produced no output; the verdict is from the last attempt, '
                          'all attempts are in tests/verdicts.json): ' + ', '.join(retried)]
            overall.append('guest-test harness needed retries: ' + ', '.join(retried))
        neg = tests.get('negative_controls', {})
        undetected = [f'{core}/{n}' for core, d in neg.items() for n, r in d.items() if not r['detected']]
        lines += ['', f'Negative controls (deliberately wrong expectations, must be reported): '
                      f'{"all detected" if not undetected else "NOT detected: " + ", ".join(undetected)}', '']
        if undetected:
            overall.append('negative controls not detected')
    b = s.get('bench')
    if b and 'determinism' in b:
        det, st = b['determinism'], b['stats']
        lines += ['## Quake 1.06 timedemo demo1 (fixed 1,000,000 cycles)', '']
        lines.append(f'Determinism: **{"OK" if det["deterministic"] else "FAILED"}** '
                     f'({len(det["deviating_runs"])} deviating, {len(det["failed_runs"])} failed runs); '
                     f'guest results match Phase 2 reference ({det.get("reference_set")}): '
                     f'{"yes" if det["all_match_phase2_reference"] else "NO"}')
        if not det['deterministic']:
            overall.append('benchmark determinism FAILED (see bench/determinism.json and diagnostics/bench)')
        if not det['all_match_phase2_reference']:
            overall.append('guest result differs from the Phase 2 reference')
        if st.get('blocks_dropped'):
            lines.append(f'Blocks dropped from statistics: {len(st["blocks_dropped"])} — ' +
                         '; '.join(st['blocks_dropped']))
        if 'speedup_vs_base' in st:
            aa = st['aa_base_vs_base2']
            lines += ['', f'A/A placebo (base vs base2): {aa["speedup_pct"]:+.2f}% '
                          f'[{aa["boot95_pct"][0]:+.2f}, {aa["boot95_pct"][1]:+.2f}] — '
                          f'{"consistent with zero" if st["aa_ok"] else "NOT consistent with zero"}; '
                          f'resolution ≈ ±{st["aa_resolution_pct"]}% (limit {ctx.args.max_aa_resolution}%)', '',
                      f'Timing: **{"CONCLUSIVE" if st["timing_conclusive"] else "INCONCLUSIVE"}**'
                      + ('' if st['timing_conclusive'] else
                         ' — the speed-ups below are not interpretable; '
                         + ('quick mode is a pipeline check, use --mode full' if ctx.args.mode == 'quick'
                            else 'reduce host noise (idle machine, fixed CPU frequency) or add --blocks')), '']
            if not st['aa_ok']:
                overall.append('A/A placebo not consistent with zero: timing results are not reliable')
            elif not st['timing_conclusive'] and ctx.args.mode == 'full':
                overall.append(f'timing inconclusive: A/A resolution ±{st["aa_resolution_pct"]}% '
                               f'> {ctx.args.max_aa_resolution}%')
            lines += ['| Label | Speed-up vs base [95% CI] | Blocks | Median demo CPU s | CV % | Phase 2 (Intel VM) |',
                      '|---|---|---|---|---|---|']
            for lab, r in st['speedup_vs_base'].items():
                pl = st['per_label'].get(lab, {})
                lines.append(f'| {lab} | {r["speedup_pct"]:+.2f}% [{r["boot95_pct"][0]:+.2f}, '
                             f'{r["boot95_pct"][1]:+.2f}] | {r["blocks"]} | {pl.get("median_s", "")} | '
                             f'{pl.get("cv_pct", "")} | {PHASE2_SPEEDUP.get(lab, "")} |')
            cyc = st.get('speedup_vs_base_thread_cycles')
            if cyc:
                lines += ['', 'Same analysis with QueryThreadCycleTime (cycle-accurate; GetThreadTimes above has '
                              'clock-tick resolution): ' + ', '.join(
                                  f'{lab} {r["speedup_pct"]:+.2f}% [{r["boot95_pct"][0]:+.2f}, {r["boot95_pct"][1]:+.2f}]'
                                  for lab, r in cyc.items() if lab not in ('base', 'base2'))]
            lines += ['', 'Speed-up = geometric mean over blocks of (reference CPU time / variant CPU time) − 1, '
                          'reference = geometric mean of base and base2 in the same block; percentile bootstrap.']
        else:
            lines.append(st.get('note', 'no statistics'))
        lines.append('')
    errors = s.get('errors', [])
    if errors:
        overall += [f'error in stage {e["stage"]}' for e in errors]
        lines += ['## Errors', ''] + [f'- {e["stage"]}: {e["error"].splitlines()[0]}' for e in errors] + ['']
    verdict = 'OK' if not overall else 'ATTENTION'
    lines[2:2] = [f'**Overall: {verdict}**', ''] + [f'- {o}' for o in overall] + ([''] if overall else [])
    s['overall'] = {'verdict': verdict, 'reasons': overall}
    (ctx.results / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    write_json(ctx.results / 'summary.json', s)


REVIEWER_README = """\
# How to read this archive

Produced by extras/jit-audit/validate/phase2_validate.py (DOSBox Staging,
kaaburgh/dosbox-staging). It validates the Phase 2 x86-64 dynamic-core
pull requests #14 (C4), #15 (C1a), #16 (C2), #17 (C6), the measurement-only
integration build all4 (C4+C1a+C2+C6) and optionally #18 (C3) and #19 (C1b)
against main at 27b1b7c.

Start with summary.md (human readable) and summary.json (machine readable).

- metadata.json: host, CPU topology (L3 groups ~ CCDs), governor/boost,
  power/thermal state where readable, tool versions, runner arguments.
- sources.json: exact commit of every variant, whether it equals the Phase 2
  head, files changed relative to the baseline.
- builds.json: CMake preset, compiler, flags, the compile command of
  core_dyn_x86.cpp, SHA-256 of every binary, rebuild check.
- tests/verdicts.json and tests/<variant>/<core>/<TEST>.txt: raw output of
  every guest test (DOS .COM programs built with NASM from
  extras/jit-audit/tests) and the verdict. Expected values come from the Intel
  SDM, not from comparison with the normal core. KNOWN marks two documented
  deviations of the unmodified emulator: x87 #PF imprecision in the dynamic
  core (pushed EIP = block start, stale registers) and the normal core's
  wrong EIP for a faulting CALL push. Both must be identical to the baseline.
- bench/runs.csv: every Quake run (label, guest frames/seconds/fps, main
  thread CPU seconds for the demo window, affinity, sensors);
  bench/manifest.json (seed, schedule, binary hashes); bench/stats.json
  (bootstrap CIs, A/A placebo); bench/determinism.json.
- diagnostics/: logs of failed tests, deviating or failed benchmark runs (with
  one normal run of the same label for comparison), build logs.

Questions worth checking independently:
1. Do all variants meet the architectural expectations, with no output
   differing from the baseline?
2. Is the A/A placebo consistent with zero, and is its CI narrow enough to
   resolve the claimed effects?
3. Are the guest results deterministic and equal to the Phase 2 reference
   (969 frames, 326.7 fps; 326.9 fps for c6/all4, explained in PR #17)?
4. Are the speed-ups consistent with Phase 2 (Intel Emerald Rapids VM,
   Linux): C4 +11.1%, C1a +9.1%, C6 +8.8%, C2 +3.4%, all4 +37.1%?

Not included on purpose: game files, emulator binaries, JIT dumps, local paths
(replaced by <HOME>, <WORK>, <REPO>, <TMP>, <USER>, <HOST>).
"""


# --------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--mode', choices=list(MODES), default='quick',
                   help='quick: 3 benchmark blocks; full: 20 blocks (default quick)')
    p.add_argument('--with-optional', action='store_true', help='also validate PR #18 (C3) and #19 (C1b)')
    p.add_argument('--variants', default='', help='comma-separated subset of variant labels (base is always included)')
    p.add_argument('--work', type=Path, default=None, help='work directory (default: user cache dir)')
    p.add_argument('--out-dir', type=Path, default=None, help='where to write the ZIP (default: <work>/results)')
    p.add_argument('--source', type=Path, default=None, help='local clone to take refs from (default: this checkout)')
    p.add_argument('--remote', default=DEFAULT_REMOTE)
    p.add_argument('--offline', action='store_true', help='do not fetch; use refs of --source only')
    p.add_argument('--ref', action='append', default=[], help='override a variant: LABEL=REV')
    p.add_argument('--preset', default=None, help='CMake configure/build preset (default per OS)')
    p.add_argument('--cmake-arg', action='append', default=[], help='extra argument for the configure step')
    p.add_argument('--compat-shims', action='store_true',
                   help='disable two non-CPU calls missing in older system SDL3/FluidSynth (Debian 13)')
    p.add_argument('--build-mode', choices=['incremental', 'separate'], default='incremental',
                   help='incremental: one runner-owned worktree, variants checked out in turn (fast); '
                        'separate: one worktree and full build per variant')
    p.add_argument('--jobs', type=int, default=0, help='build parallelism (default: CMake/Ninja default)')
    p.add_argument('--configure-retries', type=int, default=2,
                   help='retries of the CMake configure step (vcpkg downloads), default 2')
    p.add_argument('--test-jobs', type=int, default=0)
    p.add_argument('--test-timeout', type=int, default=120)
    p.add_argument('--quake-dir', type=Path)
    p.add_argument('--quake-zip', type=Path)
    p.add_argument('--no-download', action='store_true')
    p.add_argument('--cpus', default='auto', help="'auto', 'none' or a CPU list such as 2,3")
    p.add_argument('--blocks', type=int, default=0)
    p.add_argument('--warmups', type=int, default=None)
    p.add_argument('--seed', type=int, default=None)
    p.add_argument('--bench-timeout', type=int, default=300)
    p.add_argument('--max-aa-resolution', type=float, default=3.0,
                   help='largest A/A CI half-width (percent) for which timing counts as conclusive')
    p.add_argument('--skip-build', action='store_true', help='reuse binaries from a previous run in --work')
    p.add_argument('--skip-tests', action='store_true')
    p.add_argument('--skip-bench', action='store_true')
    return p.parse_args(argv)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass
    args = parse_args(argv)
    gate = platform_gate()
    if gate is not None:
        return gate
    ctx = Ctx()
    ctx.args = args
    ctx.preset = args.preset or default_preset()
    ctx.work = (args.work or default_work()).resolve()
    ctx.store = ctx.work / 'git'
    ctx.source = (args.source or Path(subprocess.run(
        ['git', '-C', str(HERE), 'rev-parse', '--show-toplevel'], capture_output=True, text=True).stdout.strip()
        or HERE)).resolve()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    ctx.run_id = f'phase2-validation-{args.mode}-{stamp}'
    ctx.results = ctx.work / 'results' / ctx.run_id
    ctx.out_dir = (args.out_dir or ctx.work / 'results').resolve()
    ctx.results.mkdir(parents=True, exist_ok=True)
    ctx.status = {'run_id': ctx.run_id, 'started': now_utc(), 'stages': {}}
    ctx.summary = {'run_id': ctx.run_id, 'runner_version': RUNNER_VERSION, 'mode': args.mode, 'errors': []}
    ctx.runtime = ctx.work / 'runtime'
    ctx.cpus = []
    ctx.msvc_abi = False
    say(ctx, f'Phase 2 validation {ctx.run_id}')
    say(ctx, f'work directory: {ctx.work}')
    wanted = [v for v in VARIANTS if not v.optional or args.with_optional]
    if args.variants:
        keep = set(args.variants.split(',')) | {'base'}
        wanted = [v for v in VARIANTS if v.label in keep]
    exit_code = 0
    try:
        set_stage(ctx, 'metadata', 'running')
        meta = hostplat.host_metadata()
        meta['tools'] = check_prereqs(ctx)
        meta['runner'] = {'version': RUNNER_VERSION,
                          'commit': git_out(ctx, 'rev-parse', 'HEAD', cwd=HERE),
                          'dirty': bool(git_out(ctx, 'status', '--porcelain', '--', str(AUDIT), cwd=HERE)),
                          'argv': [a for a in sys.argv[1:]]}
        if args.cpus == 'auto':
            ctx.cpus = hostplat.pick_cpus(2)
        elif args.cpus != 'none':
            ctx.cpus = hostplat.parse_cpu_list(args.cpus)
        meta['bench_cpus'] = ctx.cpus
        meta['preset'] = ctx.preset
        ctx.summary['host'] = {'os': meta['os'], 'machine': meta['machine'], 'cpu_model': meta['cpu_model']}
        write_json(ctx.results / 'metadata.json', meta)
        (ctx.results / 'README_FOR_REVIEWER.md').write_text(REVIEWER_README, encoding='utf-8')
        set_stage(ctx, 'metadata', 'done')

        set_stage(ctx, 'sources', 'running')
        say(ctx, 'resolving variant commits ...')
        src_report = prepare_sources(ctx, wanted)
        write_json(ctx.results / 'sources.json', src_report)
        ctx.summary['sources'] = src_report
        wanted = [v for v in wanted if v.sha]
        for v in wanted:
            say(ctx, f'  {v.label:5s} {v.sha[:12]} ({v.ref}){"" if v.sha == v.phase2_sha else "  [differs from Phase 2 head]"}')
        if not any(v.label == 'base' for v in wanted):
            raise RuntimeError('baseline commit not available (fetch failed and not present locally)')
        set_stage(ctx, 'sources', 'done')

        set_stage(ctx, 'build', 'running')
        if args.skip_build:
            for v in wanted:
                cand = ctx.work / 'bins' / v.label / ('dosbox.exe' if hostplat.IS_WINDOWS else 'dosbox')
                v.binary = cand if cand.is_file() else None
            prev = ctx.work / 'last-builds.json'
            ctx.summary['builds'] = json.loads(prev.read_text()) if prev.is_file() else {}
            ctx.runtime = ctx.work / 'runtime'
        else:
            ctx.summary['builds'] = build_all(ctx, wanted)
            shutil.copy(ctx.results / 'builds.json', ctx.work / 'last-builds.json')
        write_json(ctx.results / 'builds.json', ctx.summary['builds'])
        set_stage(ctx, 'build', 'done')

        if not args.skip_tests:
            set_stage(ctx, 'tests', 'running')
            say(ctx, 'assembling guest tests ...')
            ctx.summary['test_binaries'] = assemble_tests(ctx)
            write_json(ctx.results / 'tests' / 'binaries.json', ctx.summary['test_binaries'])
            ctx.summary['tests'] = run_tests(ctx, wanted)
            set_stage(ctx, 'tests', 'done')

        if not args.skip_bench:
            set_stage(ctx, 'quake', 'running')
            say(ctx, 'preparing Quake 1.06 shareware files ...')
            game, prov = quake_files.prepare(ctx.work, args.quake_dir, args.quake_zip,
                                             download=not args.no_download, log=lambda m: say(ctx, m))
            write_json(ctx.results / 'quake.json', prov)
            set_stage(ctx, 'quake', 'done')
            set_stage(ctx, 'bench', 'running')
            base_cache = ctx.summary.get('builds', {}).get('variants', {}).get('base', {}).get('cmake_cache', {})
            ctx.msvc_abi = 'MSVC' in (base_cache.get('CMAKE_CXX_COMPILER_ID', ''),
                                      base_cache.get('CMAKE_CXX_SIMULATE_ID', ''))
            info = bench(ctx, wanted, game)
            ctx.summary['bench'] = analyse_bench(ctx, info)
            ctx.summary['bench']['params'] = {**info['params'], 'seed': info['seed'],
                                              'returncode': info['returncode']}
            set_stage(ctx, 'bench', 'done')
            meta['sensors_end'] = hostplat.sensor_sample(ctx.cpus)
            write_json(ctx.results / 'metadata.json', meta)
    except KeyboardInterrupt:
        ctx.summary['errors'].append({'stage': 'interrupted', 'error': 'KeyboardInterrupt'})
        exit_code = 130
    except SystemExit as e:
        ctx.summary['errors'].append({'stage': 'prerequisites', 'error': str(e)})
        print(e, file=sys.stderr)
        exit_code = 2
    except Exception as e:
        stage = next((k for k, v in reversed(list(ctx.status['stages'].items())) if v['status'] == 'running'), '?')
        ctx.summary['errors'].append({'stage': stage, 'error': f'{type(e).__name__}: {e}',
                                      'traceback': traceback.format_exc()})
        set_stage(ctx, stage, 'failed')
        say(ctx, f'ERROR in {stage}: {e}')
        exit_code = 1
    finally:
        try:
            ctx.status['finished'] = now_utc()
            write_json(ctx.results / 'status.json', ctx.status)
            summarize(ctx)
            counts = sanitize_tree(ctx)
            zpath = make_zip(ctx)
            hits = residual_check(ctx, zpath)
            say(ctx, f'sanitised: {counts}; residual local-path hits: {len(hits)}')
            if hits:
                say(ctx, 'WARNING: possible local paths left in: ' + '; '.join(hits[:5]))
            print()
            print((ctx.results / 'summary.md').read_text())
            print(f'Results archive: {zpath}')
        except Exception:
            traceback.print_exc()
            exit_code = exit_code or 1
    if exit_code == 0 and ctx.summary.get('overall', {}).get('verdict') != 'OK':
        exit_code = 5
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
