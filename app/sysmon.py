# -*- coding: utf-8 -*-
"""系统健康指标采集。

后台线程定时采样，主线程只读快照，不阻塞界面。
GPU 走 pynvml（有 NVIDIA 卡才有），CPU 温度在 Windows 上多数机器读不到，
读到就读，读不到就显示 --。
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass, field

import psutil

try:
    import pynvml

    pynvml.nvmlInit()
    _NVML_OK = True
except Exception:
    _NVML_OK = False


@dataclass
class GpuInfo:
    name: str = ""
    util: float = 0.0
    temp: float = 0.0
    mem_used: float = 0.0   # MB
    mem_total: float = 0.0  # MB
    power: float = 0.0      # W


@dataclass
class DiskInfo:
    mount: str = ""
    percent: float = 0.0
    used_gb: float = 0.0
    total_gb: float = 0.0


@dataclass
class ProcInfo:
    name: str = ""
    value: float = 0.0


@dataclass
class Snapshot:
    ts: float = 0.0
    cpu: float = 0.0
    cpu_freq: float = 0.0
    cpu_cores: int = 0
    cpu_temp: float | None = None
    ram: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    swap: float = 0.0
    disks: list[DiskInfo] = field(default_factory=list)
    gpus: list[GpuInfo] = field(default_factory=list)
    net_up: float = 0.0      # KB/s
    net_down: float = 0.0    # KB/s
    battery: float | None = None
    charging: bool = False
    uptime_h: float = 0.0
    top_cpu: list[ProcInfo] = field(default_factory=list)
    top_mem: list[ProcInfo] = field(default_factory=list)

    # ---------- 综合评分 ----------
    def score(self) -> int:
        """0-100 的健康分，用来给鱼一个直观状态。"""
        s = 100.0
        s -= max(0.0, self.cpu - 40) * 0.35
        s -= max(0.0, self.ram - 50) * 0.45
        if self.cpu_temp:
            s -= max(0.0, self.cpu_temp - 70) * 0.8
        for g in self.gpus:
            if g.temp:
                s -= max(0.0, g.temp - 75) * 0.9
        for d in self.disks:
            if d.percent > 88:
                s -= (d.percent - 88) * 1.2
        if self.battery is not None and self.battery < 20 and not self.charging:
            s -= (20 - self.battery) * 0.8
        return int(max(0, min(100, s)))

    def verdict(self) -> str:
        s = self.score()
        if s >= 88:
            return "状态极佳"
        if s >= 72:
            return "一切正常"
        if s >= 55:
            return "有点吃力"
        if s >= 35:
            return "需要休息"
        return "快撑不住了"


class Monitor:
    """后台采样器。"""

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self._lock = threading.Lock()
        self._snap = Snapshot()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_net: tuple[float, float, float] | None = None
        self._disk_cache: list[DiskInfo] = []
        self._disk_ts = 0.0

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread is not None:
            return
        psutil.cpu_percent(interval=None)  # 第一次调用总是 0，先预热
        try:  # 进程 CPU 百分比也是基于两次采样的差值，先预热一遍
            for p in psutil.process_iter(["name"]):
                try:
                    p.cpu_percent(None)
                except Exception:
                    pass
        except Exception:
            pass
        self._thread = threading.Thread(target=self._run, daemon=True, name="sysmon")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None

    @property
    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snap

    # ---------- 采样 ----------
    def _run(self) -> None:
        # psutil 的 cpu_percent 状态是线程局部的，必须在采样线程里预热，
        # 否则线程里的第一次调用永远是 0。
        psutil.cpu_percent(interval=None)
        try:
            for p in psutil.process_iter(["name"]):
                try:
                    p.cpu_percent(None)
                except Exception:
                    pass
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                snap = self._sample()
            except Exception as exc:
                print("[sysmon] 采样失败:", exc)
                if self._stop.wait(self.interval):
                    break
                continue
            with self._lock:
                self._snap = snap
            if self._stop.wait(self.interval):
                break

    def _sample(self) -> Snapshot:
        s = Snapshot(ts=time.time())
        s.cpu = psutil.cpu_percent(interval=None)
        s.cpu_cores = psutil.cpu_count(logical=True) or 0
        try:
            f = psutil.cpu_freq()
            s.cpu_freq = (f.current or 0) / 1000.0
        except Exception:
            s.cpu_freq = 0.0
        s.cpu_temp = _cpu_temp()

        vm = psutil.virtual_memory()
        s.ram = vm.percent
        s.ram_used_gb = vm.used / 1024 ** 3
        s.ram_total_gb = vm.total / 1024 ** 3
        try:
            s.swap = psutil.swap_memory().percent
        except Exception:
            s.swap = 0.0

        # 磁盘：3 秒缓存一次，避免频繁枚举
        now = time.time()
        if now - self._disk_ts > 3.0 or not self._disk_cache:
            self._disk_cache = _disks()
            self._disk_ts = now
        s.disks = list(self._disk_cache)

        s.gpus = _gpus()

        try:
            io = psutil.net_io_counters()
            if self._last_net:
                dt = now - self._last_net[0]
                if dt > 0:
                    s.net_up = (io.bytes_sent - self._last_net[1]) / dt / 1024
                    s.net_down = (io.bytes_recv - self._last_net[2]) / dt / 1024
            self._last_net = (now, io.bytes_sent, io.bytes_recv)
        except Exception:
            pass

        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                s.battery = bat.percent
                s.charging = bool(bat.power_plugged)
        except Exception:
            pass

        try:
            s.uptime_h = (time.time() - psutil.boot_time()) / 3600
        except Exception:
            s.uptime_h = 0.0

        s.top_cpu, s.top_mem = _top_procs()
        return s


# ---------------- 内部工具 ----------------
def _cpu_temp() -> float | None:
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        return None
    if not temps:
        return None
    for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz", "zenpower"):
        for entry in temps.get(key, []):
            if entry.current:
                return float(entry.current)
    for entries in temps.values():
        for entry in entries:
            if entry.current:
                return float(entry.current)
    return None


def _disks() -> list[DiskInfo]:
    out: list[DiskInfo] = []
    seen: set[str] = set()
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        return out
    for p in parts:
        if p.mountpoint in seen:
            continue
        if p.fstype in ("", "squashfs", "tmpfs"):
            continue
        try:
            usage = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue
        seen.add(p.mountpoint)
        out.append(DiskInfo(
            mount=p.mountpoint.rstrip("\\") or p.mountpoint,
            percent=usage.percent,
            used_gb=usage.used / 1024 ** 3,
            total_gb=usage.total / 1024 ** 3,
        ))
    out.sort(key=lambda d: d.mount)
    return out


def _gpus() -> list[GpuInfo]:
    if not _NVML_OK:
        return []
    out: list[GpuInfo] = []
    try:
        count = pynvml.nvmlDeviceGetCount()
    except Exception:
        return out
    for i in range(count):
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode("utf-8", "ignore")
            util = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
            try:
                temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                temp = 0
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            try:
                power = pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0
            except Exception:
                power = 0.0
            out.append(GpuInfo(
                name=str(name), util=float(util), temp=float(temp),
                mem_used=mem.used / 1024 ** 2, mem_total=mem.total / 1024 ** 2,
                power=power,
            ))
        except Exception:
            continue
    return out


def _top_procs(n: int = 5) -> tuple[list[ProcInfo], list[ProcInfo]]:
    procs = []
    skip = {"System Idle Process", "Idle", "?", "System"}
    for p in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            name = (info.get("name") or "").replace(".exe", "").strip()
            if not name or name in skip:
                continue
            procs.append((name, info.get("cpu_percent") or 0.0,
                          info.get("memory_percent") or 0.0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    top_cpu = [ProcInfo(n_, float(c)) for n_, c, _ in sorted(procs, key=lambda x: -x[1])[:n]]
    top_mem = [ProcInfo(n_, float(m)) for n_, _, m in sorted(procs, key=lambda x: -x[2])[:n]]
    return top_cpu, top_mem


# ---------------- 阈值提醒 ----------------
@dataclass
class Alert:
    kind: str
    value: float


def check_alerts(snap: Snapshot, state: dict) -> list[Alert]:
    """返回本次新触发的告警。state 记录上次是否已经在告警，避免刷屏。"""
    alerts: list[Alert] = []

    def fire(kind: str, cond: bool, value: float) -> None:
        if cond and not state.get(kind):
            state[kind] = True
            alerts.append(Alert(kind, value))
        elif not cond:
            state[kind] = False

    fire("cpu", snap.cpu >= 90, snap.cpu)
    fire("ram", snap.ram >= 92, snap.ram)
    if snap.cpu_temp:
        fire("cputemp", snap.cpu_temp >= 88, snap.cpu_temp)
    for g in snap.gpus:
        fire("gputemp", g.temp >= 82, g.temp)
    if snap.battery is not None:
        fire("battery", snap.battery <= 15 and not snap.charging, snap.battery)
    if snap.disks:
        worst = max(snap.disks, key=lambda d: d.percent)
        fire("disk", worst.percent >= 93, worst.percent)
    return alerts
