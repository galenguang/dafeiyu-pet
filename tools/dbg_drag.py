# -*- coding: utf-8 -*-
"""逐步跟踪拖动：每一步打印窗口位置和光标位置，看是哪一步断的。"""
import ctypes
import sys
import time
from ctypes import wintypes

u = ctypes.windll.user32
u.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


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


def geo(hwnd):
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top


h = find_panel()
if not h:
    print("面板没开")
    sys.exit(1)

x0, y0 = geo(h)
print(f"起始窗口 ({x0},{y0})")
sx, sy = x0 + 120, y0 + 50
print(f"按下点 ({sx},{sy})")
u.SetCursorPos(sx, sy)
time.sleep(0.2)
u.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.1)

for i in range(1, 11):
    tx, ty = sx + 7 * i, sy + 4 * i
    u.SetCursorPos(tx, ty)
    time.sleep(0.08)
    gx, gy = geo(h)
    cur = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(cur))
    print(f"  step{i:2d} 光标({tx},{ty}) 窗口({gx},{gy}) 位移({gx - x0:+d},{gy - y0:+d})")

u.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(0.3)
print("结束窗口", geo(h))
