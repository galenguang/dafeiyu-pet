# -*- coding: utf-8 -*-
"""截屏工具：抓整个桌面（含透明置顶窗口）到 preview/ 下。"""
import os
import sys

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
scr = app.primaryScreen()
geo = scr.geometry()
pix = scr.grabWindow(0)
out = sys.argv[1] if len(sys.argv) > 1 else "preview/shot.png"
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
pix.save(out, "PNG")
print(f"screen {geo.width()}x{geo.height()} dpr={scr.devicePixelRatio()} -> {out} {pix.width()}x{pix.height()}")
