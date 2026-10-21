# -*- coding: utf-8 -*-
"""A2 验收：行为观察。

用法：python tools/test_memory_behavior.py
用假时钟驱动，不真的等 150 分钟。时钟同时注入 store 和 watcher，所以
"跨两天"这种场景也能瞬间模拟出来。

最后一段是**隐私边界静态扫描**：``app/memory/`` 下只要出现读进程名、
读窗口标题、枚举窗口、截屏的调用，直接判失败。
"""
from __future__ import annotations

import ast
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox  # noqa: E402

CFG = {"memory_enabled": True, "memory_watch_activity": True}


class FakeTime:
    """可控时钟：单调秒 + 墙钟 + 空闲毫秒，三者一起走。"""

    def __init__(self, start: datetime, mono: float = 1000.0):
        self.mono = mono
        self.dt = start
        self.idle = 1_000          # 1 秒前有过输入 = 活跃

    def clock(self) -> float:
        return self.mono

    def wall(self) -> datetime:
        return self.dt

    def idle_fn(self) -> int:
        return self.idle

    def advance(self, sec: float) -> None:
        self.mono += sec
        self.dt += timedelta(seconds=sec)


def setup(start: datetime, cfg=None):
    """建一套（store + 假时钟 + watcher），三者共用同一个时钟。"""
    from app.memory.behavior import BehaviorWatcher
    from app.memory.store import MemoryStore

    ft = FakeTime(start)
    st = MemoryStore(now_fn=ft.wall)
    st.reset()
    w = BehaviorWatcher(st, lambda: (cfg or CFG),
                        clock=ft.clock, idle_fn=ft.idle_fn, wall=ft.wall)
    return st, ft, w


def skip_grace(w, ft: FakeTime) -> None:
    """跳过启动宽限期（前 10 分钟不产生任何行为记忆）。"""
    ft.advance(700)
    w.tick()


def run(ft: FakeTime, w, minutes: int) -> list[str]:
    says: list[str] = []
    for _ in range(minutes):
        ft.advance(60)
        says.extend(w.tick())
    return says


def main() -> int:
    c = Checker()

    # ================= 启动宽限 =================
    c.section("启动宽限")
    with DataSandbox():
        st, ft, w = setup(datetime(2026, 10, 21, 10, 0, 0))
        for _ in range(9):
            ft.advance(60)
            w.tick()
        c("启动 10 分钟内不产生 episode", st.data["episodes"] == [], st.data["episodes"])

    # ================= 长时段 =================
    c.section("长时段（连续用 150 分钟）")
    with DataSandbox():
        st, ft, w = setup(datetime(2026, 10, 21, 10, 0, 0))
        skip_grace(w, ft)
        run(ft, w, 160)
        longs = [e for e in st.data["episodes"] if e["kind"] == "long_session"]
        c("150 分钟活跃恰好 1 条 long_session", len(longs) == 1,
          [e["text"] for e in st.data["episodes"]])
        c("stats.long_sessions.total = 1",
          st.data["stats"]["long_sessions"]["total"] == 1,
          st.data["stats"]["long_sessions"])
        c("longest_min >= 120",
          st.data["stats"]["long_sessions"]["longest_min"] >= 120,
          st.data["stats"]["long_sessions"]["longest_min"])

        run(ft, w, 60)
        longs = [e for e in st.data["episodes"] if e["kind"] == "long_session"]
        c("同一会话不重复记 long_session", len(longs) == 1, len(longs))

    # ================= 熬夜 =================
    c.section("熬夜（00:10 → 01:20）")
    with DataSandbox():
        st, ft, w = setup(datetime(2026, 10, 22, 0, 10, 0))
        skip_grace(w, ft)
        says = run(ft, w, 80)
        lates = [e for e in st.data["episodes"] if e["kind"] == "late_night"]
        c("跨夜活跃 80 分钟恰好 1 条 late_night", len(lates) == 1,
          [e["text"] for e in st.data["episodes"]])
        c("late_night 文本含具体点数",
          bool(lates) and "点" in lates[0]["text"], lates and lates[0]["text"])
        c("stats.late_nights.total = 1",
          st.data["stats"]["late_nights"]["total"] == 1,
          st.data["stats"]["late_nights"])
        c("last_30d 也记上了", st.data["stats"]["late_nights"]["last_30d"] == 1,
          st.data["stats"]["late_nights"])
        c("熬夜不主动说话（静音时段）", all("熬" not in s for s in says), says)

    # 连续两天熬夜
    c.section("连续两天熬夜 → consecutive 累加")
    with DataSandbox():
        st, ft, w = setup(datetime(2026, 10, 22, 0, 5, 0))
        skip_grace(w, ft)
        run(ft, w, 45)
        # 跳到第二天同一时间（时钟一起走，store 才知道真的过了一天）
        ft.advance(23 * 3600)
        from app.memory.behavior import BehaviorWatcher
        w2 = BehaviorWatcher(st, lambda: CFG, clock=ft.clock,
                             idle_fn=ft.idle_fn, wall=ft.wall)
        skip_grace(w2, ft)
        run(ft, w2, 45)
        ln = st.data["stats"]["late_nights"]
        c("连续两天熬夜 → consecutive = 2", ln["consecutive"] == 2, ln)
        c("两天共 2 条 late_night",
          len([e for e in st.data["episodes"] if e["kind"] == "late_night"]) == 2,
          [(e["date"], e["text"]) for e in st.data["episodes"]])

    # ================= 断档 =================
    c.section("断档与会话切分")
    with DataSandbox():
        st, ft, w = setup(datetime(2026, 10, 21, 10, 0, 0))
        skip_grace(w, ft)
        run(ft, w, 10)
        c("活跃中会话是开着的", w._session_open is True)

        ft.idle = 1_200_000          # 空闲 20 分钟
        ft.advance(60)
        w.tick()
        c("空闲 15 分钟以上会话关闭", w._session_open is False)
        c("关会话后 active 归零", w._active_sec == 0.0, w._active_sec)

        ft.idle = 1_000
        ft.advance(60)
        w.tick()
        c("重新活跃开新会话", w._session_open is True)
        c("新会话不继承上一条 long_session 标记", w._long_fired is False)

        ft.idle = 300_000            # 短休息 5 分钟
        ft.advance(60)
        w.tick()
        c("短休息（5 分钟）不断会话", w._session_open is True)

    # ================= 久坐 =================
    c.section("久坐提醒")
    with DataSandbox():
        st, ft, w = setup(datetime(2026, 10, 21, 14, 0, 0))
        skip_grace(w, ft)
        says = run(ft, w, 95)
        sit = [s for s in says if "坐了" in s]
        c("90 分钟后冒泡一次久坐提醒", len(sit) == 1, says)
        c("久坐也落了 episode",
          any(e["kind"] == "health" for e in st.data["episodes"]),
          [e["kind"] for e in st.data["episodes"]])

        says2 = run(ft, w, 30)
        c("冷却期内不重复提醒", not any("坐了" in s for s in says2), says2)

    c.section("静音时段只落库不说话")
    with DataSandbox():
        st, ft, w = setup(datetime(2026, 10, 21, 23, 45, 0))
        skip_grace(w, ft)
        says = run(ft, w, 95)
        c("23:45 之后久坐不冒泡", not any("坐了" in s for s in says), says)
        c("但 episode 还是记了",
          any(e["kind"] == "health" for e in st.data["episodes"]),
          [e["kind"] for e in st.data["episodes"]])

    # ================= 开关 =================
    c.section("开关生效")
    with DataSandbox():
        st, ft, w = setup(datetime(2026, 10, 21, 10, 0, 0),
                          cfg={"memory_enabled": True, "memory_watch_activity": False})
        skip_grace(w, ft)
        run(ft, w, 200)
        c("memory_watch_activity=False 时完全不记", st.data["episodes"] == [],
          st.data["episodes"])

    with DataSandbox():
        st, ft, w = setup(datetime(2026, 10, 21, 10, 0, 0),
                          cfg={"memory_enabled": False, "memory_watch_activity": True})
        skip_grace(w, ft)
        run(ft, w, 200)
        c("memory_enabled=False 时完全不记", st.data["episodes"] == [])

    # ================= summary =================
    c.section("统计摘要（面板用）")
    with DataSandbox():
        st, ft, w = setup(datetime(2026, 10, 21, 0, 5, 0))
        skip_grace(w, ft)
        run(ft, w, 45)
        s = w.summary()
        c("summary 有熬夜次数", s["late_nights"] >= 1, s)
        c("summary 有近 7 天列表", isinstance(s["week"], list) and len(s["week"]) >= 1, s["week"])
        c("summary 有今日活跃分钟", "today_min" in s)

    # ================= 隐私边界静态扫描 =================
    c.section("隐私边界静态扫描（最重要）")
    forbidden_attrs = {
        "top_cpu", "top_mem", "GetWindowText", "GetWindowTextW", "GetWindowTextLengthW",
        "EnumWindows", "GetForegroundWindow", "GetWindowRect", "screenshot",
        "BitBlt", "GetDC", "ImageGrab", "grabWindow",
    }
    forbidden_mods = {"psutil", "pynvml", "PIL", "cv2", "mss", "uiautomation", "pywinauto"}

    mem_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "app", "memory")
    files = sorted(f for f in os.listdir(mem_dir) if f.endswith(".py"))
    c(f"扫到 {len(files)} 个记忆模块文件", len(files) >= 5, files)

    hits: list[str] = []
    mod_hits: list[str] = []
    for fn in files:
        path = os.path.join(mem_dir, fn)
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        except SyntaxError as exc:
            hits.append(f"{fn} 语法错误 {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                hits.append(f"{fn}:{node.lineno} .{node.attr}")
            if isinstance(node, ast.Name) and node.id in forbidden_attrs:
                hits.append(f"{fn}:{node.lineno} {node.id}")
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in forbidden_mods:
                        mod_hits.append(f"{fn} import {a.name}")
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in forbidden_mods:
                    mod_hits.append(f"{fn} from {node.module}")

    c("不调用任何读进程名/窗口标题/截屏的 API", not hits, hits)
    c("不 import psutil / PIL / uiautomation 等", not mod_hits, mod_hits)

    beh = open(os.path.join(mem_dir, "behavior.py"), encoding="utf-8").read()
    c("确实用了 GetLastInputInfo（只报空闲时长）", "GetLastInputInfo" in beh)
    c("没有把按键内容带出来",
      "GetKeyboardState" not in beh and "GetAsyncKeyState" not in beh)

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
