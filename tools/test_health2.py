# -*- coding: utf-8 -*-
"""体检面板拖动全覆盖测试：在标题、指标条、标签、空白处分别按下拖动。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

app = QApplication(sys.argv)

from app.sysmon import Monitor
from app.ui.health import Bar, HealthPanel

m = Monitor(interval=0.3)
m.start()
time.sleep(2.5)

panel = HealthPanel(m)
panel.move(200, 150)
panel.show()
app.processEvents()
panel.refresh()
app.processEvents()

results = []
ok = True


def drag_from(widget, gpos, dx, dy, label, expect=None):
    """在 widget 上按下，向外拖 (dx,dy)，检查面板有没有跟着动。"""
    global ok
    start = panel.geometry().topLeft()
    local = QPointF(widget.mapFromGlobal(gpos))
    press = QMouseEvent(QEvent.Type.MouseButtonPress, local, QPointF(gpos),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier)
    app.sendEvent(widget, press)
    for i in range(1, 9):
        g = QPoint(gpos.x() + dx * i // 8, gpos.y() + dy * i // 8)
        mv = QMouseEvent(QEvent.Type.MouseMove, QPointF(widget.mapFromGlobal(g)), QPointF(g),
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        app.sendEvent(widget, mv)
        app.processEvents()
    end_g = QPoint(gpos.x() + dx, gpos.y() + dy)
    rel = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(widget.mapFromGlobal(end_g)),
                      QPointF(end_g), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                      Qt.KeyboardModifier.NoModifier)
    app.sendEvent(widget, rel)
    app.processEvents()

    moved = panel.geometry().topLeft()
    got = (moved.x() - start.x(), moved.y() - start.y())
    want = expect if expect is not None else (dx, dy)
    good = got == want
    if not good:
        ok = False
    msg = f"  {'PASS' if good else 'FAIL'}  从 {label} 拖动 -> 位移 {got} (期望 {want})"
    results.append(msg)
    print(msg)


# 依次从不同子控件拖动，每次先归位
targets = [
    (panel.verdict, "标题下的小字"),
    (panel.ring, "健康分圆环"),
    (panel.b_cpu, "CPU 指标条"),
    (panel.b_ram, "内存指标条"),
    (panel, "面板空白处"),
]
for i, (w, label) in enumerate(targets):
    panel.move(200 + i * 10, 150)
    app.processEvents()
    c = w.mapToGlobal(w.rect().center())
    drag_from(w, c, 40, 24, label)

# 关闭按钮不能触发拖动
panel.move(300, 200)
app.processEvents()
before = panel.geometry().topLeft()
bc = panel.close_btn.mapToGlobal(panel.close_btn.rect().center())
drag_from(panel.close_btn, bc, 40, 24, "关闭按钮(应不拖动)", expect=(0, 0))
after = panel.geometry().topLeft()
no_move = before == after
print(f"  {'PASS' if no_move else 'FAIL'}  关闭按钮不触发拖动 (面板未移动)")
if not no_move:
    ok = False

m.stop()
print("DRAG TEST " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
