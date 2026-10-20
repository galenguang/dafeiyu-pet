# -*- coding: utf-8 -*-
"""对话输入框：贴在鱼身上方，回车发送。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

ACCENT = "#4d6bfe"


class ChatBox(QWidget):
    submitted = Signal(str)
    closed = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(392, 56)
        self.setWindowTitle("和大肥鱼说话")
        self._drag_offset = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #ffffff;
                border: 1px solid #e6e9f0;
                border-radius: 18px;
            }
        """)
        outer.addWidget(card)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 6, 8, 6)
        lay.setSpacing(8)

        self.input = QLineEdit()
        self.input.setPlaceholderText("跟大肥鱼说点什么…")
        self.input.setStyleSheet("""
            QLineEdit {
                color: #1f2430;
                font-size: 14px;
                font-family: "Microsoft YaHei UI", sans-serif;
                border: none;
                background: transparent;
            }
        """)
        self.input.returnPressed.connect(self._submit)
        self.input.textChanged.connect(self._sync_btn)
        lay.addWidget(self.input, 1)

        self.btn = QPushButton("↑")
        self.btn.setFixedSize(32, 32)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self._submit)
        lay.addWidget(self.btn)
        self._sync_btn()

    def _sync_btn(self) -> None:
        on = bool(self.input.text().strip())
        bg = ACCENT if on else "#c7d1f5"
        self.btn.setStyleSheet(f"""
            QPushButton {{
                border-radius: 16px;
                background: {bg};
                border: none;
                color: #ffffff;
                font-size: 18px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: #3d5be0; }}
            QPushButton:pressed {{ background: #3350cc; }}
        """)

    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.hide()
        self.submitted.emit(text)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
            self.closed.emit()
            return
        super().keyPressEvent(e)

    # ---------- 拖动 ----------
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.pos()
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
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.input.setFocus()

    def popup_near(self, anchor: QWidget) -> None:
        geo = anchor.screen().availableGeometry()
        x = anchor.x() + anchor.width() // 2 - self.width() // 2
        y = anchor.y() - self.height() - 6
        if y < geo.top() + 4:
            y = anchor.y() + anchor.height() + 6
        x = max(geo.left() + 6, min(geo.right() - self.width() - 6, x))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.activateWindow()
