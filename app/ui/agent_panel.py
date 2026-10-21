# -*- coding: utf-8 -*-
"""任务面板：她现在在干什么、干到哪了、要不要叫停。

**信息密度刻意做得很低。** 主人不需要看模型的原话、不需要看代码、不需要看
token 账本——那是给开发者的。他只需要三件事：

1. 她在干什么（一句话）
2. 干到哪了（账本）
3. 怎么让她停（一个大按钮）

日志区留着，但放在最下面、默认只露 5 行。它存在的意义是"出事了能翻一眼"，
不是"实时监控"。

**急停按钮放在固定位置、永远是红的、不用滚屏就能点到。** 这一条比美观重要：
真要叫停的时候，人多半已经有点急了。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QTextEdit, QVBoxLayout, QWidget)

from ..agent.todo import CHANGED, DONE, DOING, FAILED, PENDING, SKIPPED

INK = "#1f2430"
MUTED = "#8a90a0"
LINE = "#eef0f6"
BLUE = "#2f6bff"
WARN = "#c8811a"
BAD = "#d9534f"
GOOD = "#2e9e63"

STATE_META = {
    "planning": ("在想", BLUE),
    "executing": ("在干", BLUE),
    "confirming": ("等你点头", WARN),
    "failed": ("翻车了", BAD),
    "stuck": ("卡住了", WARN),
    "done": ("搞定了", GOOD),
    "stopped": ("停了", MUTED),
}

TODO_META = {
    PENDING: ("○", MUTED),
    DOING: ("◐", BLUE),
    DONE: ("●", GOOD),
    CHANGED: ("◆", WARN),
    SKIPPED: ("—", MUTED),
    FAILED: ("✗", BAD),
}

MAX_LOG = 200

QSS = f"""
QFrame#card {{ background:#ffffff; border:1px solid #e6e9f0; border-radius:14px; }}
QLabel {{ color:{INK}; font-size:12px; background:transparent; }}
QLabel#h1 {{ font-size:14px; font-weight:600; }}
QLabel#badge {{
    font-size:10px; font-weight:700; padding:2px 8px; border-radius:8px;
}}
QLabel#task {{ color:{INK}; font-size:12px; }}
QLabel#dim {{ color:{MUTED}; font-size:11px; }}
QFrame#hr {{ background:{LINE}; max-height:1px; border:none; }}
QTextEdit {{
    background:#f7f8fb; border:1px solid {LINE}; border-radius:8px;
    color:#5b6274; font-family:"Consolas","Microsoft YaHei UI"; font-size:10px;
    padding:4px;
}}
QScrollArea {{ background:transparent; border:none; }}
QPushButton {{ border:none; border-radius:8px; padding:8px 16px;
               font-size:12px; font-family:"Microsoft YaHei UI"; }}
QPushButton#stop {{ background:{BAD}; color:#ffffff; font-weight:700; }}
QPushButton#stop:hover {{ background:#c9302c; }}
QPushButton#stop:disabled {{ background:#e8b4b2; }}
QPushButton#close {{
    background:#eef0f6; color:{INK};
    padding:0; font-size:14px; font-weight:600;
}}
QPushButton#close:hover {{ background:#e3e6ee; }}
"""


def _esc(t: str) -> str:
    return (str(t or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


class AgentPanel(QWidget):
    """跟着桌宠走的一块小看板。``stop_requested`` 由主窗口接。"""

    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("她在给你干活")
        self.setStyleSheet(QSS)
        self.setFixedSize(420, 560)

        self._state = ""
        self._stopped = False
        self._drag = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)

        # ---- 头 ----
        head = QHBoxLayout()
        head.setSpacing(8)
        h1 = QLabel("她在给你干活")
        h1.setObjectName("h1")
        head.addWidget(h1)
        self.badge = QLabel("在想")
        self.badge.setObjectName("badge")
        head.addWidget(self.badge)
        head.addStretch(1)
        self.btn_close = QPushButton("×")
        self.btn_close.setObjectName("close")
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.hide)
        head.addWidget(self.btn_close)
        lay.addLayout(head)

        self.task = QLabel("")
        self.task.setObjectName("task")
        self.task.setWordWrap(True)
        self.task.setMaximumHeight(38)
        lay.addWidget(self.task)

        self.note = QLabel("")
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        self.note.setMaximumHeight(32)
        lay.addWidget(self.note)

        lay.addWidget(self._hr())

        # ---- 账本 ----
        tip = QLabel("进度")
        tip.setObjectName("dim")
        lay.addWidget(tip)
        self.todo = QLabel("（她还没列计划）")
        self.todo.setObjectName("dim")
        self.todo.setWordWrap(True)
        self.todo.setTextFormat(Qt.TextFormat.RichText)
        self.todo.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.todo_scroll = QScrollArea()
        self.todo_scroll.setWidgetResizable(True)
        self.todo_scroll.setWidget(self.todo)
        self.todo_scroll.setFixedHeight(150)
        self.todo_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.todo_scroll)

        lay.addWidget(self._hr())

        # ---- 日志 ----
        tip2 = QLabel("记录")
        tip2.setObjectName("dim")
        lay.addWidget(tip2)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        # 日志吃掉剩余高度：账本高度是自适应的，多出来的空间给日志比留白有用
        self.log.setMinimumHeight(96)
        self.log.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        lay.addWidget(self.log, 1)

        # ---- 底 ----
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.stats = QLabel("")
        self.stats.setObjectName("dim")
        foot.addWidget(self.stats)
        foot.addStretch(1)
        self.btn_stop = QPushButton("让她停下")
        self.btn_stop.setObjectName("stop")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.clicked.connect(self._on_stop)
        foot.addWidget(self.btn_stop)
        lay.addLayout(foot)

        self._logs: list[str] = []

    @staticmethod
    def _hr() -> QFrame:
        f = QFrame()
        f.setObjectName("hr")
        f.setFixedHeight(1)
        return f

    # ------------------------------------------------------------------
    def set_task(self, text: str) -> None:
        t = " ".join(str(text or "").split())
        self.task.setText(t[:120] + ("…" if len(t) > 120 else ""))

    def set_state(self, name: str, text: str = "") -> None:
        self._state = str(name or "")
        label, color = STATE_META.get(self._state, ("在忙", BLUE))
        self.badge.setText(label)
        self.badge.setStyleSheet(
            f"background:{color}22; color:{color};")
        if text:
            self.note.setText(" ".join(str(text).split())[:80])
        if self._state in ("done", "stopped", "stuck", "failed"):
            self.btn_stop.setEnabled(False)
            self.btn_stop.setText("已经结束" if self._state == "done" else "已停下")
        elif self._stopped:
            self.btn_stop.setEnabled(False)

    def set_todo(self, items) -> None:
        rows = list(items or [])
        if not rows:
            self.todo.setText("（她还没列计划）")
            self.todo_scroll.setFixedHeight(40)
            return
        out = []
        for it in rows:
            icon, color = TODO_META.get(it.get("status"), ("○", MUTED))
            txt = _esc(it.get("content") or "")
            note = it.get("note") or ""
            extra = f' <span style="color:{MUTED}">（{_esc(note)}）</span>' if note else ""
            strike = ' style="color:%s"' % MUTED if it.get("status") == SKIPPED else ""
            out.append(f'<span style="color:{color}">{icon}</span> '
                       f'<span{strike}>{_esc(it.get("id"))}. {txt}</span>{extra}')
        self.todo.setText("<br>".join(out))
        # 高度跟着条数走：固定 150 时只有两三条会留一大片空白，看着像没加载完
        self.todo_scroll.setFixedHeight(max(40, min(150, 21 * len(rows) + 10)))

    def add_log(self, text: str) -> None:
        t = " ".join(str(text or "").split())
        if not t:
            return
        self._logs.append(t[:160])
        if len(self._logs) > MAX_LOG:
            self._logs = self._logs[-MAX_LOG:]
        self.log.append(t[:160])
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def set_stats(self, steps: int = 0, tokens: int = 0) -> None:
        bits = []
        if steps:
            bits.append(f"第 {steps} 步")
        if tokens:
            bits.append(f"{tokens} token")
        self.stats.setText(" · ".join(bits))

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """开新任务时清一遍。"""
        self._stopped = False
        self._logs.clear()
        self.log.clear()
        self.set_todo([])
        self.task.setText("")
        self.note.setText("")
        self.stats.setText("")
        self.btn_stop.setEnabled(True)
        self.btn_stop.setText("让她停下")
        self.set_state("planning")

    def _on_stop(self) -> None:
        self._stopped = True
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText("正在停…")
        self.add_log("你按了急停")
        self.stop_requested.emit()

    # ------------------------------------------------------------------
    def show_near(self, anchor: QWidget) -> None:
        """贴着桌宠弹出来，超出屏幕就往里挪。"""
        try:
            g = anchor.frameGeometry()
            scr = anchor.screen().availableGeometry()
            x = g.center().x() - self.width() // 2
            y = g.top() - self.height() - 8
            if y < scr.top() + 8:
                y = g.bottom() + 8
            x = max(scr.left() + 8, min(x, scr.right() - self.width() - 8))
            y = max(scr.top() + 8, min(y, scr.bottom() - self.height() - 8))
            self.move(x, y)
        except Exception:
            pass
        self.show()
        self.raise_()
        self.activateWindow()

    # ---- 无边框窗口拖动 ----
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None
