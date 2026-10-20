# -*- coding: utf-8 -*-
"""裁剪图片：crop.py in.png out.png x y w h"""
import sys

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
src, dst = sys.argv[1], sys.argv[2]
x, y, w, h = (int(v) for v in sys.argv[3:7])
img = QGuiApplication  # 保持引用
from PySide6.QtGui import QImage

im = QImage(src)
print("src", im.width(), im.height())
im.copy(x, y, w, h).save(dst, "PNG")
print("saved", dst, w, h)
