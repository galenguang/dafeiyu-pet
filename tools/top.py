# -*- coding: utf-8 -*-
"""把指定尺寸的桌宠窗口强制提到最前，验证是否层级问题。"""
import ctypes
import sys
import time
from ctypes import wintypes

u = ctypes.windll.user32
u.SetProcessDPIAware()
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


rows = []


@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
def cb(hwnd, _):
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    u.GetWindowTextW(hwnd, buf, n + 2)
    if buf.value == "大肥鱼桌宠":
        r = RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        rows.append((hwnd, r.right - r.left, r.bottom - r.top, u.IsWindowVisible(hwnd)))
    return True


u.EnumWindows(cb, 0)
for hwnd, w, h, vis in rows:
    print(f"hwnd={hwnd} {w}x{h} visible={vis}")
    if w > 700 and h > 1000:
        u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        print("  -> 提到最前")
time.sleep(0.6)
