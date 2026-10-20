# -*- coding: utf-8 -*-
"""给指定窗口发一个按键（默认 ESC），用来关掉弹出的菜单。"""
import ctypes
import sys
import time
from ctypes import wintypes

u = ctypes.windll.user32
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
VK = {"esc": 0x1B}

key = VK.get((sys.argv[1] if len(sys.argv) > 1 else "esc").lower(), 0x1B)

targets = []


@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
def cb(hwnd, _):
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    u.GetWindowTextW(hwnd, buf, n + 2)
    if buf.value == "大肥鱼桌宠" and u.IsWindowVisible(hwnd):
        targets.append(hwnd)
    return True


u.EnumWindows(cb, 0)
for h in targets:
    u.PostMessageW(h, WM_KEYDOWN, key, 0)
    u.PostMessageW(h, WM_KEYUP, key, 0)
    time.sleep(0.1)
print("sent ESC to", targets)
