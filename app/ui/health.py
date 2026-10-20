# -*- coding: utf-8 -*-
"""系统健康面板：CPU / 内存 / 显卡 / 磁盘 / 网络 / 电池 / 进程。"""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..sysmon import Monitor, Snapshot

INK = "#1f2430"
MUTED = "#8a90a0"
LINE = "#eef0f6"

QSS = f"""
QFrame#card {{
    background: #ffffff;
    border: 1px solid #e6e9f0;
    border-radius: 16px;
}}
QLabel {{
    color: {INK};
    font-size: 12px;
}}
QLabel#h1 {{ font-size: 15px; font-weight: 600; }}
QLabel#dim {{ color: {MUTED}; font-size: 11px; }}
QLabel#sec {{
    color: {MUTED};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QFrame#hr {{ background: {LINE}; max-height: 1px; border: none; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #d8dce8; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QPushButton#close {{
    background: transparent;
    border: none;
    border-radius: 11px;
    color: #9aa0b0;
    font-size: 16px;
    font-weight: 600;
    padding: 0px;
}}
QPushButton#close:hover {{ background: #f0f2f8; color: #1f2430; }}
QPushButton#close:pressed {{ background: #e6e9f2; }}
"""


def _bar_color(pct: float, invert: bool = False) -> QColor:
    """invert=True 用于电池这类「越低越危险」的指标。"""
    if invert:
        if pct <= 15:
            return QColor(233, 84, 84)
        if pct <= 30:
            return QColor(240, 160, 60)
        return QColor(88, 190, 130)
    if pct >= 90:
        return QColor(233, 84, 84)
    if pct >= 75:
        return QColor(240, 160, 60)
    if pct >= 55:
        return QColor(240, 200, 70)
    return QColor(88, 160, 240)


class Bar(QWidget):
    """一行指标：名称 + 数值 / 进度条 / 补充说明。"""

    def __init__(self, label: str, invert: bool = False):
        super().__init__()
        self.label = label
        self.value_text = "--"
        self.pct = 0.0
        self.detail = ""
        self.invert = invert          # True 时数值越低越危险（电池）
        self.setFixedHeight(40)
        self.setMinimumWidth(280)

    def set_data(self, pct: float, value_text: str, detail: str = "") -> None:
        self.pct = max(0.0, min(100.0, pct))
        self.value_text = value_text
        self.detail = detail
        self.update()

    def set_label(self, text: str) -> None:
        self.label = text

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()

        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.setPen(QColor(INK))
        p.drawText(QRectF(0, 0, w * 0.55, 17),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.label)
        p.setPen(QColor(INK))
        p.drawText(QRectF(w * 0.45, 0, w * 0.55, 17),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.value_text)

        # 进度条
        track = QRectF(0, 20, w, 5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(240, 242, 248))
        p.drawRoundedRect(track, 2.5, 2.5)
        if self.pct > 0:
            fill = QRectF(0, 20, max(4.0, w * self.pct / 100.0), 5)
            p.setBrush(_bar_color(self.pct, self.invert))
            p.drawRoundedRect(fill, 2.5, 2.5)

        # 补充说明
        if self.detail:
            p.setPen(QColor(MUTED))
            p.setFont(QFont("Microsoft YaHei UI", 8))
            p.drawText(QRectF(0, 26, w, 13),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.detail)


class ScoreRing(QWidget):
    def __init__(self):
        super().__init__()
        self.score = 100
        self.verdict = ""
        self.setFixedSize(86, 86)

    def set_data(self, score: int, verdict: str) -> None:
        self.score = score
        self.verdict = verdict
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(6, 6, self.width() - 12, self.height() - 12)
        pen = QPen(QColor(238, 240, 246), 7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)
        color = QColor(88, 190, 130) if self.score >= 72 else (
            QColor(240, 180, 60) if self.score >= 45 else QColor(230, 90, 90))
        pen = QPen(color, 7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        span = int(-self.score / 100.0 * 360 * 16)
        p.drawArc(rect, 90 * 16, span)
        p.setPen(QColor(INK))
        f = QFont("Microsoft YaHei UI", 16)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.score))


class HealthPanel(QWidget):
    def __init__(self, monitor: Monitor):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("大肥鱼体检")
        self.setStyleSheet(QSS)
        self.monitor = monitor
        self.setFixedSize(392, 560)
        # 拖动状态：按住面板任意空白处就能拖走
        self._drag_offset = None
        self._press_global = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        # 顶部：评分 + 标题
        head = QHBoxLayout()
        head.setSpacing(12)
        self.ring = ScoreRing()
        head.addWidget(self.ring)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel("体检报告")
        t.setObjectName("h1")
        col.addWidget(t)
        self.verdict = QLabel("正在采样…")
        self.verdict.setObjectName("dim")
        col.addWidget(self.verdict)
        self.uptime = QLabel("")
        self.uptime.setObjectName("dim")
        col.addWidget(self.uptime)
        col.addStretch(1)
        head.addLayout(col, 1)

        # 右上角关闭按钮
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("close")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("关闭体检报告（Esc）")
        self.close_btn.clicked.connect(self.hide)
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(0)
        side.addWidget(self.close_btn, 0,
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        side.addStretch(1)
        head.addLayout(side)

        lay.addLayout(head)

        lay.addWidget(self._hr())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(0, 0, 4, 0)
        self.body_lay.setSpacing(4)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        foot = QLabel("按住面板可拖动 · Esc 或右上角 × 关闭")
        foot.setObjectName("dim")
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(foot)

        # 固定指标
        self.b_cpu = Bar("CPU")
        self.b_ram = Bar("内存")
        self.b_net = Bar("网络")
        self.b_bat = Bar("电池", invert=True)
        self.body_lay.addWidget(self.b_cpu)
        self.body_lay.addWidget(self.b_ram)

        self.gpu_title = QLabel("显卡")
        self.gpu_title.setObjectName("sec")
        self.body_lay.addWidget(self.gpu_title)
        self.gpu_box = QVBoxLayout()
        self.gpu_box.setSpacing(2)
        self.body_lay.addLayout(self.gpu_box)
        self.gpu_bars: list[Bar] = []

        disk_title = QLabel("磁盘")
        disk_title.setObjectName("sec")
        self.body_lay.addWidget(disk_title)
        self.disk_box = QVBoxLayout()
        self.disk_box.setSpacing(2)
        self.body_lay.addLayout(self.disk_box)
        self.disk_bars: list[Bar] = []

        self.body_lay.addWidget(self.b_net)
        self.body_lay.addWidget(self.b_bat)

        proc_title = QLabel("占用最高")
        proc_title.setObjectName("sec")
        self.body_lay.addWidget(proc_title)
        self.proc_grid = QGridLayout()
        self.proc_grid.setHorizontalSpacing(8)
        self.proc_grid.setVerticalSpacing(2)
        self.body_lay.addLayout(self.proc_grid)
        self.body_lay.addStretch(1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

        # 让整块面板都能拖：子控件（标签、指标条、卡片）会截走鼠标事件，
        # 所以在它们身上装事件过滤器，统一交给面板处理。
        for w in self.findChildren(QWidget):
            if w is self or w is self.close_btn:
                continue
            w.installEventFilter(self)

    # ---------- 刷新 ----------
    def _hr(self) -> QFrame:
        f = QFrame()
        f.setObjectName("hr")
        f.setFixedHeight(1)
        return f

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()
        self.timer.start(1000)

    def hideEvent(self, e) -> None:
        self.timer.stop()
        super().hideEvent(e)

    # ---------- 拖动 / 关闭 ----------
    def eventFilter(self, obj, e) -> bool:
        """在子控件上按下鼠标时，把整个拖动过程接管过来。

        注意：这里必须 return False。如果吞掉（return True）了 MouseButtonPress，
        Qt 就不会把这个子控件设成鼠标 grabber，后续的 MouseMove 压根不会派发过来，
        表现就是「拖一下就断」。正确做法是接受事件但放行，让 grab 正常建立，
        然后在子控件的 press/move/release 里统一走面板的拖动逻辑。
        """
        t = e.type()
        if t == QEvent.Type.MouseButtonPress:
            if e.button() == Qt.MouseButton.LeftButton:
                self._begin_drag(e)
                e.accept()
            return False
        if t == QEvent.Type.MouseMove:
            if self._drag_offset is not None:
                self._move_drag(e)
                e.accept()
            return False
        if t == QEvent.Type.MouseButtonRelease:
            if self._drag_offset is not None:
                self._end_drag()
                e.accept()
                return True      # 松开后不再传给子控件，避免误触发点击
            return False
        return super().eventFilter(obj, e)

    # ---------- 拖动实现（面板自己和子控件共用） ----------
    def _begin_drag(self, e) -> None:
        self._drag_offset = e.globalPosition().toPoint() - self.pos()
        self._press_global = e.globalPosition().toPoint()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _move_drag(self, e) -> None:
        self.move(e.globalPosition().toPoint() - self._drag_offset)

    def _end_drag(self) -> None:
        self._drag_offset = None
        self._press_global = None
        self.unsetCursor()
        self._keep_on_screen()

    def mousePressEvent(self, e) -> None:
        # 面板本身是 Tool 窗口，没有标题栏，所以自己实现拖动。
        if e.button() == Qt.MouseButton.LeftButton:
            self._begin_drag(e)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._drag_offset is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self._move_drag(e)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.unsetCursor()
            self._keep_on_screen()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def _keep_on_screen(self) -> None:
        scr = self.screen()
        if scr is None:
            return
        geo = scr.availableGeometry()
        x = max(geo.left() + 4, min(geo.right() - self.width() - 4, self.x()))
        y = max(geo.top() + 4, min(geo.bottom() - self.height() - 4, self.y()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
            e.accept()
            return
        super().keyPressEvent(e)

    def refresh(self) -> None:
        s = self.monitor.snapshot
        if s.ts == 0:
            return
        self.ring.set_data(s.score(), s.verdict())
        self.verdict.setText(f"{s.verdict()} · 健康分 {s.score()}")
        h = int(s.uptime_h)
        self.uptime.setText(f"已开机 {h // 24} 天 {h % 24} 小时")

        temp = f" · {s.cpu_temp:.0f}°C" if s.cpu_temp else ""
        self.b_cpu.set_data(s.cpu, f"{s.cpu:.0f}%{temp}",
                            f"{s.cpu_freq:.2f}GHz × {s.cpu_cores}" if s.cpu_freq else f"{s.cpu_cores} 线程")
        self.b_ram.set_data(s.ram, f"{s.ram:.0f}%",
                            f"{s.ram_used_gb:.1f} / {s.ram_total_gb:.0f} GB")

        # 显卡
        if not s.gpus:
            self.gpu_title.setVisible(False)
            for b in self.gpu_bars:
                b.setVisible(False)
        else:
            self.gpu_title.setVisible(True)
            while len(self.gpu_bars) < len(s.gpus):
                b = Bar("GPU")
                self.gpu_box.addWidget(b)
                self.gpu_bars.append(b)
            for i, b in enumerate(self.gpu_bars):
                if i < len(s.gpus):
                    g = s.gpus[i]
                    b.setVisible(True)
                    b.set_data(g.util, f"{g.util:.0f}% · {g.temp:.0f}°C",
                               f"显存 {g.mem_used / 1024:.1f}/{g.mem_total / 1024:.0f}G")
                else:
                    b.setVisible(False)

        # 磁盘
        while len(self.disk_bars) < len(s.disks):
            b = Bar("磁盘")
            self.disk_box.addWidget(b)
            self.disk_bars.append(b)
        for i, b in enumerate(self.disk_bars):
            if i < len(s.disks):
                d = s.disks[i]
                b.setVisible(True)
                b.set_label(d.mount or "磁盘")
                b.set_data(d.percent, f"{d.percent:.0f}%",
                           f"{d.used_gb:.0f} / {d.total_gb:.0f} GB")
            else:
                b.setVisible(False)

        # 网络（用 0-100 的比例显示，10MB/s 封顶）
        net = s.net_down
        self.b_net.set_data(min(100, net / 10240 * 100), f"↓{_fmt_speed(net)}",
                            f"↑{_fmt_speed(s.net_up)}")

        # 电池
        if s.battery is None:
            self.b_bat.setVisible(False)
        else:
            self.b_bat.setVisible(True)
            self.b_bat.set_data(s.battery, f"{s.battery:.0f}%",
                                "充电中" if s.charging else "使用电池")

        self._refresh_procs(s)

    def _track(self, w: QWidget) -> QWidget:
        """新造出来的子控件也要能拖动。"""
        w.installEventFilter(self)
        return w

    def _refresh_procs(self, s: Snapshot) -> None:
        while self.proc_grid.count():
            item = self.proc_grid.takeAt(0)
            w = item.widget()
            if w:
                w.removeEventFilter(self)
                w.setParent(None)
                w.deleteLater()
        heads = [("进程", 0), ("CPU", 1), ("内存", 2)]
        f = QFont("Microsoft YaHei UI", 8)
        for text, col in heads:
            lb = self._track(QLabel(text))
            lb.setObjectName("dim")
            lb.setFont(f)
            lb.setAlignment(Qt.AlignmentFlag.AlignRight if col else Qt.AlignmentFlag.AlignLeft)
            self.proc_grid.addWidget(lb, 0, col)
        for row, (cpu_p, mem_p) in enumerate(zip(s.top_cpu, s.top_mem), start=1):
            name = self._track(QLabel((cpu_p.name or "?")[:14]))
            name.setFont(f)
            cv = self._track(QLabel(f"{cpu_p.value:.0f}%"))
            cv.setFont(f)
            cv.setObjectName("dim")
            cv.setAlignment(Qt.AlignmentFlag.AlignRight)
            mv = self._track(QLabel(f"{mem_p.value:.1f}%"))
            mv.setFont(f)
            mv.setObjectName("dim")
            mv.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.proc_grid.addWidget(name, row, 0)
            self.proc_grid.addWidget(cv, row, 1)
            self.proc_grid.addWidget(mv, row, 2)

    def popup_near(self, anchor: QWidget) -> None:
        from PySide6.QtCore import QPoint
        geo = anchor.screen().availableGeometry()
        x = anchor.x() + anchor.width() // 2 - self.width() // 2
        y = anchor.y() + anchor.height() // 2 - self.height() // 2
        x = max(geo.left() + 6, min(geo.right() - self.width() - 6, x))
        y = max(geo.top() + 6, min(geo.bottom() - self.height() - 6, y))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)   # 让 Esc 能收到


def _fmt_speed(kbps: float) -> str:
    if kbps >= 1024:
        return f"{kbps / 1024:.1f}MB/s"
    return f"{kbps:.0f}KB/s"
