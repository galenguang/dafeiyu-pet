# -*- coding: utf-8 -*-
"""通过真实右键菜单打开体检报告：右键鱼 → 键盘选中「体检报告」→ 回车。

比盲点坐标可靠：菜单弹出后用方向键导航。
"""
import ctypes
import sys
import time
from ctypes import wintypes

u = ctypes.windll.user32
u.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def find_pet():
    best = None

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        nonlocal best
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        u.GetWindowTextW(hwnd, buf, n + 2)
        if buf.value == "大肥鱼桌宠" and u.IsWindowVisible(hwnd):
            r = RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if best is None or area > best[0]:
                best = (area, hwnd, r.left, r.top, r.right - r.left, r.bottom - r.top)
        return True

    u.EnumWindows(cb, 0)
    return best


pet = find_pet()
if not pet:
    print("没找到桌宠窗口")
    sys.exit(1)
_, hwnd, px, py, pw, ph = pet
print(f"桌宠 hwnd={hwnd} at ({px},{py}) {pw}x{ph}")

# 右键鱼身中心偏下
cx, cy = px + pw // 2, py + int(ph * 0.65)
u.SetCursorPos(cx, cy)
time.sleep(0.2)
u.mouse_event(0x0008, 0, 0, 0, 0)   # RIGHTDOWN
time.sleep(0.08)
u.mouse_event(0x0010, 0, 0, 0, 0)   # RIGHTUP
time.sleep(0.8)
print("右键菜单已弹出")

# 菜单项顺序：动作点播 / 互动面板 / 体检报告 —— 要按三次 ↓ 才到体检报告
for _ in range(3):
    u.keybd_event(0x28, 0, 0, 0)   # DOWN
    u.keybd_event(0x28, 0, 2, 0)
    time.sleep(0.18)
u.keybd_event(0x0D, 0, 0, 0)   # ENTER
u.keybd_event(0x0D, 0, 2, 0)
time.sleep(1.0)

# 看有没有体检面板
found = []


@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
def cb2(hwnd, _):
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    u.GetWindowTextW(hwnd, buf, n + 2)
    if "体检" in buf.value and u.IsWindowVisible(hwnd):
        r = RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        found.append((hwnd, r.right - r.left, r.bottom - r.top))
    return True


u.EnumWindows(cb2, 0)
if found:
    print(f"PASS 体检面板已打开: {found[0]}")
    print("OPEN HEALTH PASS")
else:
    print("FAIL 体检面板没打开")
    print("OPEN HEALTH FAIL")
    sys.exit(1)
