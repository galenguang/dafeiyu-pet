# -*- coding: utf-8 -*-
"""打包后瘦身：删掉运行时不用的文件，然后跑自检确认还能启动。

只删「确定用不到」的东西：
  - numpy / PIL：只有开发工具用，主程序不 import
  - opengl32sw.dll：软件 OpenGL 回退，我们只用 QPainter 光栅绘制
  - Qt6Quick / Qml：没用到 QML
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "大肥鱼桌宠")
INTERNAL = os.path.join(DIST, "_internal")

PATTERNS = [
    # 开发工具才用的库
    "numpy", "numpy.libs", "numpy-*.dist-info",
    "PIL", "Pillow-*.dist-info",
    # 不用的 Qt 模块 / 软件 GL
    "opengl32sw.dll",
    # Qt 自带的多语言翻译，我们界面文案全是中文硬编码，用不到
    "PySide6/translations",
    "PySide6/Qt6Quick*.dll",
    "PySide6/Qt6Qml*.dll",
    "PySide6/Qt6VirtualKeyboard*.dll",
    "PySide6/Qt6Pdf*.dll",
    "PySide6/Qt6Designer*.dll",
    "PySide6/Qt6Test*.dll",
    "PySide6/Qt6Sql*.dll",
    "PySide6/Qt6Charts*.dll",
    "PySide6/Qt6DataVisualization*.dll",
    "PySide6/Qt6Multimedia*.dll",
    "PySide6/Qt6WebEngine*.dll",
    "PySide6/Qt6Bluetooth*.dll",
    "PySide6/Qt6Nfc*.dll",
    "PySide6/Qt6Positioning*.dll",
    "PySide6/Qt6SerialPort*.dll",
    "PySide6/Qt6Sensors*.dll",
    "PySide6/Qt6RemoteObjects*.dll",
    "PySide6/Qt6Scxml*.dll",
    "PySide6/Qt6StateMachine*.dll",
    "PySide6/Qt6SpatialAudio*.dll",
    "PySide6/Qt6WebSockets*.dll",
    "PySide6/Qt6WebChannel*.dll",
    "PySide6/Qt6Graphs*.dll",
]


def size_of(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def main() -> int:
    if not os.path.isdir(DIST):
        print("找不到 dist 目录，先打包")
        return 1
    before = size_of(DIST)
    removed = 0
    for pat in PATTERNS:
        for path in glob.glob(os.path.join(INTERNAL, pat)):
            sz = size_of(path)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                removed += sz
                print(f"  - {os.path.relpath(path, INTERNAL)}  {sz / 1024 / 1024:.1f}MB")
            except OSError as exc:
                print(f"  ! 删不掉 {path}: {exc}")
    after = size_of(DIST)
    print(f"\n瘦身: {before / 1024 / 1024:.0f}MB -> {after / 1024 / 1024:.0f}MB (省 {removed / 1024 / 1024:.0f}MB)")

    # 自检
    exe = os.path.join(DIST, "大肥鱼桌宠.exe")
    log = os.path.join(os.environ.get("TEMP", "."), "dafeiyu_selftest.txt")
    if os.path.exists(log):
        os.remove(log)
    print("\n运行自检…")
    try:
        subprocess.run([exe, "--selftest"], timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        print("自检超时")
        return 1
    if os.path.exists(log):
        print(open(log, encoding="utf-8").read())
        return 0 if "PASS" in open(log, encoding="utf-8").read() else 1
    print("自检没有产出结果文件")
    return 1


if __name__ == "__main__":
    sys.exit(main())
