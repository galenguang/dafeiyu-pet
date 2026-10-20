# -*- coding: utf-8 -*-
"""列出桌宠顶层窗口的标题/位置/可见性，用来排查窗口状态。"""
import ctypes
from ctypes import wintypes

u = ctypes.windll.user32
u.SetProcessDPIAware()

EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def cb(hwnd, _):
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    u.GetWindowTextW(hwnd, buf, n + 2)
    title = buf.value
    if "大肥鱼" not in title:
        return True
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    print(f"hwnd={hwnd} pid={pid.value} visible={u.IsWindowVisible(hwnd)} "
          f"rect=({r.left},{r.top})-({r.right},{r.bottom}) "
          f"size={r.right - r.left}x{r.bottom - r.top} title={title!r}")
    return True


u.EnumWindows(EnumProc(cb), 0)
print("screen", u.GetSystemMetrics(0), "x", u.GetSystemMetrics(1))
