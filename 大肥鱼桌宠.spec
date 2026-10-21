# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

产物是目录形式（dist/大肥鱼桌宠/），启动快；不推荐 onefile，因为要带
50MB 动画素材，单文件每次启动都要解压一遍。
"""
import os

block_cipher = None

EXCLUDES = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "notebook",
    "numpy", "PIL", "PIL.Image", "PIL.ImageDraw",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtSensors", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtGraphs",
]

datas = [
    ("assets/webm", "assets/webm"),
    ("assets/icon.png", "assets"),
    ("assets/icon.ico", "assets"),
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["av", "psutil", "pynvml",
                   # app.memory 的 __getattr__ 是延迟导入，PyInstaller 静态分析看不到
                   "app.memory.core", "app.memory.store", "app.memory.schema",
                   "app.memory.rules", "app.memory.extract", "app.memory.behavior",
                   "app.memory.recall", "app.memory.voice",
                   "app.ui.memory",
                   # app.pet 里 agent 那一整段是 try/except 包着的延迟导入，
                   # 静态分析同样看不到；漏一个就会"打包出来点菜单没反应"
                   "app.agent.protocol", "app.agent.prompts", "app.agent.llm",
                   "app.agent.sandbox", "app.agent.tools", "app.agent.win32",
                   "app.agent.gate", "app.agent.runner", "app.agent.todo",
                   "app.agent.stall", "app.agent.context", "app.agent.journal",
                   "app.ui.confirm", "app.ui.agent_panel", "app.ui.agent_bridge"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="大肥鱼桌宠",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="大肥鱼桌宠",
)
