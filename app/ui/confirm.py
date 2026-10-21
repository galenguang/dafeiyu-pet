# -*- coding: utf-8 -*-
"""确认面板 + 线程桥。

**这是整个 Agent 里唯一一个"人的判断优先于模型"的地方**，所以它的交互设计
不能按"方便"来，得按"不容易点错"来：

- 默认焦点在**拒绝**，回车就是拒绝。想让 Agent 干活，必须动鼠标或按 Tab。
  反过来的话，一个回车就过，人根本来不及看内容。
- 30 秒倒计时自动拒绝。人不在了，就当作拒绝——**超时永远不是同意**。
- Esc = 拒绝。
- DANGER 级不给"本会话都允许"这个按钮。连续点几次允许之后人就不看内容了，
  这是人性，不是疏忽，所以干脆不给这个机会。

**线程模型**：``Runner`` 在 ``agent-runner`` 线程里调 ``ConfirmHub.ask()``，
它发信号到主线程弹窗，然后阻塞等答案。Qt 的 ``AutoConnection`` 会自动把跨线程
信号排进主线程队列，所以这里不用手写 ``invokeMethod``。

**双保险**：弹窗自己 30 秒倒计时，外面再套一层 ``timeout + 5`` 的硬等待。
万一主线程被别的东西堵住了，Agent 线程也不会永远挂着。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from ..agent.gate import ASK_ALWAYS, ASK_NO, ASK_YES
from ..agent.tools import DANGER

DEFAULT_TIMEOUT = 30.0
HARD_GRACE = 5.0            # 外层硬等待比弹窗多留这么多秒

INK = "#1f2430"
MUTED = "#8a90a0"
LINE = "#eef0f6"
WARN = "#c8811a"
DANGER_C = "#d9534f"

# 工具名 → 人话。这一段不是装饰：主人看到的应该是"她要往你电脑上写文件"，
# 而不是 file_write(path=..., content=...)。技术细节放在下面第二行。
TOOL_LABEL = {
    "file_write": "她要往你的电脑上写一个文件",
    "file_read": "她要读你电脑上的一个文件",
    "file_list": "她要翻一下某个文件夹",
    "app_launch": "她要启动一个程序",
    "open_url": "她要用浏览器打开一个网址",
    "clipboard_get": "她要读你剪贴板里的内容",
    "clipboard_set": "她要往你的剪贴板里写东西",
    "windows_list": "她要看一下你现在开了哪些窗口",
    "window_activate": "她要切换到一个别的窗口",
    "screenshot_save": "她要截屏（存成图片，不会上传）",
    "lock_screen": "她要把电脑锁屏",
    "top_processes": "她要看看哪些程序在占 CPU",
    "volume_set": "她要调音量",
    "brightness_set": "她要调屏幕亮度",
    "ask_user": "她想问你一件事",
}

QSS = f"""
QFrame#card {{
    background: #ffffff;
    border: 1px solid #e6e9f0;
    border-radius: 14px;
}}
QLabel {{ color: {INK}; font-size: 12px; background: transparent; }}
QLabel#badge {{
    font-size: 10px; font-weight: 700; padding: 2px 8px;
    border-radius: 8px;
}}
QLabel#what {{ font-size: 13px; font-weight: 600; }}
QLabel#detail {{
    background: #f7f8fb; border: 1px solid {LINE}; border-radius: 8px;
    padding: 8px 10px; color: #3b4252;
    font-family: "Consolas", "Microsoft YaHei UI"; font-size: 11px;
}}
QLabel#why {{ color: {MUTED}; font-size: 11px; }}
QLabel#count {{ color: {MUTED}; font-size: 11px; }}
QLabel#count_hot {{ color: {DANGER_C}; font-size: 11px; font-weight: 600; }}
QLabel#tool {{ color: {MUTED}; font-size: 10px; }}

QPushButton {{
    border: none; border-radius: 8px; padding: 7px 16px;
    font-size: 12px; font-family: "Microsoft YaHei UI";
}}
QPushButton#deny {{
    background: #eef0f6; color: {INK}; font-weight: 600;
}}
QPushButton#deny:hover {{ background: #e3e6ee; }}
QPushButton#deny:focus {{ background: #dde2ec; border: 1px solid #b9c0d0; }}
QPushButton#allow {{
    background: #2f6bff; color: #ffffff; font-weight: 600;
}}
QPushButton#allow:hover {{ background: #2a5fe0; }}
QPushButton#always {{
    background: transparent; color: {MUTED}; font-size: 11px;
    padding: 6px 8px;
}}
QPushButton#always:hover {{ color: #2f6bff; }}
"""


class ConfirmDialog(QDialog):
    """一次确认。``exec_answer()`` 返回 ``yes`` / ``no`` / ``always``。"""

    def __init__(self, tool: str, risk: str, detail: str,
                 timeout: float = DEFAULT_TIMEOUT, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Dialog
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("大肥鱼要动你的电脑")
        self.setStyleSheet(QSS)
        self.setFixedWidth(430)

        self.tool = tool
        self.risk = risk
        self.allow_always = risk != DANGER
        self._answer = ASK_NO
        self._left = max(1, int(round(float(timeout))))
        self._total = self._left

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(9)

        # ---- 标题行：徽章 + 人话 ----
        head = QHBoxLayout()
        head.setSpacing(8)
        badge = QLabel("危险操作" if risk == DANGER else "等你点头")
        badge.setObjectName("badge")
        if risk == DANGER:
            badge.setStyleSheet(f"background:#fdecec; color:{DANGER_C};")
        else:
            badge.setStyleSheet(f"background:#fdf3e3; color:{WARN};")
        head.addWidget(badge)
        head.addStretch(1)
        tl = QLabel(tool)
        tl.setObjectName("tool")
        head.addWidget(tl)
        lay.addLayout(head)

        what = QLabel(TOOL_LABEL.get(tool, f"她要使用「{tool}」"))
        what.setObjectName("what")
        what.setWordWrap(True)
        lay.addWidget(what)

        box = QLabel(self._pretty(detail))
        box.setObjectName("detail")
        box.setWordWrap(True)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.setMaximumHeight(120)
        lay.addWidget(box)

        why = QLabel(self._why_text())
        why.setObjectName("why")
        why.setWordWrap(True)
        lay.addWidget(why)

        # ---- 倒计时 ----
        self.count = QLabel()
        self.count.setObjectName("count")
        lay.addWidget(self.count)
        self._tick_label()

        # ---- 按钮 ----
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_always = QPushButton("本会话都允许")
        self.btn_always.setObjectName("always")
        self.btn_always.setCursor(Qt.CursorShape.PointingHandCursor)
        # QDialog 里的 QPushButton 默认 autoDefault=True，谁先建谁是回车键。
        # 这个按钮是第一个建的，不关掉的话**回车就变成"本会话都允许"**——
        # 一个误按就把整场会话放开了，这是本文件里最不能出的一类错。
        self.btn_always.setAutoDefault(False)
        self.btn_always.clicked.connect(lambda: self._pick(ASK_ALWAYS))
        if self.allow_always:
            row.addWidget(self.btn_always)
        row.addStretch(1)

        self.btn_deny = QPushButton("拒绝")
        self.btn_deny.setObjectName("deny")
        self.btn_deny.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_deny.setDefault(True)          # 回车 = 拒绝
        self.btn_deny.setAutoDefault(True)
        self.btn_deny.clicked.connect(lambda: self._pick(ASK_NO))
        row.addWidget(self.btn_deny)

        self.btn_allow = QPushButton("允许这一次")
        self.btn_allow.setObjectName("allow")
        self.btn_allow.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_allow.setAutoDefault(False)    # Tab 才轮得到它
        self.btn_allow.clicked.connect(lambda: self._pick(ASK_YES))
        row.addWidget(self.btn_allow)
        lay.addLayout(row)

        # Esc 走 reject()，答案默认就是拒绝，不用额外处理
        sc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        sc.activated.connect(self.reject)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------------
    @staticmethod
    def _pretty(detail: str) -> str:
        """路径里的反斜杠太长会撑爆卡片，简单折一下。"""
        t = (detail or "").strip()
        if len(t) > 400:
            t = t[:400] + " …"
        return t or "（没有更多细节）"

    def _why_text(self) -> str:
        if self.risk == DANGER:
            return "这一项风险太高，只能拒绝。"
        if self.tool == "file_write":
            return "允许之后她就会真的改这个文件（会先留一份 .bak 备份）。"
        if self.tool in ("clipboard_get", "clipboard_set"):
            return "剪贴板里可能有你复制过的密码，允许前先看一眼。"
        if self.tool == "app_launch":
            return "允许之后会真的启动这个程序。"
        if self.tool == "lock_screen":
            return "允许之后屏幕会立刻锁上。"
        return "允许之后她会真的在你的电脑上执行这一步。"

    def _tick_label(self) -> None:
        hot = self._left <= 10
        self.count.setObjectName("count_hot" if hot else "count")
        self.count.setStyleSheet(
            f"color:{DANGER_C}; font-weight:600;" if hot else f"color:{MUTED};")
        self.count.setText(f"{self._left} 秒后自动拒绝")

    def _on_tick(self) -> None:
        self._left -= 1
        if self._left <= 0:
            self.timer.stop()
            self._answer = ASK_NO
            self.reject()
            return
        self._tick_label()

    def _pick(self, ans: str) -> None:
        self._answer = ans
        self.timer.stop()
        self.accept()

    # ------------------------------------------------------------------
    def reject(self) -> None:            # noqa: D401 —— Qt 的约定名
        """Esc / 关窗口 / 超时都走这里，答案一律是拒绝。"""
        if not self._answer:
            self._answer = ASK_NO
        self.timer.stop()
        super().reject()

    def exec_answer(self) -> str:
        self._answer = ASK_NO
        self.adjustSize()
        self.timer.start()
        self.exec()
        self.timer.stop()
        return self._answer or ASK_NO

    def showEvent(self, e) -> None:      # noqa: N802 —— Qt 的约定名
        """每次显示都把焦点按到「拒绝」上。

        不能在 ``__init__`` 里 setFocus 就算完：``exec()`` 显示窗口时会把焦点
        交给第一个能接受焦点的控件，构造函数里设的焦点会被冲掉。
        """
        super().showEvent(e)
        self.btn_deny.setFocus(Qt.FocusReason.OtherFocusReason)


# --------------------------------------------------------------------------
# 问主人一句话
# --------------------------------------------------------------------------
ASK_TIMEOUT = 120.0


class AskDialog(QDialog):
    """``ask_user`` 用。这里**不需要**"默认拒绝"那套——

    问问题和给权限是两件事：给权限的默认值必须是拒绝，回答问题没有"默认"，
    空着就是没答。所以回车就是提交，Esc 就是"算了不答"。
    """

    def __init__(self, question: str, timeout: float = ASK_TIMEOUT, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Dialog
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("大肥鱼想问你一句")
        self.setStyleSheet(QSS)
        self.setFixedWidth(430)
        self._answer = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(9)

        head = QHBoxLayout()
        badge = QLabel("想问你")
        badge.setObjectName("badge")
        badge.setStyleSheet("background:#eaf1ff; color:#2f6bff;")
        head.addWidget(badge)
        head.addStretch(1)
        lay.addLayout(head)

        q = QLabel(" ".join((question or "").split())[:300] or "（她没想好问什么）")
        q.setObjectName("what")
        q.setWordWrap(True)
        lay.addWidget(q)

        from PySide6.QtWidgets import QLineEdit
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("说一句，或者直接关掉")
        lay.addWidget(self.edit)

        tip = QLabel("回车发送 · Esc 不回答")
        tip.setObjectName("why")
        lay.addWidget(tip)

        row = QHBoxLayout()
        row.addStretch(1)
        skip = QPushButton("算了")
        skip.setObjectName("deny")
        skip.setAutoDefault(False)
        skip.clicked.connect(self.reject)
        row.addWidget(skip)
        ok = QPushButton("回答")
        ok.setObjectName("allow")
        ok.setAutoDefault(True)
        ok.setDefault(True)
        ok.clicked.connect(self._submit)
        row.addWidget(ok)
        lay.addLayout(row)

        self.edit.returnPressed.connect(self._submit)

    def _submit(self) -> None:
        self._answer = self.edit.text().strip()[:400]
        self.accept()

    def reject(self) -> None:
        self._answer = ""
        super().reject()

    def showEvent(self, e) -> None:      # noqa: N802
        super().showEvent(e)
        self.edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def exec_answer(self) -> str:
        self._answer = ""
        self.adjustSize()
        self.exec()
        return self._answer


# --------------------------------------------------------------------------
# 线程桥
# --------------------------------------------------------------------------
@dataclass
class _Req:
    tool: str
    risk: str
    detail: str
    timeout: float
    answer: str = ASK_NO
    event: threading.Event = field(default_factory=threading.Event)


class _Hub(QObject):
    """把"Agent 线程阻塞等一个弹窗答案"这件事封起来。

    子类只决定两件事：弹哪个窗、拿不到答案时返回什么。
    """

    _requested = Signal(object)
    LOG_LABEL = "确认结果"

    def __init__(self, parent=None, on_log=None):
        super().__init__(parent)
        self.on_log = on_log
        self._lock = threading.Lock()
        self._pending: list[_Req] = []
        self._closed = False
        self._shown = 0
        self._requested.connect(self._show)

    # ---------- 子类实现 ----------
    def _dialog(self, req: _Req):
        raise NotImplementedError

    def _fallback(self) -> str:
        return ASK_NO

    # ---------- 阻塞等待 ----------
    def _wait(self, req: _Req):
        """返回弹窗答案；超时/关闭/主线程调用返回 None。"""
        if self._closed:
            return None
        if threading.current_thread() is threading.main_thread():
            # 主线程直接调会把事件循环堵死（弹窗永远等不到人点），只能放弃
            self._log("主线程调弹窗，直接放弃", req.tool)
            return None

        with self._lock:
            self._pending.append(req)
        try:
            self._requested.emit(req)
        except Exception as exc:
            self._log(f"发弹窗请求失败：{exc}", req.tool)
            with self._lock:
                if req in self._pending:
                    self._pending.remove(req)
            return None

        # 弹窗自己会按 req.timeout 倒计时，这里多留几秒纯属防主线程被堵
        got = req.event.wait(max(0.5, req.timeout + HARD_GRACE))
        with self._lock:
            if req in self._pending:
                self._pending.remove(req)
        if not got:
            self._log("弹窗超时，按默认值处理", req.tool)
            return None
        return req.answer

    # ---------- 主线程 ----------
    def _show(self, req: _Req) -> None:
        if self._closed:
            req.answer = self._fallback()
            req.event.set()
            return
        try:
            dlg = self._dialog(req)
            self._shown += 1
            ans = dlg.exec_answer()
            dlg.deleteLater()
        except Exception as exc:
            self._log(f"弹窗出错：{exc}", req.tool)
            ans = self._fallback()
        req.answer = ans
        req.event.set()
        self._log(f"{self.LOG_LABEL}：{ans}", req.tool)

    def _log(self, text: str, tool: str = "") -> None:
        if self.on_log is not None:
            try:
                self.on_log(text, tool)
            except Exception:
                pass

    # ---------- 生命周期 ----------
    def close_all(self) -> None:
        """急停时用：所有挂着的请求立刻按默认值放行。"""
        self._closed = True
        with self._lock:
            pend = list(self._pending)
            self._pending.clear()
        for req in pend:
            req.answer = self._fallback()
            req.event.set()

    def reopen(self) -> None:
        self._closed = False

    @property
    def shown(self) -> int:
        return self._shown

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)


class ConfirmHub(_Hub):
    """给权限。``ask`` 永远返回 ``yes`` / ``no`` / ``always``，拿不准就是 no。"""

    def _dialog(self, req: _Req):
        return ConfirmDialog(req.tool, req.risk, req.detail, req.timeout)

    def ask(self, tool: str, risk: str, detail: str,
            timeout: float = DEFAULT_TIMEOUT) -> str:
        ans = self._wait(_Req(tool, risk, detail, float(timeout)))
        return ans or ASK_NO


class AskHub(_Hub):
    """问一句话。拿不到答案返回 ``None``——空字符串也算没答。"""

    LOG_LABEL = "回答结果"

    def _dialog(self, req: _Req):
        return AskDialog(req.detail, req.timeout)

    def _fallback(self) -> str:
        return ""

    def ask(self, question: str, timeout: float = ASK_TIMEOUT):
        ans = self._wait(_Req("ask_user", "", question, float(timeout)))
        ans = " ".join((ans or "").split()).strip()[:400]
        return ans or None
