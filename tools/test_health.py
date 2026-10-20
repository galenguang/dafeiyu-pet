# -*- coding: utf-8 -*-
"""体检面板交互测试：弹出 → 拖动 → Esc 关闭 → 再弹出 → 关闭按钮。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

from app.sysmon import Monitor
from app.ui.health import HealthPanel

m = Monitor(interval=0.3)
m.start()
import time
time.sleep(2)

panel = HealthPanel(m)
out_dir = os.path.join(tempfile.gettempdir(), "dafeiyu_health")
os.makedirs(out_dir, exist_ok=True)
log = []

anchor = type("A", (), {"x": lambda s: 400, "y": lambda s: 300,
                        "width": lambda s: 448, "height": lambda s: 309,
                        "screen": lambda s: app.primaryScreen()})()


def send(widget, etype, gpos, button=Qt.MouseButton.LeftButton, buttons=None):
    if buttons is None:
        buttons = Qt.MouseButton.LeftButton if etype in (
            QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease) else Qt.MouseButton.NoButton
    ev = QMouseEvent(etype, QPointF(widget.mapFromGlobal(gpos)), QPointF(gpos),
                     button, buttons, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(widget, ev)


def step1():
    panel.popup_near(anchor)
    log.append(f"弹出: visible={panel.isVisible()} geo={panel.geometry().getRect()}")
    QTimer.singleShot(400, step2)


def step2():
    panel.grab().save(os.path.join(out_dir, "1_open.png"), "PNG")
    start = panel.geometry().topLeft()
    x, y = panel.x() + 40, panel.y() + 20      # 标题区空白处
    g0 = QPoint(x, y)

    send(panel, QEvent.Type.MouseButtonPress, g0)
    for i in range(1, 11):
        send(panel, QEvent.Type.MouseMove, QPoint(x + i * 12, y + i * 8))
        app.processEvents()
    send(panel, QEvent.Type.MouseButtonRelease, QPoint(x + 120, y + 80))
    app.processEvents()
    moved = panel.geometry().topLeft()
    log.append(f"拖动: {start.x()},{start.y()} -> {moved.x()},{moved.y()} "
               f"(位移 {moved.x() - start.x()},{moved.y() - start.y()})")
    QTimer.singleShot(300, step3)


def step3():
    panel.grab().save(os.path.join(out_dir, "2_dragged.png"), "PNG")
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import Qt as Q
    ev = QKeyEvent(QEvent.Type.KeyPress, Q.Key.Key_Escape, Q.KeyboardModifier.NoModifier)
    panel.setFocus()
    app.sendEvent(panel, ev)
    app.processEvents()
    log.append(f"Esc 关闭: visible={panel.isVisible()}")
    QTimer.singleShot(200, step4)


def step4():
    panel.popup_near(anchor)
    app.processEvents()
    log.append(f"再次弹出: visible={panel.isVisible()}")
    panel.close_btn.click()
    app.processEvents()
    log.append(f"点 × 关闭: visible={panel.isVisible()}")
    m.stop()
    txt = "\n".join(log)
    open(os.path.join(out_dir, "log.txt"), "w", encoding="utf-8").write(txt)
    print(txt)
    app.quit()


QTimer.singleShot(200, step1)
app.exec()
