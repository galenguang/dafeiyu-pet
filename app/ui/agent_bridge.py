# -*- coding: utf-8 -*-
"""``AgentBridge``：agent 线程 ↔ Qt 主线程的唯一通道。

**为什么用 Signal 而不是队列。** 记忆模块走队列（``drain_events``）是因为它
刻意不 import Qt，好在没有 QApplication 的进程里跑测试。Agent 不一样：它必须
弹确认窗、必须打断执行，这些天然是"事件"而不是"数据"，用 Signal 直接派发到
主线程最省事，也不会因为主循环忙而丢消息。

**这个类的所有方法都从 agent 线程调用**，只有 ``_maybe_progress`` 里的节流
状态需要加锁——Signal 本身跨线程发射是安全的。

**8 秒节流的理由**：一步大概 2–5 秒，不节流的话桌宠会一秒一句话，像个报错
弹窗。节流之后每 8 秒最多一句，而且带步数（"第 3 步，别急"），重复说同一句
也不会显得是卡住了。
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal

from .confirm import ASK_TIMEOUT, DEFAULT_TIMEOUT, AskHub, ConfirmHub

PROGRESS_GAP = 8.0          # 秒

# 状态 → 默认台词。桌宠按这个说话，面板按这个上色。
STATE_LINES = {
    "planning": "让我想想怎么弄",
    "executing": "正在干",
    "confirming": "这一步得你点头",
    "failed": "翻车了，我换个法子",
    "stuck": "我卡住了",
    "done": "搞定",
    "stopped": "停了停了",
}


class AgentBridge(QObject):
    """``runner`` 只认下面这 6 个方法，别的什么都不需要知道。"""

    state_changed = Signal(str, str)     # 状态名, 说明
    said = Signal(str)                   # 让桌宠说一句
    todo_changed = Signal(list)          # list[dict]
    log_added = Signal(str)              # 一行日志
    progress = Signal(str)               # 节流后的进度台词
    finished = Signal(bool, str)         # 成功?, 结论或原因

    def __init__(self, parent=None, stop_event: threading.Event | None = None):
        super().__init__(parent)
        self.stop_event = stop_event or threading.Event()
        self.confirm_hub = ConfirmHub(self, on_log=self._on_hub_log)
        self.ask_hub = AskHub(self, on_log=self._on_hub_log)
        self.last_state = ""
        self._lock = threading.Lock()
        self._steps = 0
        self._last_progress = 0.0

    # ==================================================================
    # runner 侧（agent 线程）
    # ==================================================================
    def state(self, name: str, text: str = "") -> None:
        name = str(name or "")
        text = str(text or "")
        self.last_state = name
        self.state_changed.emit(name, text)
        self._maybe_progress(name)

    def say(self, text: str) -> None:
        t = " ".join(str(text or "").split())[:120]
        if t:
            self.said.emit(t)

    def todo(self, items) -> None:
        try:
            self.todo_changed.emit([dict(x) for x in (items or [])])
        except Exception:
            pass

    def log(self, text: str) -> None:
        self.log_added.emit(str(text or "")[:200])

    def confirm(self, tool: str, risk: str, detail: str,
                timeout: float = DEFAULT_TIMEOUT) -> str:
        return self.confirm_hub.ask(tool, risk, detail, timeout)

    def ask(self, question: str, timeout: float = ASK_TIMEOUT):
        return self.ask_hub.ask(question, timeout)

    # ==================================================================
    # 主线程侧
    # ==================================================================
    def stop(self) -> None:
        """急停：既打断沙箱检查点，也把挂着的弹窗按默认值放掉。"""
        self.stop_event.set()
        self.confirm_hub.close_all()
        self.ask_hub.close_all()

    def reset(self) -> None:
        """开新任务前清一遍。**不清 stop_event**——那是 runner 自己负责的。"""
        self.confirm_hub.reopen()
        self.ask_hub.reopen()
        self.last_state = ""
        with self._lock:
            self._steps = 0
            self._last_progress = 0.0

    def notify_finished(self, ok: bool, text: str) -> None:
        self.finished.emit(bool(ok), str(text or "")[:200])

    # ==================================================================
    def _maybe_progress(self, name: str) -> None:
        """只有"执行"阶段才报进度。想事情的时候报进度是噪音。"""
        if name != "executing":
            return
        with self._lock:
            self._steps += 1
            n = self._steps
            now = time.monotonic()
            if now - self._last_progress < PROGRESS_GAP:
                return
            self._last_progress = now
        self.progress.emit(f"第 {n} 步，别急" if n > 1 else "收到，我先看看")

    def _on_hub_log(self, text: str, tool: str = "") -> None:
        self.log_added.emit(f"{text}{(' · ' + tool) if tool else ''}")
