# -*- coding: utf-8 -*-
"""纯 GDI 截屏（不创建 QApplication，不会抢焦点），存成 BMP。

用法: gdi_shot.py out.bmp
"""
import ctypes
import sys
from ctypes import wintypes

u = ctypes.windll.user32
g = ctypes.windll.gdi32
u.SetProcessDPIAware()

SRCCOPY = 0x00CC0020
BI_RGB = 0
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


w = u.GetSystemMetrics(0)
h = u.GetSystemMetrics(1)

screen_dc = u.GetDC(0)
mem_dc = g.CreateCompatibleDC(screen_dc)
bmp = g.CreateCompatibleBitmap(screen_dc, w, h)
g.SelectObject(mem_dc, bmp)
g.BitBlt(mem_dc, 0, 0, w, h, screen_dc, 0, 0, SRCCOPY)

bi = BITMAPINFO()
bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bi.bmiHeader.biWidth = w
bi.bmiHeader.biHeight = -h          # 负数 = 自上而下
bi.bmiHeader.biPlanes = 1
bi.bmiHeader.biBitCount = 32
bi.bmiHeader.biCompression = BI_RGB

stride = w * 4
buf = ctypes.create_string_buffer(stride * h)
g.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)

# 写 32 位 BMP
import struct

pixel_off = 14 + ctypes.sizeof(BITMAPINFOHEADER)
header = struct.pack("<2sIHHI", b"BM", pixel_off + stride * h, 0, 0, pixel_off)
info = bytes(bi.bmiHeader)
out = sys.argv[1] if len(sys.argv) > 1 else "shot.bmp"
with open(out, "wb") as f:
    f.write(header)
    f.write(info)
    f.write(buf.raw)

g.DeleteObject(bmp)
g.DeleteDC(mem_dc)
u.ReleaseDC(0, screen_dc)
print(f"GDI 截屏 {w}x{h} -> {out}")
