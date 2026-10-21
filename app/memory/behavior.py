# -*- coding: utf-8 -*-
"""行为观察：从"有没有键鼠输入"和系统指标里提炼作息记忆。

**隐私边界写死在结构里**，不是靠自觉：

- 只用 ``GetLastInputInfo`` —— 它只告诉你"距上次键鼠输入多少毫秒"，
  不知道你按了什么。
- 只用 ``Monitor.snapshot`` 里的 cpu / ram / 电池这类**聚合数字**。
- **不 import psutil**，所以拿不到进程名。
- **不调 EnumWindows / GetWindowText**，所以拿不到窗口标题。
- 不截屏。

``tools/test_memory_behavior.py`` 里有一条静态扫描专门守这条线：
``app/memory/`` 下出现 ``top_cpu`` / ``top_mem`` / ``GetWindowText`` /
``EnumWindows`` 就判失败。

采样粒度 60 秒。不存时序，只存聚合量。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from datetime import date, datetime, timedelta

from . import schema
from .store import MemoryStore

# ---- 阈值 ----
ACTIVE_IDLE_MS = 180_000        # 3 分钟内有输入 = 活跃
BREAK_IDLE_MS = 900_000         # 15 分钟没输入 = 会话断了 / 休息了
LONG_SESSION_MIN = 120          # 单会话活跃 120 分钟 = 长时段
LATE_NIGHT_MIN = 30             # 深夜活跃 30 分钟 = 熬夜
SIT_MIN = 90                    # 连续 90 分钟没休息 = 久坐
BOOT_GRACE_SEC = 600            # 启动后 10 分钟内不产生任何行为记忆
SIT_COOLDOWN_SEC = 3600         # 久坐提醒 1 小时最多一次
QUIET_START, QUIET_END = 23 * 60 + 30, 8 * 60   # 静音时段，不主动说话


def _idle_ms_win32() -> int:
    """距上次键鼠输入的毫秒数。拿不到就返回一个很大的数（当作空闲）。"""
    try:
        info = wt.LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return int(ctypes.windll.kernel32.GetTickCount() - info.dwTime)
    except Exception:
        pass
    return 24 * 3600 * 1000


def _minutes_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def is_quiet_hours(dt: datetime) -> bool:
    m = _minutes_of_day(dt)
    return m >= QUIET_START or m < QUIET_END


class BehaviorWatcher:
    """每 60 秒被 ``tick()`` 一次。

    ``tick()`` 返回"值得说出口"的台词列表（可能为空）。落库是副作用，
    不需要调用方关心。
    """

    def __init__(self, store: MemoryStore,
                 get_cfg=None,
                 clock=None, idle_fn=None, wall=None):
        self.store = store
        self._get_cfg = get_cfg or (lambda: {})
        self._clock = clock or _monotonic
        self._idle = idle_fn or _idle_ms_win32
        self._wall = wall or datetime.now

        self._boot_at = self._clock()
        self._last_tick: float | None = None

        # 当前会话
        self._session_open = False
        self._session_start: float | None = None
        self._active_sec = 0.0
        self._long_fired = False

        # 熬夜（按日期累计）
        self._late_date: str | None = None
        self._late_sec = 0.0

        # 久坐
        self._last_break: float | None = None
        self._last_sit_fire = 0.0

    # ================= 对外 =================
    def tick(self) -> list[str]:
        cfg = self._get_cfg()
        if not cfg.get("memory_enabled", True):
            return []
        if not cfg.get("memory_watch_activity", True):
            return []

        now_m = self._clock()
        wall = self._wall()
        idle = int(self._idle())

        # 启动宽限：刚开机不算熬夜、不算久坐
        if now_m - self._boot_at < BOOT_GRACE_SEC:
            self._last_tick = now_m
            return []

        delta = 0.0
        if self._last_tick is not None:
            # 封顶 120 秒：机器睡眠回来时不要一次补上几小时
            delta = max(0.0, min(now_m - self._last_tick, 120.0))
        self._last_tick = now_m

        says: list[str] = []
        active = idle < ACTIVE_IDLE_MS

        if active:
            if not self._session_open:
                self._open_session(now_m)
            self._active_sec += delta
            self._late_accumulate(wall, delta)
            says.extend(self._check_long_session(now_m))
        else:
            if self._session_open and idle >= BREAK_IDLE_MS:
                self._close_session(now_m)

        says.extend(self._check_sitting(now_m, active, idle))
        self._write_daily(wall)
        return [s for s in says if s]

    def summary(self) -> dict:
        """给记忆面板的"习惯"页用。"""
        d = self.store.data
        ln = d["stats"]["late_nights"]
        ls = d["stats"]["long_sessions"]
        daily = d["stats"]["daily"]
        recent = sorted(daily.items(), key=lambda kv: kv[0], reverse=True)[:7]
        return {
            "late_nights": int(ln.get("total", 0)),
            "late_30d": self._late_count_30d(),
            "late_consecutive": int(ln.get("consecutive", 0)),
            "long_sessions": int(ls.get("total", 0)),
            "longest_min": int(ls.get("longest_min", 0)),
            "week": [(k, int(v.get("active_min", 0))) for k, v in reversed(recent)],
            "today_min": int(daily.get(schema.today_str(), {}).get("active_min", 0)),
        }

    # ================= 会话 =================
    def _open_session(self, now_m: float) -> None:
        self._session_open = True
        self._session_start = now_m
        self._active_sec = 0.0
        self._long_fired = False
        if self._last_break is None:
            self._last_break = now_m

    def _close_session(self, now_m: float) -> None:
        self._session_open = False
        self._session_start = None
        self._last_break = now_m
        self._active_sec = 0.0
        self._long_fired = False
        self._late_sec = 0.0

    # ================= 长时段 =================
    def _check_long_session(self, now_m: float) -> list[str]:
        if self._long_fired:
            return []
        mins = self._active_sec / 60.0
        if mins < LONG_SESSION_MIN:
            return []
        self._long_fired = True
        h = int(mins // 60)
        m = int(mins % 60)
        text = f"连续干了 {h} 小时{m} 分" if h else f"连续干了 {m} 分钟"
        self.store.add_episode("long_session", text, tags=["久坐", "工作"],
                               weight=0.55, source="behavior",
                               dedupe_minutes=30)
        ls = self.store.data["stats"]["long_sessions"]
        ls["total"] = int(ls.get("total", 0)) + 1
        ls["last_date"] = schema.today_str()
        ls["longest_min"] = max(int(ls.get("longest_min", 0)), int(mins))
        self.store.mark_dirty()
        # 长时段只落库不冒泡，避免打断主人
        return []

    # ================= 熬夜 =================
    def _late_accumulate(self, wall: datetime, delta: float) -> None:
        if not (0 <= wall.hour < 5):
            return
        ds = schema.today_str(wall.date())
        if self._late_date != ds:
            self._late_date = ds
            self._late_sec = 0.0
        self._late_sec += delta

        if self._late_sec / 60.0 < LATE_NIGHT_MIN:
            return
        # 同一天只记一次
        ln = self.store.data["stats"]["late_nights"]
        if ln.get("last_date") == ds:
            return

        text = f"熬到 {wall.hour} 点还没睡"
        self.store.add_episode("late_night", text, tags=["熬夜"],
                               weight=0.75, source="behavior",
                               dedupe_minutes=30)
        prev = ln.get("last_date")
        consecutive = 1
        if prev:
            try:
                if (date.fromisoformat(ds) - date.fromisoformat(prev)).days == 1:
                    consecutive = int(ln.get("consecutive", 0)) + 1
            except Exception:
                pass
        ln["total"] = int(ln.get("total", 0)) + 1
        ln["last_date"] = ds
        ln["consecutive"] = consecutive
        ln["last_30d"] = self._late_count_30d()
        self.store.mark_dirty()
        self.store.emit("changed")

    def _late_count_30d(self) -> int:
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        n = 0
        for e in self.store.data["episodes"]:
            if e.get("kind") == "late_night" and str(e.get("date", "")) >= cutoff:
                n += 1
        return n

    # ================= 久坐 =================
    def _check_sitting(self, now_m: float, active: bool, idle: int) -> list[str]:
        if not active or self._last_break is None:
            return []
        sitting = now_m - self._last_break
        if sitting < SIT_MIN * 60:
            return []
        if now_m - self._last_sit_fire < SIT_COOLDOWN_SEC:
            return []
        self._last_sit_fire = now_m

        mins = int(sitting // 60)
        self.store.add_episode("health", f"连着坐了 {mins} 分钟没起来",
                               tags=["久坐"], weight=0.5, source="behavior",
                               dedupe_minutes=60)
        self.store.mark_dirty()

        wall = self._wall()
        if is_quiet_hours(wall):
            return []      # 静音时段只落库，不主动说话
        return [f"坐了 {mins} 分钟了，起来动动"]

    # ================= 日统计 =================
    def _write_daily(self, wall: datetime) -> None:
        ds = schema.today_str(wall.date())
        daily = self.store.data["stats"]["daily"]
        day = daily.setdefault(ds, {"active_min": 0, "sessions": 0,
                                    "late_night": False, "chats": 0})
        if self._session_open:
            # 每 tick 增量累加，用 active_sec 反推不靠谱，直接按 delta 记
            pass
        # 用当前会话时长做保守估计，避免重复累加
        if self._session_start is not None:
            mins = int(self._active_sec / 60.0)
            if mins > int(day.get("active_min", 0)):
                day["active_min"] = mins
        if self._late_date == ds and self._late_sec >= LATE_NIGHT_MIN * 60:
            day["late_night"] = True
        # 裁剪
        if len(daily) > schema.LIMITS["daily_days"]:
            keep = sorted(daily.keys(), reverse=True)[:schema.LIMITS["daily_days"]]
            for k in list(daily.keys()):
                if k not in keep:
                    daily.pop(k, None)


def _monotonic() -> float:
    import time
    return time.monotonic()


# --------------------------------------------------------------------------
# 给主窗口用的采样线程（60 秒一次，跑在 daemon 线程）
# --------------------------------------------------------------------------
class BehaviorThread:
    def __init__(self, watcher: BehaviorWatcher, interval: float = 60.0):
        import threading
        self._watcher = watcher
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import threading
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="memory-behavior")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                says = self._watcher.tick()
                for s in says:
                    self._watcher.store.emit("say", s)
            except Exception as exc:
                print("[memory] 行为采样失败:", exc)
            if self._stop.wait(self._interval):
                break

    def stop(self) -> None:
        self._stop.set()
