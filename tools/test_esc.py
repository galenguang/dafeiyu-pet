# -*- coding: utf-8 -*-
"""验证体检面板的 Esc 关闭：真实键盘发送（SendInput 路径）。"""
import ctypes
import sys
import time
from ctypes import wintypes

u = ctypes.windll.user32
u.SetProcessDPIAware()
KEYEVENTF_KEYUP = 0x0002
VK_ESCAPE = 0x1B


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


h = find_panel()
if not h:
    print("体检面板未打开")
    sys.exit(1)

print(f"面板 hwnd={h} 可见")
# 先给它焦点，再发 Esc
u.SetForegroundWindow(h)
time.sleep(0.4)
u.keybd_event(VK_ESCAPE, 0, 0, 0)
time.sleep(0.06)
u.keybd_event(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0)
time.sleep(0.8)

still = bool(u.IsWindowVisible(h))
print(f"  {'FAIL' if still else 'PASS'}  Esc 关闭 (可见={still})")
print("ESC TEST " + ("FAIL" if still else "PASS"))
sys.exit(1 if still else 0)
