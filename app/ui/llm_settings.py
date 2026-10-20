# -*- coding: utf-8 -*-
"""模型设置面板：Base URL / API Key / 模型名，三样填完就能用。

不绑定厂商 —— DeepSeek、OpenAI、通义、Kimi、智谱、硅基流动、Ollama、
LM Studio、vLLM、各种 one-api 中转站，只要兼容 OpenAI 接口格式就行。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout, QWidget)

from .. import config, llm

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
    background: transparent;
}}
QLabel#h1 {{ font-size: 15px; font-weight: 600; }}
QLabel#dim {{ color: {MUTED}; font-size: 11px; }}
QLabel#field {{ color: #5b6274; font-size: 11px; font-weight: 600; }}
QLabel#hint {{ color: {MUTED}; font-size: 11px; }}
QLabel#status_ok {{ color: #2e9e63; font-size: 11px; }}
QLabel#status_err {{ color: #d9534f; font-size: 11px; }}

QFrame#hr {{ background: {LINE}; max-height: 1px; border: none; }}

QLineEdit {{
    background: #f5f7fb;
    border: 1px solid #e6e9f0;
    border-radius: 8px;
    padding: 7px 10px;
    color: {INK};
    font-size: 12px;
    selection-background-color: #cfe0ff;
}}
QLineEdit:focus {{ border: 1px solid #8fb4ff; background: #ffffff; }}

QComboBox {{
    background: #f5f7fb;
    border: 1px solid #e6e9f0;
    border-radius: 8px;
    padding: 6px 10px;
    color: {INK};
    font-size: 12px;
    min-height: 16px;
}}
QComboBox:focus {{ border: 1px solid #8fb4ff; background: #ffffff; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: #ffffff;
    border: 1px solid #e6e9f0;
    border-radius: 8px;
    padding: 4px;
    outline: none;
    color: {INK};
    selection-background-color: #eaf1ff;
    selection-color: {INK};
}}

QPushButton#primary {{
    background: #2f6bff;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: #4a7fff; }}
QPushButton#primary:pressed {{ background: #2559d8; }}

QPushButton#ghost {{
    background: #f2f4f9;
    color: #5b6274;
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    font-size: 12px;
}}
QPushButton#ghost:hover {{ background: #e9edf6; }}
QPushButton#ghost:pressed {{ background: #dfe4f0; }}
QPushButton#ghost:disabled {{ color: #b9bfcd; }}

QPushButton#close {{
    background: transparent;
    border: none;
    border-radius: 11px;
    color: #9aa0b0;
    font-size: 16px;
    font-weight: 600;
    padding: 0px;
}}
QPushButton#close:hover {{ background: #f0f2f8; color: {INK}; }}
QPushButton#close:pressed {{ background: #e6e9f2; }}

QPushButton#eye {{
    background: transparent;
    border: none;
    color: #9aa0b0;
    font-size: 13px;
    padding: 0px;
}}
QPushButton#eye:hover {{ color: {INK}; }}
"""


class _DragBar(QWidget):
    """标题栏：按住这里拖整个面板。"""

    def __init__(self, target: QWidget):
        super().__init__(target)
        self.target = target
        self._offset = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._offset = e.globalPosition().toPoint() - self.target.pos()

    def mouseMoveEvent(self, e) -> None:
        if self._offset is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self.target.move(e.globalPosition().toPoint() - self._offset)

    def mouseReleaseEvent(self, e) -> None:
        self._offset = None


class LLMSettings(QWidget):
    """模型设置面板。保存后 emit saved(cfg)，让主窗口去冒泡提示。"""

    saved = Signal(dict)
    _pinged = Signal(bool, str)      # 后台线程 -> 主线程

    def __init__(self, cfg: dict):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("大肥鱼模型设置")
        self.setStyleSheet(QSS)
        self.cfg = cfg
        self.setFixedWidth(452)
        self._pinged.connect(self._on_pinged)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(9)

        # ---------- 标题栏 ----------
        bar = _DragBar(self)
        head = QHBoxLayout(bar)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel("模型设置")
        t.setObjectName("h1")
        col.addWidget(t)
        sub = QLabel("兼容 OpenAI 接口格式，任意厂商或本地模型都能接")
        sub.setObjectName("dim")
        col.addWidget(sub)
        head.addLayout(col, 1)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("close")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("关闭（Esc）")
        self.close_btn.clicked.connect(self.hide)
        head.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(bar)

        lay.addWidget(self._hr())

        # ---------- 服务商预设 ----------
        lay.addWidget(self._field("服务商"))
        self.cb = QComboBox()
        self.cb.addItems([p["name"] for p in llm.PRESETS])
        self.cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cb.currentIndexChanged.connect(self._on_preset)
        lay.addWidget(self.cb)

        # ---------- Base URL ----------
        lay.addWidget(self._field("Base URL"))
        self.ed_base = QLineEdit()
        self.ed_base.setPlaceholderText("https://api.deepseek.com/v1")
        lay.addWidget(self.ed_base)

        # ---------- API Key ----------
        lay.addWidget(self._field("API Key"))
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        self.ed_key = QLineEdit()
        self.ed_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_key.setPlaceholderText("本地模型可以留空")
        key_row.addWidget(self.ed_key, 1)
        self.btn_eye = QPushButton("👁")
        self.btn_eye.setObjectName("eye")
        self.btn_eye.setFixedSize(26, 30)
        self.btn_eye.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_eye.setToolTip("显示 / 隐藏")
        self.btn_eye.clicked.connect(self._toggle_key)
        key_row.addWidget(self.btn_eye)
        lay.addLayout(key_row)

        # ---------- 模型名 ----------
        lay.addWidget(self._field("模型名"))
        self.ed_model = QLineEdit()
        self.ed_model.setPlaceholderText("deepseek-chat / gpt-4o-mini / qwen2.5:7b …")
        lay.addWidget(self.ed_model)

        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        lay.addWidget(self._hr())

        # ---------- 测试连接 ----------
        test_row = QHBoxLayout()
        test_row.setSpacing(8)
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setObjectName("ghost")
        self.btn_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_test.clicked.connect(self._test)
        test_row.addWidget(self.btn_test)
        self.status = QLabel("")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        test_row.addWidget(self.status, 1)
        lay.addLayout(test_row)

        lay.addStretch(1)

        # ---------- 底部按钮 ----------
        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("ghost")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.hide)
        btns.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("primary")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self._save)
        btns.addWidget(self.btn_save)
        lay.addLayout(btns)

        foot = QLabel("按住标题可拖动 · Esc 关闭 · 只有 DeepSeek 支持查余额")
        foot.setObjectName("dim")
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(foot)

        self._load_from_cfg()

    # ---------- 小工具 ----------
    @staticmethod
    def _hr() -> QFrame:
        f = QFrame()
        f.setObjectName("hr")
        f.setFixedHeight(1)
        return f

    @staticmethod
    def _field(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("field")
        return lab

    def _toggle_key(self) -> None:
        showing = self.ed_key.echoMode() == QLineEdit.EchoMode.Normal
        self.ed_key.setEchoMode(
            QLineEdit.EchoMode.Password if showing else QLineEdit.EchoMode.Normal)
        self.btn_eye.setText("🙈" if showing else "👁")

    # ---------- 读写配置 ----------
    def _load_from_cfg(self) -> None:
        name = self.cfg.get("llm_preset") or "DeepSeek"
        names = [p["name"] for p in llm.PRESETS]
        self.cb.blockSignals(True)
        self.cb.setCurrentIndex(names.index(name) if name in names else len(names) - 1)
        self.cb.blockSignals(False)
        self.ed_base.setText(self.cfg.get("llm_base_url", ""))
        self.ed_key.setText(self.cfg.get("llm_api_key", ""))
        self.ed_model.setText(self.cfg.get("llm_model", ""))
        self.hint.setText(llm.PRESET_BY_NAME.get(name, llm.PRESETS[-1])["hint"])

    def _on_preset(self, idx: int) -> None:
        if not (0 <= idx < len(llm.PRESETS)):
            return
        p = llm.PRESETS[idx]
        self.hint.setText(p["hint"])
        if p["name"] == "自定义":
            return                      # 自定义不动用户已经填的内容
        self.ed_base.setText(p["base"])
        self.ed_model.setText(p["model"])
        self.status.setText("")

    def _current_llm(self) -> dict:
        return {
            "base_url": llm.normalize_base(self.ed_base.text()),
            "api_key": self.ed_key.text().strip(),
            "model": self.ed_model.text().strip(),
            "timeout": int(self.cfg.get("llm_timeout") or 30),
        }

    # ---------- 测试 ----------
    def _test(self) -> None:
        self.status.setObjectName("hint")
        self.status.setStyleSheet("")
        self.status.setText("正在连…")
        self.btn_test.setEnabled(False)

        def ok(msg: str) -> None:
            self._pinged.emit(True, msg)

        def err(msg: str) -> None:
            self._pinged.emit(False, msg)

        llm.ping(self._current_llm(), ok, err)

    def _on_pinged(self, ok: bool, msg: str) -> None:
        self.btn_test.setEnabled(True)
        self.status.setObjectName("status_ok" if ok else "status_err")
        self.status.setText(("✓ " if ok else "✗ ") + msg)

    # ---------- 保存 ----------
    def _save(self) -> None:
        cur = self._current_llm()
        if not cur["base_url"]:
            self._on_pinged(False, "Base URL 不能空着")
            return
        if not cur["model"]:
            self._on_pinged(False, "模型名不能空着")
            return
        if not cur["api_key"] and not llm.is_local(cur["base_url"]):
            self._on_pinged(False, "这个地址需要 API Key")
            return

        self.cfg["llm_preset"] = self.cb.currentText()
        self.cfg["llm_base_url"] = cur["base_url"]
        self.cfg["llm_api_key"] = cur["api_key"]
        self.cfg["llm_model"] = cur["model"]
        config.save_config(self.cfg)
        self.hide()
        self.saved.emit(self.cfg)

    # ---------- 弹出 ----------
    def popup_near(self, anchor: QWidget) -> None:
        self._load_from_cfg()
        self.status.setText("")
        self.btn_test.setEnabled(True)
        self.adjustSize()
        geo = anchor.screen().availableGeometry()
        x = anchor.x() + anchor.width() // 2 - self.width() // 2
        y = anchor.y() + anchor.height() // 2 - self.height() // 2
        x = max(geo.left() + 6, min(geo.right() - self.width() - 6, x))
        y = max(geo.top() + 6, min(geo.bottom() - self.height() - 6, y))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.ed_base.setFocus()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def event(self, e):
        # 失焦不自动关：用户可能要去浏览器复制 Key
        return super().event(e)
