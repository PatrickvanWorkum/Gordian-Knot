"""This process's CPU and RAM share of the machine.

Uses Windows counters so Gordian does not need an extra package. CPU needs two
samples a short time apart; the first call returns 0.0 until a second sample.
RAM is working set over total physical memory.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.GetProcessTimes.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
]
kernel32.GetProcessTimes.restype = wintypes.BOOL
kernel32.GetSystemTimes.argtypes = [
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
]
kernel32.GetSystemTimes.restype = wintypes.BOOL
kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
psapi.GetProcessMemoryInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
    wintypes.DWORD,
]
psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def _filetime_int(value: FILETIME) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


class ProcessLoad:
    """Samples this process. Call sample() while the window is open."""

    def __init__(self) -> None:
        self._process = kernel32.GetCurrentProcess()
        self._last_proc = 0
        self._last_system = 0
        self.cpu_percent = 0.0
        self.ram_percent = 0.0

    def sample(self) -> tuple[float, float]:
        self.cpu_percent = self._cpu_percent()
        self.ram_percent = self._ram_percent()
        return self.cpu_percent, self.ram_percent

    def _cpu_percent(self) -> float:
        created = FILETIME()
        exited = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        idle = FILETIME()
        sys_kernel = FILETIME()
        sys_user = FILETIME()
        if not kernel32.GetProcessTimes(
            self._process, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return self.cpu_percent
        if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(sys_kernel), ctypes.byref(sys_user)):
            return self.cpu_percent
        proc = _filetime_int(kernel) + _filetime_int(user)
        system = _filetime_int(sys_kernel) + _filetime_int(sys_user)
        if self._last_system <= 0 or system <= self._last_system:
            self._last_proc = proc
            self._last_system = system
            return 0.0
        proc_delta = proc - self._last_proc
        system_delta = system - self._last_system
        self._last_proc = proc
        self._last_system = system
        if system_delta <= 0 or proc_delta < 0:
            return 0.0
        return min(100.0, 100.0 * proc_delta / system_delta)

    def _ram_percent(self) -> float:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not psapi.GetProcessMemoryInfo(self._process, ctypes.byref(counters), counters.cb):
            return self.ram_percent
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) or status.ullTotalPhys <= 0:
            return self.ram_percent
        return min(100.0, 100.0 * counters.WorkingSetSize / status.ullTotalPhys)


def format_load(cpu_percent: float, ram_percent: float) -> str:
    return f"CPU: {cpu_percent:.1f}%   RAM: {ram_percent:.1f}%"
