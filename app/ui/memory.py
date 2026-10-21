# -*- coding: utf-8 -*-
"""记忆面板：她到底记得什么，以及怎么让她忘掉。

骨架照抄 ``HealthPanel``——同样的 392 宽白圆角卡片、同样的无边框 Tool 窗口、
同样的"按住任意空白处拖走"和 Esc 关闭。这样做不是偷懒：桌宠的每个浮层长得
一样，用户才不用重新学一遍怎么关掉它。

三页：

- **画像**：称呼 / 职业 / 说话风格 + 喜欢·不喜欢的 chip 云
- **回忆**：搜索 + 时间线（情景记忆和事实混排，📌 置顶、× 删掉）
- **习惯**：熬夜次数 / 最长一次 / 近 7 天活跃 + 自绘的 7 根竖条

**这个模块不做任何业务判断**：改记忆就调 store，需要主窗口配合就发信号
（``changed`` / ``organize_requested``）。所以它可以脱离 pet.py 单独跑起来
截图，见 ``tools/preview_memory_ui.py``。
"""
from __future__ import annotations

from datetime import timedelta

from PySide6.QtCore import (QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer,
                            Signal)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QAbstractSlider, QCheckBox, QComboBox, QFrame,
                               QHBoxLayout, QLabel, QLayout, QLineEdit,
                               QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..memory import schema

INK = "#1f2430"
MUTED = "#8a90a0"
LINE = "#eef0f6"
ACCENT = "#4a8cf7"
BAD = "#d8544f"

QSS = f"""
QFrame#card {{
    background: #ffffff;
    border: 1px solid #e6e9f0;
    border-radius: 16px;
}}
QLabel {{ color: {INK}; font-size: 12px; background: transparent; }}
QLabel#h1 {{ font-size: 15px; font-weight: 600; }}
QLabel#dim {{ color: {MUTED}; font-size: 11px; }}
QLabel#sec {{
    color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px;
}}
QLabel#num {{ font-size: 17px; font-weight: 600; }}
QFrame#hr {{ background: {LINE}; max-height: 1px; border: none; }}
QFrame#chip {{ background: #f4f6fb; border: 1px solid #e6e9f0; border-radius: 11px; }}
QFrame#chipBad {{ background: #fdf2f2; border: 1px solid #f6dede; border-radius: 11px; }}
QLabel#chipText {{ font-size: 11px; }}
QPushButton#chipX {{
    background: transparent; border: none; color: #a8adbd;
    font-size: 12px; font-weight: 600; padding: 0px;
}}
QPushButton#chipX:hover {{ color: {BAD}; }}
QPushButton#tab {{
    background: transparent; border: none; border-radius: 9px;
    color: {MUTED}; font-size: 12px; padding: 5px 13px;
}}
QPushButton#tab:hover {{ background: #f4f6fb; color: {INK}; }}
QPushButton#tab:checked {{ background: #eaf1ff; color: {ACCENT}; font-weight: 600; }}
QPushButton#act {{
    background: #f4f6fb; border: 1px solid #e6e9f0; border-radius: 9px;
    color: {INK}; font-size: 12px; padding: 5px 11px;
}}
QPushButton#act:hover {{ background: #eaf1ff; border-color: #cfe0ff; }}
QPushButton#danger {{
    background: #fdf2f2; border: 1px solid #f6dede; border-radius: 9px;
    color: {BAD}; font-size: 12px; padding: 5px 11px;
}}
QPushButton#danger:hover {{ background: #fbe6e6; }}
QPushButton#mini {{
    background: transparent; border: none; color: #b6bbca;
    font-size: 12px; padding: 0px;
}}
QPushButton#mini:hover {{ color: {INK}; }}
QPushButton#close {{
    background: transparent; border: none; border-radius: 11px;
    color: #9aa0b0; font-size: 16px; font-weight: 600; padding: 0px;
}}
QPushButton#close:hover {{ background: #f0f2f8; color: {INK}; }}
QPushButton#close:pressed {{ background: #e6e9f2; }}
QLineEdit {{
    background: #f7f8fc; border: 1px solid #e6e9f0; border-radius: 9px;
    padding: 5px 9px; font-size: 12px; color: {INK};
}}
QLineEdit:focus {{ border-color: #cfe0ff; background: #ffffff; }}
QComboBox {{
    background: #f7f8fc; border: 1px solid #e6e9f0; border-radius: 9px;
    padding: 4px 9px; font-size: 12px; color: {INK};
}}
QComboBox QAbstractItemView {{
    background: #ffffff; border: 1px solid #e6e9f0; outline: none;
    selection-background-color: #eaf1ff; selection-color: {INK};
}}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #d8dce8; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QCheckBox {{ color: {INK}; font-size: 12px; }}
QFrame#row {{ background: transparent; border: none; border-radius: 10px; }}
QFrame#row:hover {{ background: #f7f8fc; }}
QLabel#badge {{
    color: #7d8598; font-size: 10px; background: #f0f2f8;
    border-radius: 6px; padding: 1px 6px;
}}
"""


# --------------------------------------------------------------------------
# 流式布局（Qt 没有内置的，chip 云要用）
# --------------------------------------------------------------------------
class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._sp = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item) -> None:      # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):             # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):             # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):        # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:   # noqa: N802
        return self._layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect: QRect) -> None:    # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self) -> QSize:          # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:       # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        x, y = rect.x() + m.left(), rect.y() + m.top()
        right = rect.right() - m.right()
        line_h = 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > right and line_h > 0:
                x = rect.x() + m.left()
                y += line_h + self._sp
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._sp
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


# --------------------------------------------------------------------------
# chip
# --------------------------------------------------------------------------
class Chip(QFrame):
    removed = Signal(str)

    def __init__(self, text: str, bad: bool = False):
        super().__init__()
        self.text = text
        self.setObjectName("chipBad" if bad else "chip")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 2, 5, 2)
        lay.setSpacing(3)
        lb = QLabel(text)
        lb.setObjectName("chipText")
        lay.addWidget(lb)
        x = QPushButton("×")
        x.setObjectName("chipX")
        x.setFixedSize(13, 13)
        x.setCursor(Qt.CursorShape.PointingHandCursor)
        x.setToolTip("忘掉这个")
        x.clicked.connect(lambda: self.removed.emit(self.text))
        lay.addWidget(x)


class ChipCloud(QWidget):
    """会自动换行的 chip 容器。空的时候显示一句灰字。"""

    removed = Signal(str)

    def __init__(self, empty_text: str = "还没记住"):
        super().__init__()
        self.empty_text = empty_text
        self._lay = FlowLayout(self, spacing=6)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred,
                         QSizePolicy.Policy.Minimum)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def set_items(self, items: list[tuple[str, bool]]) -> None:
        while self._lay.count():
            it = self._lay.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        if not items:
            lb = QLabel(self.empty_text)
            lb.setObjectName("dim")
            self._lay.addWidget(lb)
            return
        for text, bad in items:
            chip = Chip(text, bad)
            chip.removed.connect(self.removed)
            self._lay.addWidget(chip)

    def sizeHint(self) -> QSize:          # noqa: N802
        return QSize(330, self._lay.heightForWidth(330))


# --------------------------------------------------------------------------
# 回忆列表的一行
# --------------------------------------------------------------------------
class MemRow(QFrame):
    removed = Signal(str, str)     # kind, id
    pin = Signal(str, str)

    def __init__(self, kind: str, item: dict, badge: str):
        super().__init__()
        self.kind = kind
        self.item_id = item.get("id", "")
        self.setObjectName("row")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 4, 5)
        lay.setSpacing(1)

        top = QHBoxLayout()
        top.setSpacing(6)
        b = QLabel(badge)
        b.setObjectName("badge")
        top.addWidget(b)
        when = QLabel(_when(item))
        when.setObjectName("dim")
        top.addWidget(when)
        top.addStretch(1)

        pin = QPushButton("★" if item.get("pinned") else "☆")
        pin.setObjectName("mini")
        pin.setFixedSize(16, 16)
        pin.setCursor(Qt.CursorShape.PointingHandCursor)
        pin.setToolTip("取消置顶" if item.get("pinned") else "置顶，永不忘掉")
        pin.clicked.connect(lambda: self.pin.emit(self.kind, self.item_id))
        top.addWidget(pin)

        rm = QPushButton("×")
        rm.setObjectName("mini")
        rm.setFixedSize(16, 16)
        rm.setCursor(Qt.CursorShape.PointingHandCursor)
        rm.setToolTip("忘掉这条")
        rm.clicked.connect(lambda: self.removed.emit(self.kind, self.item_id))
        top.addWidget(rm)
        lay.addLayout(top)

        txt = QLabel(item.get("text", ""))
        txt.setWordWrap(True)
        txt.setStyleSheet("font-size:12px; padding-left:2px;")
        lay.addWidget(txt)
        self.txt = txt


def _when(item: dict) -> str:
    ts = str(item.get("ts") or item.get("created_at") or "")
    return ts[:10] if len(ts) >= 10 else ""


# --------------------------------------------------------------------------
# 近 7 天活跃柱状图
# --------------------------------------------------------------------------
class WeekBars(QWidget):
    def __init__(self):
        super().__init__()
        self.vals: list[int] = [0] * 7
        self.labels: list[str] = ["一"] * 7
        self.setFixedHeight(100)
        self.setMinimumWidth(300)

    def set_data(self, vals: list[int], labels: list[str]) -> None:
        self.vals = (list(vals) + [0] * 7)[:7]
        self.labels = (list(labels) + [""] * 7)[:7]
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        base = h - 18
        top_pad = 19
        peak = max(max(self.vals), 60)
        n = 7
        gap = 6
        bw = max(6.0, (w - gap * (n - 1)) / n)

        p.setFont(QFont("Microsoft YaHei UI", 8))
        for i in range(n):
            x = i * (bw + gap)
            v = self.vals[i]
            bh = 0.0 if v <= 0 else max(3.0, (base - top_pad) * v / peak)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(240, 242, 248))
            p.drawRoundedRect(QRectF(x, top_pad, bw, base - top_pad), 3, 3)
            if bh > 0:
                p.setBrush(QColor(88, 160, 240) if v < 120 else QColor(240, 180, 60))
                p.drawRoundedRect(QRectF(x, base - bh, bw, bh), 3, 3)
                p.setPen(QColor(MUTED))
                # 数值统一摆在顶部一行，不跟着柱子跑，短柱也不会压在灰槽里
                p.drawText(QRectF(x - 4, 0, bw + 8, 12),
                           Qt.AlignmentFlag.AlignCenter,
                           f"{v // 60}h" if v >= 60 else f"{v}m")
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(x, base + 3, bw, 13),
                       Qt.AlignmentFlag.AlignCenter, self.labels[i])


# --------------------------------------------------------------------------
# 面板
# --------------------------------------------------------------------------
class MemoryPanel(QWidget):
    """她记得什么。``changed`` 表示记忆被改过，主窗口负责存盘/刷新。"""

    changed = Signal()
    organize_requested = Signal()

    STYLES = ["不指定", "毒舌但别扎心", "温柔一点", "话少一点", "正经专业", "随便聊聊"]

    def __init__(self, store, cfg: dict | None = None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("她记得什么")
        self.setStyleSheet(QSS)
        self.store = store
        self.cfg = cfg if cfg is not None else {}
        self.setFixedSize(392, 600)

        self._drag_offset = None
        self._forget_armed = False
        self._mem_dirty = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)

        lay.addLayout(self._build_head())
        lay.addWidget(self._hr())
        lay.addLayout(self._build_tabs())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_page_profile())
        self.stack.addWidget(self._build_page_memory())
        self.stack.addWidget(self._build_page_habit())
        lay.addWidget(self.stack, 1)

        lay.addWidget(self._hr())
        lay.addLayout(self._build_foot())

        # 拖动：交互控件不能装过滤器，否则连输入框都点不进去
        no_drag = (QLineEdit, QComboBox, QPushButton, QCheckBox,
                   QScrollArea, QAbstractSlider)
        for w in self.findChildren(QWidget):
            if isinstance(w, no_drag) or w is self:
                continue
            w.installEventFilter(self)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(lambda: self._refresh_memory())

        self.refresh()

    # ---------------- 结构 ----------------
    def _hr(self) -> QFrame:
        f = QFrame()
        f.setObjectName("hr")
        f.setFixedHeight(1)
        return f

    def _track(self, w: QWidget) -> QWidget:
        w.installEventFilter(self)
        return w

    def _build_head(self) -> QHBoxLayout:
        head = QHBoxLayout()
        head.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel("她记得什么")
        t.setObjectName("h1")
        col.addWidget(t)
        self.sub = QLabel("正在读取…")
        self.sub.setObjectName("dim")
        col.addWidget(self.sub)
        col.addStretch(1)
        head.addLayout(col, 1)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("close")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("关闭（Esc）")
        self.close_btn.clicked.connect(self.hide)
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(0)
        side.addWidget(self.close_btn, 0,
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        side.addStretch(1)
        head.addLayout(side)
        return head

    def _build_tabs(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.tabs: list[QPushButton] = []
        for i, name in enumerate(("画像", "回忆", "习惯")):
            b = QPushButton(name)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=i: self._switch(k))
            row.addWidget(b)
            self.tabs.append(b)
        row.addStretch(1)
        self.tabs[0].setChecked(True)
        return row

    def _switch(self, idx: int) -> None:
        for i, b in enumerate(self.tabs):
            b.setChecked(i == idx)
        self.stack.setCurrentIndex(idx)
        if idx == 1:
            self._refresh_memory()

    def _build_page_profile(self) -> QWidget:
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 4, 4, 0)
        lay.setSpacing(7)

        form = QHBoxLayout()
        form.setSpacing(6)
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("称呼")
        self.ed_name.setMaxLength(12)
        self.ed_job = QLineEdit()
        self.ed_job.setPlaceholderText("职业")
        self.ed_job.setMaxLength(20)
        form.addWidget(self.ed_name, 1)
        form.addWidget(self.ed_job, 1)
        lay.addLayout(form)

        style_row = QHBoxLayout()
        style_row.setSpacing(6)
        lb = QLabel("说话风格")
        lb.setObjectName("dim")
        style_row.addWidget(lb)
        self.cb_style = QComboBox()
        self.cb_style.addItems(self.STYLES)
        self.cb_style.setEditable(True)
        self.cb_style.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        style_row.addWidget(self.cb_style, 1)
        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("act")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self._save_profile)
        style_row.addWidget(self.btn_save)
        lay.addLayout(style_row)

        lay.addWidget(self._sec("喜欢"))
        self.cloud_like = ChipCloud("还没记住你喜欢什么")
        self.cloud_like.removed.connect(
            lambda t: self._drop_weighted("likes", t))
        lay.addWidget(self.cloud_like)

        lay.addWidget(self._sec("不喜欢"))
        self.cloud_bad = ChipCloud("还没记住你烦什么")
        self.cloud_bad.removed.connect(
            lambda t: self._drop_weighted("dislikes", t))
        lay.addWidget(self.cloud_bad)

        add = QHBoxLayout()
        add.setSpacing(5)
        self.ed_add = QLineEdit()
        self.ed_add.setPlaceholderText("加一个…")
        self.ed_add.setMaxLength(12)
        self.ed_add.returnPressed.connect(self._add_weighted)
        self.cb_add = QComboBox()
        self.cb_add.addItems(["喜欢", "不喜欢"])
        self.cb_add.setFixedWidth(74)
        btn = QPushButton("＋")
        btn.setObjectName("act")
        btn.setFixedWidth(30)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._add_weighted)
        add.addWidget(self.ed_add, 1)
        add.addWidget(self.cb_add)
        add.addWidget(btn)
        lay.addLayout(add)

        lay.addWidget(self._sec("她可能会说"))
        self.say_preview = QLabel("")
        self.say_preview.setObjectName("dim")
        self.say_preview.setWordWrap(True)
        self.say_preview.setStyleSheet(
            "background:#f7f8fc; border-radius:10px; padding:8px 10px;"
            "font-size:11px; color:#6b7285;")
        lay.addWidget(self.say_preview)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def _build_page_memory(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)

        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("搜一件她可能记得的事…")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(
            lambda: self._search_timer.start(180))
        lay.addWidget(self.ed_search)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self.mem_lay = QVBoxLayout(body)
        self.mem_lay.setContentsMargins(0, 0, 4, 0)
        self.mem_lay.setSpacing(2)
        self.scroll.setWidget(body)
        lay.addWidget(self.scroll, 1)

        self.mem_empty = QLabel("")
        self.mem_empty.setObjectName("dim")
        self.mem_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.mem_empty)
        return page

    def _build_page_habit(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(8)

        nums = QHBoxLayout()
        nums.setSpacing(8)
        self.n_late = self._stat_card(nums, "熬夜", "次")
        self.n_long = self._stat_card(nums, "最长一次", "分钟")
        self.n_week = self._stat_card(nums, "近 7 天", "小时")
        lay.addLayout(nums)

        lay.addWidget(self._sec("每天活跃多久"))
        self.week = WeekBars()
        lay.addWidget(self.week)

        self.ck_watch = QCheckBox("记录使用习惯（只在本地，不上传）")
        self.ck_watch.toggled.connect(self._toggle_watch)
        lay.addWidget(self.ck_watch)

        tip = QLabel("只看得到「你在不在电脑前」和「用了多久」。"
                     "不读窗口标题、不读进程名、不截屏。")
        tip.setObjectName("dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        lay.addStretch(1)
        return page

    def _stat_card(self, row: QHBoxLayout, title: str, unit: str) -> QLabel:
        box = QFrame()
        box.setObjectName("row")
        box.setStyleSheet("QFrame#row { background:#f7f8fc; border-radius:10px; }")
        v = QVBoxLayout(box)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(0)
        val = QLabel("0")
        val.setObjectName("num")
        v.addWidget(val)
        cap = QLabel(f"{title} · {unit}")
        cap.setObjectName("dim")
        v.addWidget(cap)
        row.addWidget(box, 1)
        return val

    def _sec(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("sec")
        return lb

    def _build_foot(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(6)

        teach = QHBoxLayout()
        teach.setSpacing(5)
        self.ed_teach = QLineEdit()
        self.ed_teach.setPlaceholderText("教她一件事，比如「我生日是 3 月 14 号」")
        self.ed_teach.setMaxLength(40)
        self.ed_teach.returnPressed.connect(self._teach)
        btn = QPushButton("记住")
        btn.setObjectName("act")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._teach)
        teach.addWidget(self.ed_teach, 1)
        teach.addWidget(btn)
        box.addLayout(teach)

        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.btn_organize = QPushButton("整理一下")
        self.btn_organize.setObjectName("act")
        self.btn_organize.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_organize.setToolTip("让她把最近的聊天重新捋一遍")
        self.btn_organize.clicked.connect(self._organize)
        acts.addWidget(self.btn_organize)

        self.btn_forget = QPushButton("全部忘掉")
        self.btn_forget.setObjectName("danger")
        self.btn_forget.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_forget.clicked.connect(self._forget_all)
        acts.addWidget(self.btn_forget)
        acts.addStretch(1)

        self.foot_hint = QLabel("Esc 或右上角 × 关闭")
        self.foot_hint.setObjectName("dim")
        acts.addWidget(self.foot_hint)
        box.addLayout(acts)
        return box

    # ---------------- 数据刷新 ----------------
    def refresh(self) -> None:
        if self.store is None:
            self.sub.setText("记忆功能已关闭")
            return
        self._refresh_head()
        self._refresh_profile()
        self._refresh_memory()
        self._refresh_habit()

    def _now(self):
        fn = getattr(self.store, "_now", None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
        from datetime import datetime
        return datetime.now().astimezone()

    def _refresh_head(self) -> None:
        rel = self.store.relationship()
        prof = self.store.profile()
        eps = self.store.episodes()
        n_fact = len(prof.get("facts") or [])
        self.sub.setText(
            f"认识 {rel.get('days_together', 1)} 天 · "
            f"{schema.intimacy_label(int(rel.get('intimacy') or 0))} · "
            f"记着 {len(eps) + n_fact} 条")

    def _refresh_profile(self) -> None:
        prof = self.store.profile()
        name = prof.get("call_me") or ""
        job = prof.get("occupation") or ""
        if not self.ed_name.hasFocus() and self.ed_name.text() != name:
            self.ed_name.setText(name)
        if not self.ed_job.hasFocus() and self.ed_job.text() != job:
            self.ed_job.setText(job)

        style = (prof.get("style") or {}).get("prefer") or ""
        if not self.cb_style.hasFocus() and self.cb_style.currentText() != (
                style or "不指定"):
            self.cb_style.setCurrentText(style or "不指定")

        self.cloud_like.set_items([(x["text"], False)
                                   for x in (prof.get("likes") or [])])
        self.cloud_bad.set_items([(x["text"], True)
                                  for x in (prof.get("dislikes") or [])])
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        """把她现在可能说的话摆出来——这是"记住之后说话不一样"最直观的证据。"""
        picked: list[str] = []
        try:
            from ..memory import voice
            fams: set[str] = set()
            for sit in ("click", "idle", "greet", "anniversary"):
                for ln in voice.candidates(self.store, sit):
                    fam = ln.key.split(":")[0] or sit
                    if fam in fams:
                        continue      # 同一件事不摆两遍
                    fams.add(fam)
                    picked.append(ln.text)
                    break
                if len(picked) >= 2:
                    break
        except Exception:
            picked = []
        self.say_preview.setText(
            "\n".join(f"「{t}」" for t in picked) if picked
            else "还不太了解你，她暂时没什么好说的。")

    def _collect_rows(self) -> list[tuple[str, dict, str]]:
        """情景记忆 + 事实混排，按置顶 / 时间倒序。"""
        st = self.store
        q = self.ed_search.text().strip()
        nq = schema.normalize_text(q)
        rows: list[tuple[str, dict, str]] = []

        def keep(text: str) -> bool:
            if not nq:
                return True
            if nq in schema.normalize_text(text):
                return True
            return schema.jaccard(text, q) >= 0.25

        for e in st.episodes():
            if not keep(e.get("text", "")):
                continue
            rows.append(("episode", e, _source_badge(e)))
        for f in (st.profile().get("facts") or []):
            if not keep(f.get("text", "")):
                continue
            rows.append(("fact", f, "你教的" if f.get("pinned") else "事实"))

        # 两次稳定排序：先按时间倒序，再把置顶的提到最前
        rows.sort(key=lambda r: str(r[1].get("ts") or r[1].get("created_at") or ""),
                  reverse=True)
        rows.sort(key=lambda r: 0 if r[1].get("pinned") else 1)

        # 同一句话可能既是事实又是情景记忆（抽取器两轨都会写），只显示一条
        seen: set[str] = set()
        uniq: list[tuple[str, dict, str]] = []
        for kind, item, badge in rows:
            key = schema.normalize_text(item.get("text", ""))
            if key and key in seen:
                continue
            seen.add(key)
            uniq.append((kind, item, badge))
        return uniq

    def _refresh_memory(self) -> None:
        if self.store is None:
            return
        while self.mem_lay.count():
            it = self.mem_lay.takeAt(0)
            w = it.widget()
            if w:
                w.removeEventFilter(self)
                w.setParent(None)
                w.deleteLater()

        rows = self._collect_rows()
        cap = 60
        for kind, item, badge in rows[:cap]:
            row = MemRow(kind, item, badge)
            row.removed.connect(self._drop_item)
            row.pin.connect(self._toggle_pin)
            self._track(row)
            self.mem_lay.addWidget(row)
        self.mem_lay.addStretch(1)

        if not rows:
            self.mem_empty.setText("还没有回忆。多聊几句她就记住了。"
                                   if not self.ed_search.text().strip()
                                   else "没搜到相关的记忆")
            self.mem_empty.show()
        elif len(rows) > cap:
            self.mem_empty.setText(f"还有 {len(rows) - cap} 条更早的，搜一下试试")
            self.mem_empty.show()
        else:
            self.mem_empty.hide()

    def _refresh_habit(self) -> None:
        st = self.store
        stats = st.data.get("stats") or {}
        ln = stats.get("late_nights") or {}
        ls = stats.get("long_sessions") or {}
        self.n_late.setText(str(int(ln.get("total") or 0)))
        self.n_long.setText(str(int(ls.get("longest_min") or 0)))

        daily = stats.get("daily") or {}
        today = self._now().date()
        days = [today - timedelta(days=i) for i in range(6, -1, -1)]
        vals = [int((daily.get(d.isoformat()) or {}).get("active_min") or 0)
                for d in days]
        self.n_week.setText(f"{sum(vals) / 60:.1f}")
        self.week.set_data(vals, ["一二三四五六日"[d.weekday()] for d in days])

        want = bool(self.cfg.get("memory_watch_activity", True))
        if self.ck_watch.isChecked() != want:
            self.ck_watch.blockSignals(True)
            self.ck_watch.setChecked(want)
            self.ck_watch.blockSignals(False)

    # ---------------- 交互 ----------------
    def _save_profile(self) -> None:
        if self.store is None:
            return
        self.store.set_profile(call_me=self.ed_name.text().strip(),
                               occupation=self.ed_job.text().strip())
        style = self.cb_style.currentText().strip()
        self.store.set_style(prefer="" if style == "不指定" else style)
        self.foot_hint.setText("已保存")
        self.changed.emit()
        self.refresh()

    def _add_weighted(self) -> None:
        text = self.ed_add.text().strip()
        if not text or self.store is None:
            return
        field = "likes" if self.cb_add.currentIndex() == 0 else "dislikes"
        self.store.add_weighted(field, text, 0.8, "manual")
        self.ed_add.clear()
        self.changed.emit()
        self._refresh_profile()

    def _drop_weighted(self, field: str, text: str) -> None:
        if self.store is None:
            return
        self.store.remove_weighted(field, text)
        self.changed.emit()
        self._refresh_profile()

    def _drop_item(self, kind: str, item_id: str) -> None:
        if self.store is None or not item_id:
            return
        if kind == "episode":
            self.store.remove_episode(item_id)
        else:
            self.store.remove_fact(item_id)
        self.changed.emit()
        self._refresh_memory()
        self._refresh_head()

    def _toggle_pin(self, kind: str, item_id: str) -> None:
        if self.store is None or not item_id:
            return
        cur = False
        pool = (self.store.data["episodes"] if kind == "episode"
                else self.store.data["profile"]["facts"])
        for it in pool:
            if it.get("id") == item_id:
                cur = bool(it.get("pinned"))
                break
        self.store.set_pinned(kind, item_id, not cur)
        self.changed.emit()
        self._refresh_memory()

    def _teach(self) -> None:
        text = self.ed_teach.text().strip()
        if not text or self.store is None:
            return
        # 只写 facts，不另外写一条 episode——两条一样的文本会在回忆页重复出现
        self.store.add_fact(text, confidence=1.0, source="manual", pinned=True)
        self.ed_teach.clear()
        self.foot_hint.setText("记住了")
        self.changed.emit()
        self.refresh()

    def _organize(self) -> None:
        self.foot_hint.setText("让她整理一下…")
        self.organize_requested.emit()

    def _forget_all(self) -> None:
        if self.store is None:
            return
        if not self._forget_armed:
            self._forget_armed = True
            self.btn_forget.setText("真的全忘掉？再点一次")
            QTimer.singleShot(4000, self._disarm_forget)
            return
        self._disarm_forget()
        self.store.reset()
        self.changed.emit()
        self.foot_hint.setText("全忘了")
        self.refresh()

    def _disarm_forget(self) -> None:
        self._forget_armed = False
        self.btn_forget.setText("全部忘掉")

    def _toggle_watch(self, on: bool) -> None:
        self.cfg["memory_watch_activity"] = bool(on)
        self.changed.emit()

    # ---------------- 窗口行为（照抄 HealthPanel） ----------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()

    def eventFilter(self, obj, e) -> bool:
        t = e.type()
        if t == QEvent.Type.MouseButtonPress:
            if e.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = e.globalPosition().toPoint() - self.pos()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                e.accept()
            return False
        if t == QEvent.Type.MouseMove:
            if self._drag_offset is not None:
                self.move(e.globalPosition().toPoint() - self._drag_offset)
                e.accept()
            return False
        if t == QEvent.Type.MouseButtonRelease:
            if self._drag_offset is not None:
                self._drag_offset = None
                self.unsetCursor()
                self._keep_on_screen()
                e.accept()
                return True
            return False
        return super().eventFilter(obj, e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._drag_offset is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_offset)
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

    def popup_near(self, anchor: QWidget) -> None:
        geo = anchor.screen().availableGeometry()
        x = anchor.x() + anchor.width() // 2 - self.width() // 2
        y = anchor.y() + anchor.height() // 2 - self.height() // 2
        x = max(geo.left() + 6, min(geo.right() - self.width() - 6, x))
        y = max(geo.top() + 6, min(geo.bottom() - self.height() - 6, y))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)


def _source_badge(item: dict) -> str:
    src = item.get("source") or "chat"
    kind = item.get("kind") or "event"
    if kind == "late_night":
        return "熬夜"
    if kind == "long_session":
        return "久坐"
    if kind == "promise":
        return "约定"
    if kind == "preference":
        return "喜好"
    if kind == "manual":
        return "你教的"
    return {"chat": "聊天", "behavior": "习惯", "manual": "你教的"}.get(src, "聊天")
