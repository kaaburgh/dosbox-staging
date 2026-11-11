# SPDX-License-Identifier: GPL-2.0-or-later
"""Host platform helpers for the JIT audit tools (Linux and Windows x86-64).

- main-thread and whole-process CPU time of a child process;
- CPU affinity;
- best-effort host metadata (CPU, topology/L3 groups, power and thermal state)
  and per-run sensor samples.

Everything that is not available on a host is reported as None or omitted;
nothing here needs root/administrator rights.
"""
from __future__ import annotations

import os
import platform
import re
import struct
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == 'win32'
IS_LINUX = sys.platform.startswith('linux')


def parse_cpu_list(text: str) -> list[int]:
    """'0-2,5' -> [0, 1, 2, 5]"""
    cpus = []
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            lo, hi = part.split('-', 1)
            cpus.extend(range(int(lo), int(hi) + 1))
        else:
            cpus.append(int(part))
    return sorted(set(cpus))


# --------------------------------------------------------------------------
# Windows API (ctypes), loaded lazily
# --------------------------------------------------------------------------
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)

    class _FILETIME(ctypes.Structure):
        _fields_ = [('lo', wintypes.DWORD), ('hi', wintypes.DWORD)]

        def value(self) -> int:
            return (self.hi << 32) | self.lo

    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ThreadID', wintypes.DWORD),
                    ('th32OwnerProcessID', wintypes.DWORD),
                    ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD)]

    class _SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [('ACLineStatus', ctypes.c_ubyte), ('BatteryFlag', ctypes.c_ubyte),
                    ('BatteryLifePercent', ctypes.c_ubyte),
                    ('SystemStatusFlag', ctypes.c_ubyte),
                    ('BatteryLifeTime', wintypes.DWORD),
                    ('BatteryFullLifeTime', wintypes.DWORD)]

    _TH32CS_SNAPTHREAD = 0x4
    _THREAD_QUERY_LIMITED_INFORMATION = 0x0800
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_SET_INFORMATION = 0x0200
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)]
    _k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)]
    _k32.OpenThread.restype = wintypes.HANDLE
    _k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetThreadTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FILETIME)] * 4
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FILETIME)] * 4
    _k32.QueryThreadCycleTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_ulonglong)]
    _k32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    _k32.GetSystemPowerStatus.argtypes = [ctypes.POINTER(_SYSTEM_POWER_STATUS)]
    _k32.GetLogicalProcessorInformationEx.argtypes = [
        ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]

    def _thread_ids(pid: int) -> list[int]:
        snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if not snap or snap == _INVALID_HANDLE:
            return []
        try:
            te = _THREADENTRY32()
            te.dwSize = ctypes.sizeof(te)
            tids = []
            ok = _k32.Thread32First(snap, ctypes.byref(te))
            while ok:
                if te.th32OwnerProcessID == pid:
                    tids.append(te.th32ThreadID)
                ok = _k32.Thread32Next(snap, ctypes.byref(te))
            return tids
        finally:
            _k32.CloseHandle(snap)

    def _thread_times(tid: int):
        """(creation, kernel+user in 100 ns, cycles) or None"""
        h = _k32.OpenThread(_THREAD_QUERY_LIMITED_INFORMATION, False, tid)
        if not h:
            return None
        try:
            c, e, k, u = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
            if not _k32.GetThreadTimes(h, ctypes.byref(c), ctypes.byref(e),
                                       ctypes.byref(k), ctypes.byref(u)):
                return None
            cyc = ctypes.c_ulonglong(0)
            if not _k32.QueryThreadCycleTime(h, ctypes.byref(cyc)):
                cyc.value = 0
            return c.value(), k.value() + u.value(), cyc.value
        finally:
            _k32.CloseHandle(h)


class ProcessClock:
    """CPU time of a running child process.

    main_ns(): CPU time of the emulator's main thread (Linux: tid == pid,
    from /proc/<pid>/task/<pid>/schedstat; Windows: the earliest-created
    thread, GetThreadTimes, kernel + user, clock-tick resolution).
    wait_ns(): run-queue wait of the main thread (Linux only, else None).
    cycles(): QueryThreadCycleTime of the main thread (Windows only).
    process_ns(): all threads of the process.
    """

    def __init__(self, pid: int):
        self.pid = pid
        self._main_tid = None

    def _win_main(self):
        if self._main_tid is None:
            best = None
            for tid in _thread_ids(self.pid):
                t = _thread_times(tid)
                if t and (best is None or t[0] < best[0]):
                    best = (t[0], tid)
            if best is None:
                raise OSError('no threads visible for pid %d' % self.pid)
            self._main_tid = best[1]
        return _thread_times(self._main_tid)

    def main_ns(self) -> int:
        if IS_WINDOWS:
            t = self._win_main()
            if t is None:
                raise OSError('GetThreadTimes failed')
            return t[1] * 100
        run_ns, _, _ = Path(f'/proc/{self.pid}/task/{self.pid}/schedstat').read_text().split()
        return int(run_ns)

    def wait_ns(self):
        if IS_WINDOWS:
            return None
        _, wait_ns, _ = Path(f'/proc/{self.pid}/task/{self.pid}/schedstat').read_text().split()
        return int(wait_ns)

    def cycles(self):
        if not IS_WINDOWS:
            return None
        t = self._win_main()
        return t[2] if t else None

    def process_ns(self):
        if IS_WINDOWS:
            h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, self.pid)
            if not h:
                return None
            try:
                c, e, k, u = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
                if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                            ctypes.byref(k), ctypes.byref(u)):
                    return None
                return (k.value() + u.value()) * 100
            finally:
                _k32.CloseHandle(h)
        total = 0
        for st in Path(f'/proc/{self.pid}/task').glob('*/schedstat'):
            try:
                total += int(st.read_text().split()[0])
            except (OSError, ValueError, IndexError):
                pass
        return total


def set_affinity(pid: int, cpus: list[int]) -> bool:
    if not cpus:
        return False
    if IS_WINDOWS:
        mask = 0
        for c in cpus:
            if c >= 64:
                return False  # processor groups are not handled
            mask |= 1 << c
        h = _k32.OpenProcess(_PROCESS_SET_INFORMATION | _PROCESS_QUERY_LIMITED_INFORMATION,
                             False, pid)
        if not h:
            return False
        try:
            return bool(_k32.SetProcessAffinityMask(h, mask))
        finally:
            _k32.CloseHandle(h)
    # Linux: the main thread exists from the start; threads created later
    # inherit its mask. Apply to every task present now as well.
    ok = True
    for task in Path(f'/proc/{pid}/task').glob('*'):
        try:
            os.sched_setaffinity(int(task.name), cpus)
        except OSError:
            ok = False
    return ok


def allowed_cpus() -> list[int]:
    if hasattr(os, 'sched_getaffinity'):
        return sorted(os.sched_getaffinity(0))
    return list(range(os.cpu_count() or 1))


def steal_ticks():
    if not IS_LINUX:
        return None
    try:
        return int(Path('/proc/stat').read_text().splitlines()[0].split()[8])
    except (OSError, IndexError, ValueError):
        return None


def load1():
    try:
        return round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        return None


def kill(proc) -> None:
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if proc.poll() is None:
            proc.kill()
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def popen_kwargs() -> dict:
    if IS_WINDOWS:
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


# --------------------------------------------------------------------------
# Sensors (per run) and metadata (once)
# --------------------------------------------------------------------------
def _read(p, default=None):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return default


def _rapl_uj():
    vals = {}
    for zone in sorted(Path('/sys/class/powercap').glob('intel-rapl:*')):
        if zone.name.count(':') != 1:
            continue  # top-level packages only
        v = _read(zone / 'energy_uj')
        if v is not None and v.isdigit():
            vals[zone.name] = int(v)
    return vals


def sensor_sample(cpus: list[int] | None = None) -> dict:
    """Cheap per-run snapshot. Values that cannot be read are omitted."""
    out = {}
    if IS_LINUX:
        temps = []
        for z in Path('/sys/class/thermal').glob('thermal_zone*'):
            t = _read(z / 'temp')
            if t and t.lstrip('-').isdigit():
                temps.append(int(t) / 1000)
        if temps:
            out['temp_c_max'] = round(max(temps), 1)
        freqs = []
        for c in (cpus or []):
            f = _read(f'/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq')
            if f and f.isdigit():
                freqs.append(int(f) / 1000)
        if freqs:
            out['cpu_mhz'] = round(sum(freqs) / len(freqs))
        rapl = _rapl_uj()
        if rapl:
            out['rapl_uj'] = rapl
    elif IS_WINDOWS:
        st = _SYSTEM_POWER_STATUS()
        if _k32.GetSystemPowerStatus(ctypes.byref(st)):
            out['ac_power'] = {0: 'battery', 1: 'ac'}.get(st.ACLineStatus, 'unknown')
    return out


def rapl_delta_j(before: dict, after: dict):
    a, b = before.get('rapl_uj'), after.get('rapl_uj')
    if not a or not b:
        return None
    total = 0
    for k, v in b.items():
        if k in a and v >= a[k]:
            total += v - a[k]
    return round(total / 1e6, 3)


def _linux_topology() -> dict:
    cpus = {}
    for d in sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*'), key=lambda p: int(p.name[3:])):
        n = int(d.name[3:])
        info = {'core_id': _read(d / 'topology/core_id'),
                'package_id': _read(d / 'topology/physical_package_id'),
                'smt_siblings': _read(d / 'topology/thread_siblings_list'),
                'online': _read(d / 'online', '1')}
        for idx in d.glob('cache/index*'):
            if _read(idx / 'level') == '3':
                info['l3_shared_with'] = _read(idx / 'shared_cpu_list')
                info['l3_size'] = _read(idx / 'size')
        info['governor'] = _read(d / 'cpufreq/scaling_governor')
        info['max_mhz'] = (lambda v: int(v) // 1000 if v and v.isdigit() else None)(
            _read(d / 'cpufreq/cpuinfo_max_freq'))
        cpus[n] = info
    l3_groups = sorted({v.get('l3_shared_with') for v in cpus.values() if v.get('l3_shared_with')})
    return {'cpus': cpus, 'l3_groups': l3_groups,
            'smt_active': _read('/sys/devices/system/cpu/smt/active'),
            'boost': _read('/sys/devices/system/cpu/cpufreq/boost'),
            'intel_no_turbo': _read('/sys/devices/system/cpu/intel_pstate/no_turbo'),
            'amd_pstate_status': _read('/sys/devices/system/cpu/amd_pstate/status'),
            'energy_perf_preference_cpu0': _read(
                '/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference'),
            'platform_profile': _read('/sys/firmware/acpi/platform_profile')}


def _windows_topology() -> dict:
    """GetLogicalProcessorInformationEx: physical cores (SMT masks) and L3
    groups. Processor groups other than 0 are reported but not decoded."""
    out = {}
    for rel, key in ((0, 'cores'), (2, 'caches')):
        size = wintypes.DWORD(0)
        _k32.GetLogicalProcessorInformationEx(rel, None, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not _k32.GetLogicalProcessorInformationEx(rel, buf, ctypes.byref(size)):
            out[key] = 'unavailable'
            continue
        raw = buf.raw[:size.value]
        items, off = [], 0
        while off + 8 <= len(raw):
            relationship, rsize = struct.unpack_from('<II', raw, off)
            if rsize == 0:
                break
            if relationship == 0:      # RelationProcessorCore
                flags = raw[off + 8]
                eff = raw[off + 9]
                mask, group = struct.unpack_from('<QH', raw, off + 32)
                items.append({'smt': bool(flags & 1), 'efficiency_class': eff,
                              'group': group, 'mask': hex(mask)})
            elif relationship == 2:    # RelationCache
                level = raw[off + 8]
                csize, ctype = struct.unpack_from('<II', raw, off + 12)
                mask, group = struct.unpack_from('<QH', raw, off + 40)
                if level == 3:
                    items.append({'level': level, 'size_kib': csize // 1024,
                                  'type': ctype, 'group': group, 'mask': hex(mask)})
            off += rsize
        out[key] = items
    return out


def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cpu_model() -> str:
    if IS_LINUX:
        for line in (_read('/proc/cpuinfo', '') or '').splitlines():
            if line.startswith('model name'):
                return line.split(':', 1)[1].strip()
    if IS_WINDOWS:
        v = _run(['powershell', '-NoProfile', '-Command',
                  '(Get-CimInstance Win32_Processor | Select-Object -First 1).Name'])
        if v:
            return v
    return platform.processor()


def host_metadata() -> dict:
    meta = {'os': platform.system(), 'os_release': platform.release(),
            'os_version': platform.version(), 'machine': platform.machine(),
            'python': platform.python_version(), 'cpu_model': cpu_model(),
            'logical_cpus': os.cpu_count(), 'allowed_cpus': allowed_cpus()}
    if IS_LINUX:
        meta['topology'] = _linux_topology()
        meta['virtualization'] = _run(['systemd-detect-virt']) or None
        meta['kernel_cmdline_isolcpus'] = next(
            (w for w in (_read('/proc/cmdline', '') or '').split() if w.startswith(('isolcpus', 'nohz_full'))), None)
        meta['sensors_start'] = sensor_sample(allowed_cpus())
        meta['rapl_readable'] = bool(_rapl_uj())
        try:
            meta['distro'] = dict(l.split('=', 1) for l in Path('/etc/os-release').read_text().splitlines()
                                  if '=' in l and l.split('=', 1)[0] in ('NAME', 'VERSION_ID', 'PRETTY_NAME'))
        except OSError:
            pass
    elif IS_WINDOWS:
        try:
            meta['topology'] = _windows_topology()
        except Exception as e:  # best effort, never fatal
            meta['topology'] = f'unavailable: {type(e).__name__}'
        meta['power_scheme'] = _run(['powercfg', '/getactivescheme'])
        meta['sensors_start'] = sensor_sample()
        meta['cpu_clock_mhz'] = _run(['powershell', '-NoProfile', '-Command',
                                      '$p = Get-CimInstance Win32_Processor | Select-Object -First 1; '
                                      '"$($p.CurrentClockSpeed) / $($p.MaxClockSpeed)"'])
        meta['thermal_zones_c'] = _run(['powershell', '-NoProfile', '-Command',
                                        '(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature '
                                        '-ErrorAction Stop | ForEach-Object { [math]::Round($_.CurrentTemperature / 10 - 273.15, 1) }) -join ","'])
    return meta


def _lowest_bit(mask: int) -> int:
    return (mask & -mask).bit_length() - 1


def pick_cpus(n: int = 2) -> list[int]:
    """Choose n logical CPUs for the emulator: performance cores only on
    hybrid CPUs, distinct physical cores in one L3 group, avoiding CPU 0,
    preferring the highest-numbered group."""
    allowed = allowed_cpus()
    if IS_LINUX:
        pcores = _read('/sys/devices/cpu_core/cpus')  # Intel hybrid: P-cores
        if pcores:
            p_set = set(parse_cpu_list(pcores))
            allowed = [c for c in allowed if c in p_set] or allowed
        topo = _linux_topology()['cpus']
        groups = {}
        for c in allowed:
            info = topo.get(c, {})
            groups.setdefault(info.get('l3_shared_with') or 'all', []).append(c)
        for key in sorted(groups, key=lambda k: -max(groups[k])):
            seen_cores, pick = set(), []
            for c in sorted(groups[key], reverse=True):
                if c == 0:
                    continue
                core = (topo.get(c, {}).get('package_id'), topo.get(c, {}).get('core_id'))
                if core in seen_cores:
                    continue
                seen_cores.add(core)
                pick.append(c)
                if len(pick) == n:
                    return sorted(pick)
    if IS_WINDOWS:
        try:
            topo = _windows_topology()
            cores = [c for c in topo.get('cores', []) if isinstance(c, dict) and c['group'] == 0]
            top = max((c['efficiency_class'] for c in cores), default=0)
            firsts = sorted(_lowest_bit(int(c['mask'], 16)) for c in cores
                            if c['efficiency_class'] == top)
            l3 = [int(c['mask'], 16) for c in topo.get('caches', []) if isinstance(c, dict) and c['group'] == 0]
            for mask in sorted(l3, key=lambda m: -m.bit_length()):
                pick = [c for c in reversed(firsts) if c != 0 and mask >> c & 1][:n]
                if len(pick) == n:
                    return sorted(pick)
            pick = [c for c in reversed(firsts) if c != 0][:n]
            if len(pick) == n:
                return sorted(pick)
        except Exception:
            pass
    cand = [c for c in allowed if c != 0] or allowed
    return sorted(cand[-n:])


def cpu_name_safe(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()
