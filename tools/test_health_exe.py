# -*- coding: utf-8 -*-
"""对真实 exe 做体检面板测试：真实光标拖拽 → 点 × 关闭。

用 SetCursorPos + mouse_event 走真实输入路径。
关键：位移必须在「松开鼠标之前」取。松手时 _keep_on_screen() 会把窗口往屏幕内
吸附一点，那是安全兜底、不是拖动失败，用吸附后的位置断言会误判。
"""
import ctypes
import sys
import time
from ctypes import wintypes

u = ctypes.windll.user32
u.SetProcessDPIAware()

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def geo(hwnd):
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def is_visible(hwnd):
    return bool(u.IsWindowVisible(hwnd))


def find_panel():
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        u.GetWindowTextW(hwnd, buf, n + 2)
        if "体检" in buf.value and u.IsWindowVisible(hwnd):
            out.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return out[0] if out else None


def drag(hwnd, sx, sy, dx, dy, steps=20):
    """在屏幕坐标 (sx,sy) 按下，拖 (dx,dy)。返回拖动过程中的位移。"""
    x0, y0 = geo(hwnd)[:2]
    u.SetCursorPos(sx, sy)
    time.sleep(0.2)
    u.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.12)
    for i in range(1, steps + 1):
        u.SetCursorPos(sx + dx * i // steps, sy + dy * i // steps)
        time.sleep(0.05)
    time.sleep(0.2)
    during = geo(hwnd)[:2]                 # 松手前取样
    u.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.4)
    after = geo(hwnd)[:2]                  # 松手吸附后
    return (during[0] - x0, during[1] - y0), (after[0] - x0, after[1] - y0)


def click(hwnd, sx, sy):
    u.SetCursorPos(sx, sy)
    time.sleep(0.2)
    u.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    u.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.6)


ok = True


def check(name, cond, detail=""):
    global ok
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        ok = False


h = find_panel()
if not h:
    print("体检面板未打开，先跑 --open-health")
    sys.exit(1)

px, py, pw, ph = geo(h)
print(f"面板 hwnd={h} {pw}x{ph} at ({px},{py})")

# 1. 标题区拖动
during1, after1 = drag(h, px + 120, py + 50, 70, 45)
# 步进里用整数除法会有 1px 取整误差，允许 ±1
check("标题区拖动（拖动中）", abs(during1[0] - 70) <= 1 and abs(during1[1] - 45) <= 1,
      f"位移 {during1} (期望约 (70,45))")

# 2. 指标条区域拖动（验证子控件也能拖）
px, py = geo(h)[:2]
during2, after2 = drag(h, px + 200, py + 300, -50, 30)
check("指标条上拖动（拖动中）", abs(during2[0] + 50) <= 1 and abs(during2[1] - 30) <= 1,
      f"位移 {during2} (期望约 (-50,30))")

# 3. 拖出屏幕后应被吸附回来
px, py = geo(h)[:2]
during3, after3 = drag(h, px + 100, py + 60, -px - 200, -py - 200)
px3, py3 = geo(h)[:2]
check("拖出屏幕被吸附回来", px3 >= 0 and py3 >= 0, f"最终位置 ({px3},{py3})")

# 4. 点右上角 × 关闭
px, py, pw, ph = geo(h)
click(h, px + pw - 40, py + 40)
check("点 × 关闭", not is_visible(h))

print("HEALTH EXE " + ("PASS" if ok else "FAIL"))
