# -*- coding: utf-8 -*-
"""向桌宠窗口投递鼠标消息，做端到端交互测试。

用法:
  click.py click  <sx> <sy>              在屏幕坐标 (sx,sy) 单击
  click.py rclick <sx> <sy>              右键
  click.py drag   <sx> <sy> <ex> <ey>    从 (sx,sy) 拖到 (ex,ey)

坐标都是物理屏幕像素。每次投递都按「当前窗口位置」换算成客户区坐标，
否则窗口一动，固定客户区点就会漂移（这不是真实鼠标的行为）。
"""
import ctypes
import sys
import time
from ctypes import wintypes

u = ctypes.windll.user32
u.SetProcessDPIAware()

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def find():
    """返回可见的桌宠窗口里面积最大的那个。

    菜单弹出时也是同一个标题，按面积挑可以避免误点到菜单上。
    """
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        u.GetWindowTextW(hwnd, buf, n + 2)
        if buf.value == "大肥鱼桌宠" and u.IsWindowVisible(hwnd):
            r = RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            found.append(((r.right - r.left) * (r.bottom - r.top), hwnd))
        return True

    u.EnumWindows(cb, 0)
    found.sort(reverse=True)
    return [h for _, h in found]


def origin(hwnd):
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def post_screen(hwnd, msg, sx, sy):
    ox, oy, w, h = origin(hwnd)
    cx, cy = sx - ox, sy - oy
    u.PostMessageW(hwnd, msg, 1, ((cy & 0xFFFF) << 16) | (cx & 0xFFFF))


hwnds = find()
if not hwnds:
    print("没找到可见的桌宠窗口")
    raise SystemExit(1)
hwnd = hwnds[0]
print("hwnd", hwnd, "rect", origin(hwnd))

mode = sys.argv[1] if len(sys.argv) > 1 else "click"
a = [int(v) for v in sys.argv[2:]]

# 不给坐标时，自动取窗口中心（桌宠会走动，绝对坐标容易点空）
if mode in ("click", "dblclick", "rclick") and len(a) < 2:
    ox, oy, w, h = origin(hwnd)
    a = [ox + w // 2, oy + int(h * 0.62)]

if mode == "click":
    post_screen(hwnd, WM_MOUSEMOVE, a[0], a[1])
    post_screen(hwnd, WM_LBUTTONDOWN, a[0], a[1])
    time.sleep(0.05)
    post_screen(hwnd, WM_LBUTTONUP, a[0], a[1])
    print("left click", a)
elif mode == "dblclick":
    post_screen(hwnd, WM_MOUSEMOVE, a[0], a[1])
    post_screen(hwnd, WM_LBUTTONDOWN, a[0], a[1])
    post_screen(hwnd, WM_LBUTTONUP, a[0], a[1])
    time.sleep(0.03)
    post_screen(hwnd, WM_LBUTTONDBLCLK, a[0], a[1])
    post_screen(hwnd, WM_LBUTTONUP, a[0], a[1])
    print("double click", a)
elif mode == "rclick":
    post_screen(hwnd, WM_MOUSEMOVE, a[0], a[1])
    post_screen(hwnd, WM_RBUTTONDOWN, a[0], a[1])
    time.sleep(0.05)
    post_screen(hwnd, WM_RBUTTONUP, a[0], a[1])
    print("right click", a)
elif mode == "drag":
    sx, sy, ex, ey = a
    post_screen(hwnd, WM_MOUSEMOVE, sx, sy)
    post_screen(hwnd, WM_LBUTTONDOWN, sx, sy)
    for i in range(1, 21):
        post_screen(hwnd, WM_MOUSEMOVE, sx + (ex - sx) * i // 20, sy + (ey - sy) * i // 20)
        time.sleep(0.02)
    post_screen(hwnd, WM_LBUTTONUP, ex, ey)
    print(f"drag ({sx},{sy}) -> ({ex},{ey})")
time.sleep(1.5)
print("after", origin(hwnd))
