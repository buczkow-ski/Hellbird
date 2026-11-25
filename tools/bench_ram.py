#!/usr/bin/env python3
"""Hellbird vs Helium memory benchmarking and profiling CLI (P0).

Measures real per-process and aggregate memory footprint on Windows and Linux
using OS-accurate metrics that avoid double-counting shared libraries:
  - Windows: Private Working Set (PROCESS_MEMORY_COUNTERS_EX2), Private Commit,
    Peak Commit, plus optional GPU Total Committed memory and system RAM pressure.
  - Linux:   PSS (Proportional Set Size) and Private RSS from /proc/<pid>/smaps_rollup,
    plus VmData and VmHWM from /proc/<pid>/status.

Subcommands:
  snapshot  Inspect currently running Hellbird and/or Helium process trees.
  bench     Launch browser(s) with fresh temporary profiles across tab counts,
            sample after load / settle / idle intervals, and compare totals.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Iterable

HELLBIRD_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "hellbird",
        "hellbird.exe",
        "ladybird",
        "ladybird.exe",
        "webcontent",
        "webcontent.exe",
        "compositor",
        "compositor.exe",
        "requestserver",
        "requestserver.exe",
        "imagedecoder",
        "imagedecoder.exe",
        "webworker",
        "webworker.exe",
        "hub",
        "hub.exe",
        "ladybirdhub",
        "ladybirdhub.exe",
        "hellbirdhub",
        "hellbirdhub.exe",
        "audioserver",
        "audioserver.exe",
        "mediaserver",
        "mediaserver.exe",
        "videodecoderserver",
        "videodecoderserver.exe",
        "videodecoderser",  # Linux /proc/<pid>/comm 15-char truncation
        "webrtcclient",
        "webrtcclient.exe",
        "systemprobe",
        "systemprobe.exe",
        "processreaper",
        "processreaper.exe",
    }
)

HELIUM_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "helium",
        "helium.exe",
    }
)

DEFAULT_BENCH_URLS: tuple[str, ...] = (
    "https://example.com/",
    "https://en.wikipedia.org/wiki/Web_browser",
    "https://github.com/LadybirdBrowser/ladybird",
    "https://news.ycombinator.com/",
    "https://developer.mozilla.org/en-US/docs/Web/HTML",
    "https://www.rust-lang.org/",
    "https://sqlite.org/index.html",
    "https://www.python.org/",
    "https://lobste.rs/",
    "https://httpbin.org/html",
)


@dataclass(frozen=True)
class ProcessSample:
    pid: int
    name: str
    role: str
    private_ws_mb: float
    commit_mb: float
    peak_commit_mb: float
    pss_mb: float | None = None
    gpu_committed_mb: float | None = None


@dataclass(frozen=True)
class SystemMemoryInfo:
    total_ram_mb: float
    available_ram_mb: float
    commit_total_mb: float | None = None
    commit_limit_mb: float | None = None


@dataclass(frozen=True)
class BrowserSnapshot:
    browser: str
    timestamp_epoch: float
    process_count: int
    total_private_ws_mb: float
    total_commit_mb: float
    total_peak_commit_mb: float
    total_pss_mb: float | None
    total_gpu_committed_mb: float | None
    system: SystemMemoryInfo
    processes: list[ProcessSample]


def classify_process_role(name: str, cmdline: str = "") -> str:
    lower = name.lower()
    if lower.endswith(".exe"):
        lower = lower[:-4]
    if lower in ("hellbird", "ladybird"):
        return "browser-ui"
    if lower in ("hub", "hellbirdhub", "ladybirdhub"):
        return "hub"
    if lower in (
        "webcontent",
        "compositor",
        "requestserver",
        "imagedecoder",
        "webworker",
        "audioserver",
        "mediaserver",
        "webrtcclient",
        "systemprobe",
        "processreaper",
    ):
        return lower
    if lower in ("videodecoderserver", "videodecoderser"):
        return "videodecoderserver"
    if lower == "helium":
        if "--type=renderer" in cmdline:
            return "renderer"
        if "--type=gpu-process" in cmdline:
            return "gpu"
        if "--type=utility" in cmdline:
            if "network.mojom.NetworkService" in cmdline:
                return "network"
            return "utility"
        return "browser-ui"
    return lower


def parse_linux_smaps_rollup(text: str) -> tuple[float, float]:
    """Parse Linux /proc/<pid>/smaps_rollup and return (pss_mb, private_rss_mb)."""
    pss_kb = 0
    priv_clean_kb = 0
    priv_dirty_kb = 0
    for line in text.splitlines():
        if line.startswith("Pss:"):
            parts = line.split()
            if len(parts) >= 2:
                pss_kb = int(parts[1])
        elif line.startswith("Private_Clean:"):
            parts = line.split()
            if len(parts) >= 2:
                priv_clean_kb = int(parts[1])
        elif line.startswith("Private_Dirty:"):
            parts = line.split()
            if len(parts) >= 2:
                priv_dirty_kb = int(parts[1])
    return round(pss_kb / 1024.0, 2), round((priv_clean_kb + priv_dirty_kb) / 1024.0, 2)


def _run_powershell_utf8(ps_body: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
    wrapped = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + ps_body
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", wrapped],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )


def _sample_system_memory_windows() -> SystemMemoryInfo:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    total_mb = round(status.ullTotalPhys / (1024 * 1024), 1)
    avail_mb = round(status.ullAvailPhys / (1024 * 1024), 1)
    commit_limit_mb = round(status.ullTotalPageFile / (1024 * 1024), 1)
    commit_total_mb = round((status.ullTotalPageFile - status.ullAvailPageFile) / (1024 * 1024), 1)
    return SystemMemoryInfo(
        total_ram_mb=total_mb,
        available_ram_mb=avail_mb,
        commit_total_mb=commit_total_mb,
        commit_limit_mb=commit_limit_mb,
    )


def _query_windows_process_memory(pid: int) -> tuple[float, float, float] | None:
    """Return (private_ws_mb, commit_mb, peak_commit_mb) via GetProcessMemoryInfo."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class PROCESS_MEMORY_COUNTERS_EX2(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
            ("PrivateWorkingSetSize", ctypes.c_size_t),
            ("SharedCommitUsage", ctypes.c_ulonglong),
        ]

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        psapi = ctypes.windll.psapi
        ex2 = PROCESS_MEMORY_COUNTERS_EX2()
        ex2.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX2)
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(ex2), ex2.cb):
            return (
                round(ex2.PrivateWorkingSetSize / (1024 * 1024), 2),
                round(ex2.PrivateUsage / (1024 * 1024), 2),
                round(ex2.PeakPagefileUsage / (1024 * 1024), 2),
            )
        ex = PROCESS_MEMORY_COUNTERS_EX()
        ex.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(ex), ex.cb):
            return (
                round(ex.WorkingSetSize / (1024 * 1024), 2),
                round(ex.PrivateUsage / (1024 * 1024), 2),
                round(ex.PeakPagefileUsage / (1024 * 1024), 2),
            )
        return None
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _sample_windows_gpu_committed(pids: Iterable[int]) -> dict[int, float]:
    pid_set = set(pids)
    if not pid_set:
        return {}
    ps_cmd = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$c=(Get-Counter '\\GPU Process Memory(*)\\Total Committed').CounterSamples;"
        "foreach($s in $c){"
        "  if($s.InstanceName -match 'pid_(\\d+)_'){"
        "    $p=[int]$Matches[1];"
        "    Write-Output \"$p $([math]::Round($s.CookedValue/1MB,2))\""
        "  }"
        "}"
    )
    try:
        proc = _run_powershell_utf8(ps_cmd, timeout=10)
    except subprocess.TimeoutExpired:
        return {}
    gpu_by_pid: dict[int, float] = {}
    if proc.returncode != 0:
        return gpu_by_pid
    for line in proc.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
            mb = float(parts[1])
        except ValueError:
            continue
        if pid in pid_set:
            gpu_by_pid[pid] = round(gpu_by_pid.get(pid, 0.0) + mb, 2)
    return gpu_by_pid


def _collect_windows_processes(
    allowed_names: frozenset[str],
    *,
    root_pid: int | None = None,
    include_gpu: bool = True,
) -> list[ProcessSample]:
    ps_cmd = (
        "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        proc = _run_powershell_utf8(ps_cmd, timeout=15)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out querying Windows process list") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"Failed querying Windows process list: {proc.stderr.strip()}")
    raw = json.loads(proc.stdout or "[]")
    if isinstance(raw, dict):
        raw = [raw]

    children_map: dict[int, list[int]] = {}
    by_pid: dict[int, dict] = {}
    for entry in raw:
        pid = int(entry["ProcessId"])
        ppid = int(entry.get("ParentProcessId") or 0)
        by_pid[pid] = entry
        children_map.setdefault(ppid, []).append(pid)

    target_pids: list[int] = []
    if root_pid is not None:
        stack = [root_pid]
        seen: set[int] = set()
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in by_pid:
                continue
            seen.add(cur)
            target_pids.append(cur)
            stack.extend(children_map.get(cur, []))
    else:
        for pid, entry in by_pid.items():
            name = (entry.get("Name") or "").lower()
            if name in allowed_names:
                target_pids.append(pid)

    gpu_map = _sample_windows_gpu_committed(target_pids) if include_gpu else {}
    samples: list[ProcessSample] = []
    for pid in sorted(target_pids):
        entry = by_pid[pid]
        name = entry.get("Name") or "unknown"
        cmdline = entry.get("CommandLine") or ""
        mem = _query_windows_process_memory(pid)
        if mem is None:
            continue
        priv_ws, commit, peak_commit = mem
        samples.append(
            ProcessSample(
                pid=pid,
                name=name,
                role=classify_process_role(name, cmdline),
                private_ws_mb=priv_ws,
                commit_mb=commit,
                peak_commit_mb=peak_commit,
                pss_mb=None,
                gpu_committed_mb=gpu_map.get(pid),
            )
        )
    return samples


def _sample_system_memory_linux() -> SystemMemoryInfo:
    meminfo: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        parts = line.split(":")
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        val_parts = parts[1].strip().split()
        if val_parts:
            try:
                meminfo[key] = int(val_parts[0])
            except ValueError:
                pass
    total_mb = round(meminfo.get("MemTotal", 0) / 1024.0, 1)
    avail_mb = round(meminfo.get("MemAvailable", 0) / 1024.0, 1)
    commit_total = round(meminfo.get("Committed_AS", 0) / 1024.0, 1)
    commit_limit = round(meminfo.get("CommitLimit", 0) / 1024.0, 1)
    return SystemMemoryInfo(
        total_ram_mb=total_mb,
        available_ram_mb=avail_mb,
        commit_total_mb=commit_total,
        commit_limit_mb=commit_limit,
    )


def _collect_linux_processes(
    allowed_names: frozenset[str],
    *,
    root_pid: int | None = None,
) -> list[ProcessSample]:
    proc_root = Path("/proc")
    entries: dict[int, tuple[int, str, str]] = {}
    children_map: dict[int, list[int]] = {}
    for child in proc_root.iterdir():
        if not child.name.isdigit():
            continue
        pid = int(child.name)
        try:
            comm = (child / "comm").read_text(encoding="utf-8", errors="replace").strip()
            cmdline = (child / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
            stat_text = (child / "stat").read_text(encoding="utf-8", errors="replace")
            rparen = stat_text.rfind(")")
            ppid = int(stat_text[rparen + 2 :].split()[1]) if rparen != -1 else 0
        except OSError:
            continue
        entries[pid] = (ppid, comm, cmdline)
        children_map.setdefault(ppid, []).append(pid)

    target_pids: list[int] = []
    if root_pid is not None:
        stack = [root_pid]
        seen: set[int] = set()
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in entries:
                continue
            seen.add(cur)
            target_pids.append(cur)
            stack.extend(children_map.get(cur, []))
    else:
        for pid, (_, comm, _) in entries.items():
            if comm.lower() in allowed_names:
                target_pids.append(pid)

    samples: list[ProcessSample] = []
    for pid in sorted(target_pids):
        _, comm, cmdline = entries[pid]
        try:
            rollup = (proc_root / str(pid) / "smaps_rollup").read_text(encoding="utf-8", errors="replace")
            pss_mb, priv_mb = parse_linux_smaps_rollup(rollup)
            status = (proc_root / str(pid) / "status").read_text(encoding="utf-8", errors="replace")
            vm_data_mb = priv_mb
            vm_hwm_mb = priv_mb
            for line in status.splitlines():
                if line.startswith("VmData:"):
                    vm_data_mb = round(int(line.split()[1]) / 1024.0, 2)
                elif line.startswith("VmHWM:"):
                    vm_hwm_mb = round(int(line.split()[1]) / 1024.0, 2)
        except OSError:
            continue
        samples.append(
            ProcessSample(
                pid=pid,
                name=comm,
                role=classify_process_role(comm, cmdline),
                private_ws_mb=priv_mb,
                commit_mb=vm_data_mb,
                peak_commit_mb=vm_hwm_mb,
                pss_mb=pss_mb,
                gpu_committed_mb=None,
            )
        )
    return samples


def collect_snapshot(
    browser: str,
    *,
    root_pid: int | None = None,
    include_gpu: bool = True,
) -> BrowserSnapshot:
    names = HELLBIRD_PROCESS_NAMES if browser == "hellbird" else HELIUM_PROCESS_NAMES
    system_name = platform.system()
    if system_name == "Windows":
        sys_mem = _sample_system_memory_windows()
        procs = _collect_windows_processes(names, root_pid=root_pid, include_gpu=include_gpu)
    elif system_name == "Linux":
        sys_mem = _sample_system_memory_linux()
        procs = _collect_linux_processes(names, root_pid=root_pid)
    else:
        raise RuntimeError(f"Unsupported OS for live memory sampling: {system_name}")

    procs.sort(key=lambda p: (p.commit_mb, p.private_ws_mb), reverse=True)
    pss_values = [p.pss_mb for p in procs if p.pss_mb is not None]
    gpu_values = [p.gpu_committed_mb for p in procs if p.gpu_committed_mb is not None]
    return BrowserSnapshot(
        browser=browser,
        timestamp_epoch=round(time.time(), 2),
        process_count=len(procs),
        total_private_ws_mb=round(sum(p.private_ws_mb for p in procs), 2),
        total_commit_mb=round(sum(p.commit_mb for p in procs), 2),
        total_peak_commit_mb=round(sum(p.peak_commit_mb for p in procs), 2),
        total_pss_mb=round(sum(pss_values), 2) if pss_values else None,
        total_gpu_committed_mb=round(sum(gpu_values), 2) if gpu_values else None,
        system=sys_mem,
        processes=procs,
    )


def format_snapshot_table(snap: BrowserSnapshot) -> str:
    is_linux = snap.total_pss_mb is not None
    col_ws = "PrivRSS" if is_linux else "PrivWS"
    col_commit = "VmData" if is_linux else "Commit"
    col_peak = "VmHWM" if is_linux else "Peak"
    lines = [
        f"=== {snap.browser.upper()} ({snap.process_count} processes) ===",
        f"System RAM: {snap.system.available_ram_mb:.0f} / {snap.system.total_ram_mb:.0f} MB free"
        + (
            f" | Commit: {snap.system.commit_total_mb:.0f} / {snap.system.commit_limit_mb:.0f} MB"
            if snap.system.commit_total_mb is not None and snap.system.commit_limit_mb is not None
            else ""
        ),
        f"{'PID':>7}  {'Role':<18} {'Name':<20} {col_ws:>8} {col_commit:>8} {col_peak:>8} {'GPU':>7}",
        "-" * 83,
    ]
    for p in snap.processes:
        gpu_str = f"{p.gpu_committed_mb:7.1f}" if p.gpu_committed_mb is not None else "      -"
        lines.append(
            f"{p.pid:>7}  {p.role:<18} {p.name:<20} {p.private_ws_mb:8.1f} "
            f"{p.commit_mb:8.1f} {p.peak_commit_mb:8.1f} {gpu_str}"
        )
    lines.append("-" * 83)
    gpu_tot = f"{snap.total_gpu_committed_mb:7.1f}" if snap.total_gpu_committed_mb is not None else "      -"
    lines.append(
        f"{'TOTAL':>7}  {'':<18} {'':<20} {snap.total_private_ws_mb:8.1f} "
        f"{snap.total_commit_mb:8.1f} {snap.total_peak_commit_mb:8.1f} {gpu_tot}"
    )
    if snap.total_pss_mb is not None:
        lines.append(f"Total Linux PSS: {snap.total_pss_mb:.1f} MB")
    return "\n".join(lines)


def check_budgets(
    snap: BrowserSnapshot,
    *,
    max_commit_mb: float | None = None,
    max_private_ws_mb: float | None = None,
    max_processes: int | None = None,
) -> list[str]:
    has_any_budget = any(x is not None for x in (max_commit_mb, max_private_ws_mb, max_processes))
    if not has_any_budget:
        return []
    if snap.process_count == 0:
        return [f"{snap.browser}: no running processes found while verifying memory budgets"]
    failures: list[str] = []
    if max_commit_mb is not None and snap.total_commit_mb > max_commit_mb:
        failures.append(f"{snap.browser}: commit {snap.total_commit_mb:.1f} MB > budget {max_commit_mb:.1f} MB")
    if max_private_ws_mb is not None and snap.total_private_ws_mb > max_private_ws_mb:
        failures.append(
            f"{snap.browser}: private WS {snap.total_private_ws_mb:.1f} MB > budget {max_private_ws_mb:.1f} MB"
        )
    if max_processes is not None and snap.process_count > max_processes:
        failures.append(f"{snap.browser}: process count {snap.process_count} > budget {max_processes}")
    return failures


def _build_bench_command(browser: str, executable: Path, profile_dir: Path, urls: list[str]) -> list[str]:
    cmd = [str(executable)]
    if browser == "helium":
        cmd.extend(
            [
                f"--user-data-dir={profile_dir.resolve()}",
                "--no-first-run",
                "--no-default-browser-check",
            ]
        )
    else:
        cmd.extend(["--profile-path", str(profile_dir.resolve())])
    cmd.extend(urls)
    return cmd


def _run_browser_bench(
    browser: str,
    executable: Path,
    urls: list[str],
    settle_seconds: int,
    idle_seconds: int,
    include_gpu: bool,
) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"hellbird-bench-{browser}-") as tmp:
        profile_dir = Path(tmp) / "profile"
        profile_dir.mkdir()
        cmd = _build_bench_command(browser, executable, profile_dir, urls)

        proc = subprocess.Popen(cmd, env=os.environ.copy())
        try:
            time.sleep(max(3, settle_seconds))
            if proc.poll() is not None:
                raise RuntimeError(
                    f"{browser} exited early (code {proc.returncode}); it may have handed off to an existing instance."
                )
            at_load = collect_snapshot(browser, root_pid=proc.pid, include_gpu=include_gpu)
            if at_load.process_count == 0:
                raise RuntimeError(f"{browser} produced 0 observable processes for PID {proc.pid}.")
            result = {
                "browser": browser,
                "tab_count": len(urls),
                "at_load": asdict(at_load),
            }
            if idle_seconds > 0:
                time.sleep(idle_seconds)
                if proc.poll() is not None:
                    raise RuntimeError(f"{browser} exited during idle window (code {proc.returncode}).")
                after_idle = collect_snapshot(browser, root_pid=proc.pid, include_gpu=include_gpu)
                result["after_idle"] = asdict(after_idle)
            return result
        finally:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            shutil.rmtree(profile_dir, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hellbird vs Helium RAM benchmark & profiler")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="Measure currently running Hellbird and/or Helium processes")
    p_snap.add_argument("--target", choices=("hellbird", "helium", "both"), default="both")
    p_snap.add_argument("--no-gpu", action="store_true", help="Skip Windows GPU counter query")
    p_snap.add_argument("--json", type=Path, help="Write raw snapshot JSON to file")
    p_snap.add_argument("--max-commit-mb", type=float, default=None)
    p_snap.add_argument("--max-private-ws-mb", type=float, default=None)
    p_snap.add_argument("--max-processes", type=int, default=None)

    p_bench = sub.add_parser("bench", help="Launch browsers with fresh profiles and compare RAM")
    p_bench.add_argument("--hellbird", type=Path, help="Path to Hellbird executable")
    p_bench.add_argument("--helium", type=Path, help="Path to Helium executable")
    p_bench.add_argument("--tabs", default="1,5,10", help="Comma-separated tab counts (default: 1,5,10)")
    p_bench.add_argument("--settle-seconds", type=int, default=15, help="Seconds to wait after launch")
    p_bench.add_argument("--idle-seconds", type=int, default=60, help="Additional idle seconds before 2nd sample")
    p_bench.add_argument("--no-gpu", action="store_true")
    p_bench.add_argument("--json", type=Path, help="Write benchmark report JSON to file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "snapshot":
        targets = ("hellbird", "helium") if args.target == "both" else (args.target,)
        snaps: list[BrowserSnapshot] = []
        budget_failures: list[str] = []
        for target in targets:
            snap = collect_snapshot(target, include_gpu=not args.no_gpu)
            snaps.append(snap)
            print(format_snapshot_table(snap))
            print()
            if target == "hellbird":
                budget_failures.extend(
                    check_budgets(
                        snap,
                        max_commit_mb=args.max_commit_mb,
                        max_private_ws_mb=args.max_private_ws_mb,
                        max_processes=args.max_processes,
                    )
                )
        if args.json:
            args.json.write_text(
                json.dumps([asdict(s) for s in snaps], indent=2) + "\n",
                encoding="utf-8",
            )
        if budget_failures:
            for msg in budget_failures:
                print(f"BUDGET EXCEEDED: {msg}", file=sys.stderr)
            return 2
        return 0

    if args.command == "bench":
        if not args.hellbird and not args.helium:
            print("Provide at least one of --hellbird or --helium", file=sys.stderr)
            return 1
        tab_counts = [int(x.strip()) for x in args.tabs.split(",") if x.strip()]
        reports: list[dict] = []
        for count in tab_counts:
            urls = [DEFAULT_BENCH_URLS[i % len(DEFAULT_BENCH_URLS)] for i in range(count)]
            for name, exe in (("hellbird", args.hellbird), ("helium", args.helium)):
                if not exe:
                    continue
                print(f"Benchmarking {name} with {count} tab(s)...")
                rep = _run_browser_bench(
                    name,
                    exe,
                    urls,
                    settle_seconds=args.settle_seconds,
                    idle_seconds=args.idle_seconds,
                    include_gpu=not args.no_gpu,
                )
                reports.append(rep)
                load = rep["at_load"]
                print(
                    f"  at_load: {load['process_count']} procs, "
                    f"PrivWS={load['total_private_ws_mb']:.1f} MB, "
                    f"Commit={load['total_commit_mb']:.1f} MB"
                )
                if "after_idle" in rep:
                    idle = rep["after_idle"]
                    print(
                        f"  after_idle: {idle['process_count']} procs, "
                        f"PrivWS={idle['total_private_ws_mb']:.1f} MB, "
                        f"Commit={idle['total_commit_mb']:.1f} MB"
                    )
        if args.json:
            args.json.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
