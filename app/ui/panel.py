# -*- coding: utf-8 -*-
"""互动面板：左键点鱼弹出，承载 28 个招牌互动 + 全部 100 个动作。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..actions import ACTIONS, CATEGORIES, FEATURES, by_cat, icon_of

ACCENT = "#4d6bfe"
INK = "#1f2430"
MUTED = "#8a90a0"

QSS = f"""
QFrame#card {{
    background: #ffffff;
    border: 1px solid #e6e9f0;
    border-radius: 16px;
}}
QLabel#title {{
    color: {INK};
    font-size: 15px;
    font-weight: 600;
}}
QLabel#sub {{
    color: {MUTED};
    font-size: 11px;
}}
QPushButton#chip {{
    background: #f4f6fb;
    border: 1px solid #e6e9f0;
    border-radius: 13px;
    color: #5a6072;
    font-size: 12px;
    padding: 4px 12px;
}}
QPushButton#chip:hover {{
    background: #eaeefe;
    color: {ACCENT};
}}
QPushButton#chip:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#act {{
    background: #fafbff;
    border: 1px solid #eef0f6;
    border-radius: 12px;
    color: {INK};
    font-size: 12px;
    padding: 6px 2px;
}}
QPushButton#act:hover {{
    background: #eef2ff;
    border-color: #c9d4ff;
    color: {ACCENT};
}}
QPushButton#act:pressed {{
    background: #dfe6ff;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #d8dce8;
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: #b9c0d4;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QLabel#foot {{
    color: #a7adbd;
    font-size: 11px;
}}
"""

COLS = 4
BTN_W, BTN_H = 93, 56


class ActionPanel(QWidget):
    """点播面板。on_pick(kind, key)：kind = feature | action"""

    def __init__(self, on_pick):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._on_pick = on_pick
        self._cat = "feature"
        self.setStyleSheet(QSS)
        self.setFixedSize(420, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(13, 11, 13, 9)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("大肥鱼 · 互动")
        title.setObjectName("title")
        head.addWidget(title)
        head.addStretch(1)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("sub")
        head.addWidget(self.count_lbl)
        lay.addLayout(head)

        # 分类 chips（两行排布，避免被挤扁）
        chip_grid = QGridLayout()
        chip_grid.setContentsMargins(0, 0, 0, 0)
        chip_grid.setHorizontalSpacing(6)
        chip_grid.setVerticalSpacing(5)
        self._chips: dict[str, QPushButton] = {}
        specs = [("feature", "招牌")] + [(c, CATEGORIES[c][0]) for c in
                                         ("eat", "music", "perform", "fx", "festival", "daily", "idle", "balance")]
        for i, (key, label) in enumerate(specs):
            b = QPushButton(label)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(26)
            b.setMinimumWidth(56)
            b.clicked.connect(lambda _, k=key: self.set_category(k))
            chip_grid.addWidget(b, i // 5, i % 5)
            self._chips[key] = b
        for c in range(5):
            chip_grid.setColumnStretch(c, 1)
        lay.addLayout(chip_grid)

        # 滚动区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.body = QWidget()
        self.body.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 2, 4, 2)
        self.grid.setSpacing(5)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)

        foot = QLabel("单击出动画 · 双击开面板 · 右键完整菜单")
        foot.setObjectName("foot")
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(foot)

        self.set_category("feature")

    # ---------- 内容 ----------
    def set_category(self, key: str) -> None:
        self._cat = key
        for k, b in self._chips.items():
            b.setChecked(k == key)
        self._rebuild()

    def _clear(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)      # 立刻脱离父窗口，否则 grab 时还会画出来
                w.deleteLater()

    def _rebuild(self) -> None:
        self._clear()
        entries: list[tuple[str, str, str]] = []   # (kind, key, label)

        if self._cat == "feature":
            for f in FEATURES:
                entries.append(("feature", f.key, f"{f.icon} {f.label}"))
            self.count_lbl.setText(f"{len(FEATURES)} 个招牌互动")
        else:
            for a in by_cat(self._cat):
                entries.append(("action", a.id, f"{icon_of(a)} {a.name}"))
            self.count_lbl.setText(f"{len(entries)} 个动作")

        for i, (kind, key, label) in enumerate(entries):
            b = QPushButton(label)
            b.setObjectName("act")
            b.setFixedSize(BTN_W, BTN_H)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(QFont("Microsoft YaHei UI", 9))
            b.setToolTip(label.split(" ", 1)[-1])
            b.clicked.connect(lambda _, k=kind, v=key: self._pick(k, v))
            self.grid.addWidget(b, i // COLS, i % COLS)

        rows = (len(entries) + COLS - 1) // COLS
        self.grid.setRowStretch(rows, 1)
        self.scroll.verticalScrollBar().setValue(0)

    def _pick(self, kind: str, key: str) -> None:
        self.hide()
        self._on_pick(kind, key)

    # ---------- 弹出 ----------
    def popup_near(self, anchor: QWidget) -> None:
        screen = anchor.screen()
        geo = screen.availableGeometry()
        x = anchor.x() + anchor.width() // 2 - self.width() // 2
        y = anchor.y() - self.height() - 8
        if y < geo.top() + 4:
            y = anchor.y() + anchor.height() + 8
        x = max(geo.left() + 4, min(geo.right() - self.width() - 4, x))
        y = max(geo.top() + 4, min(geo.bottom() - self.height() - 4, y))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.activateWindow()
