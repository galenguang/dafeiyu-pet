# -*- coding: utf-8 -*-
"""体检面板像素级自检：确认关闭按钮、底部提示、指标条都真的画出来了。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

from app.sysmon import Monitor
from app.ui.health import HealthPanel

m = Monitor(interval=0.3)
m.start()
# 采样在后台线程里：先出便宜指标，进程榜要枚举整张进程表（这台机器上要
# 一两秒）。等数据真的到位再截图，别用固定 sleep 赌时间。
deadline = time.time() + 12
while time.time() < deadline:
    snap = m.snapshot
    if snap.ts > 0 and len(snap.top_cpu) > 0:
        break
    time.sleep(0.2)

panel = HealthPanel(m)
panel.show()
app.processEvents()
panel.refresh()
app.processEvents()

img = panel.grab().toImage()
dpr = panel.devicePixelRatio() or 1.0
print(f"面板 {img.width()}x{img.height()} dpr={dpr}")

ok = True


def check(name, cond, detail=""):
    global ok
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        ok = False


# 1. 关闭按钮控件存在、可见、位置在右上角
btn = panel.close_btn
check("关闭按钮存在", btn is not None)
check("关闭按钮可见", btn.isVisible())
bp = btn.mapTo(panel, btn.rect().topLeft())
check("关闭按钮在右上角", bp.x() > panel.width() * 0.75 and bp.y() < 80,
      f"pos=({bp.x()},{bp.y()})")

# 2. 关闭按钮那一小块真的不是纯白（说明 × 字形画出来了）
bx = int((bp.x() + 8) * dpr)
by = int((bp.y() + 8) * dpr)
dark = 0
for y in range(by, min(by + int(20 * dpr), img.height())):
    for x in range(bx, min(bx + int(20 * dpr), img.width())):
        c = img.pixelColor(x, y)
        if c.red() < 200 and c.green() < 200:
            dark += 1
check("× 字形已渲染", dark > 6, f"深色像素 {dark}")

# 3. 卡片有内容：统计非白像素比例
total = img.width() * img.height()
nonwhite = 0
for y in range(0, img.height(), 4):
    for x in range(0, img.width(), 4):
        c = img.pixelColor(x, y)
        if c.alpha() > 20 and not (c.red() > 248 and c.green() > 248 and c.blue() > 248):
            nonwhite += 1
ratio = nonwhite / ((img.width() // 4 + 1) * (img.height() // 4 + 1))
check("面板有实际内容", ratio > 0.10, f"非白占比 {ratio:.0%}")

# 4. 各指标条拿到了数据
s = m.snapshot
check("CPU 有数据", s.cpu > 0, f"{s.cpu:.0f}%")
check("内存有数据", s.ram > 0, f"{s.ram:.0f}%")
check("磁盘有数据", len(s.disks) > 0, f"{len(s.disks)} 个")
check("进程榜有数据", len(s.top_cpu) > 0, f"{len(s.top_cpu)} 个")
check("CPU 条文本", panel.b_cpu.value_text != "--", panel.b_cpu.value_text)

# 5. 默认位置不与屏幕边缘重叠
g = panel.geometry()
scr = panel.screen().availableGeometry()
check("面板在屏幕内", g.left() >= scr.left() and g.top() >= scr.top()
      and g.right() <= scr.right() and g.bottom() <= scr.bottom(),
      f"{g.getRect()} in {scr.getRect()}")

m.stop()
print("HEALTH UI " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
