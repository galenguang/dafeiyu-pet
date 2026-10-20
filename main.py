# -*- coding: utf-8 -*-
"""大肥鱼桌宠 —— 入口。

启动流程：高 DPI 设置 → 单实例检查 → 创建主窗口 → 进入事件循环。
"""
from __future__ import annotations

import ctypes
import os
import sys
import traceback

# 让 Windows 任务栏显示我们自己的图标而不是 python 的
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DafeiyuPet.Desktop.1")
except Exception:
    pass

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app import config  # noqa: E402


def _already_running() -> bool:
    """用命名互斥量做单实例，避免重复开一堆鱼。"""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, "Global\\DafeiyuPet_SingleInstance")
        return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def _install_excepthook() -> None:
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        print(text, file=sys.stderr)
        try:
            log = os.path.join(config.data_dir(), "error.log")
            with open(log, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass
        try:
            QMessageBox.critical(None, "大肥鱼出错了",
                                 f"{exc}\n\n详细信息已写入 error.log")
        except Exception:
            pass

    sys.excepthook = hook


def _selftest() -> int:
    """打包后自检：素材是否齐全、能不能解码、Qt 是否正常。

    结果写到临时文件（打包成窗口程序后没有控制台）。
    """
    import tempfile
    import threading
    import time

    import psutil

    lines = []
    ok = True
    try:
        app = QApplication(sys.argv)
        from app.actions import ACTIONS
        from app.clip import ClipCache

        cache = ClipCache()
        lines.append(f"素材目录: {cache.dir}")
        missing = [a.file for a in ACTIONS.values() if not cache.exists(f"{a.file}.webm")]
        if missing:
            ok = False
            lines.append(f"缺失素材 {len(missing)} 个: {missing[:5]}")
        else:
            lines.append(f"素材齐全: {len(ACTIONS)} 个动作")

        player = cache.get("idle.webm")
        got = 0
        deadline = time.time() + 8
        while got < 3 and time.time() < deadline:
            if player.poll() is not None:
                got += 1
            else:
                time.sleep(0.01)
        if got < 3:
            ok = False
            lines.append(f"解码失败: {player.error or '超时'}")
        else:
            lines.append("解码正常 (idle.webm)")
        cache.drop_all()

        # 采样线程里的异常默认只打到 stderr，打包成窗口程序后看不到，
        # 这里挂一个钩子把它记到自检结果里。
        thread_errors: list[str] = []

        def _thread_hook(args):
            thread_errors.append(f"{args.thread.name}: {args.exc_type.__name__}: {args.exc_value}")

        threading.excepthook = _thread_hook

        from app.sysmon import Monitor
        m = Monitor(interval=0.3)
        m.start()
        # 首次采样要枚举全部进程，冷启动时可能比较慢，等数据真正到位再读
        deadline = time.time() + 20
        snap = m.snapshot
        while snap.ts <= 0 and time.time() < deadline:
            time.sleep(0.2)
            snap = m.snapshot
        m.stop()
        if thread_errors:
            ok = False
            lines.append("采样线程异常: " + " | ".join(thread_errors[:3]))
        lines.append(f"系统监控: CPU {snap.cpu:.0f}% 内存 {snap.ram:.0f}% 磁盘 {len(snap.disks)} 个")
        if snap.cpu <= 0 or snap.ram <= 0 or not snap.disks:
            ok = False
            lines.append("监控数据不完整，逐项诊断：")
            for label, fn in (
                ("virtual_memory", lambda: psutil.virtual_memory().percent),
                ("disk_partitions", lambda: len(psutil.disk_partitions(all=False))),
                ("cpu_percent", lambda: psutil.cpu_percent(interval=0.2)),
                ("process_iter", lambda: len(list(psutil.process_iter(["name"])))),
            ):
                try:
                    lines.append(f"  {label} -> {fn()}")
                except Exception as exc:
                    lines.append(f"  {label} -> 异常 {exc!r}")
        app.quit()
    except Exception as exc:
        ok = False
        lines.append("异常: " + repr(exc))

    lines.append("SELFTEST " + ("PASS" if ok else "FAIL"))
    out = os.path.join(tempfile.gettempdir(), "dafeiyu_selftest.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0 if ok else 1


def _probe() -> int:
    """诊断模式：起窗口 → 跑 3 秒 → 把几何/状态和自截图写到临时目录。"""
    import tempfile

    from PySide6.QtCore import QTimer

    out_dir = os.path.join(tempfile.gettempdir(), "dafeiyu_probe")
    os.makedirs(out_dir, exist_ok=True)
    report: list[str] = []

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    from app.pet import PetWindow

    win = PetWindow()
    win.show()

    def dump():
        g = win.geometry()
        scr = win.screen()
        sg = scr.geometry()
        ag = scr.availableGeometry()
        report.append(f"screen geometry={sg.x()},{sg.y()} {sg.width()}x{sg.height()} dpr={scr.devicePixelRatio()}")
        report.append(f"available={ag.x()},{ag.y()} {ag.width()}x{ag.height()}")
        report.append(f"window geometry={g.x()},{g.y()} {g.width()}x{g.height()}")
        report.append(f"isVisible={win.isVisible()} isHidden={win.isHidden()} "
                      f"opacity={win.windowOpacity()} scale={win.scale} mode={win.mode}")
        report.append(f"winId=0x{int(win.winId()):X}")
        report.append(f"frame={'None' if win.frame is None else f'{win.frame.width()}x{win.frame.height()}'}")
        report.append(f"player={'None' if win.player is None else win.player.path} "
                      f"err={None if win.player is None else win.player.error}")
        report.append(f"monitor cpu={win._monitor.snapshot.cpu:.0f}% ram={win._monitor.snapshot.ram:.0f}% "
                      f"disks={len(win._monitor.snapshot.disks)}")
        pix = win.grab()
        p1 = os.path.join(out_dir, "window.png")
        pix.save(p1, "PNG")
        report.append(f"grab -> {p1} {pix.width()}x{pix.height()}")
        full = scr.grabWindow(0)
        p2 = os.path.join(out_dir, "screen.png")
        full.save(p2, "PNG")
        report.append(f"screen grab -> {p2} {full.width()}x{full.height()}")
        with open(os.path.join(out_dir, "probe.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(report))
        app.quit()

    QTimer.singleShot(3000, dump)
    app.exec()
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()
    if "--probe" in sys.argv:
        return _probe()

    app = QApplication(sys.argv)
    app.setApplicationName("大肥鱼桌宠")
    app.setApplicationDisplayName("大肥鱼桌宠")
    app.setQuitOnLastWindowClosed(False)

    icon_path = config.asset_path("icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    if _already_running():
        QMessageBox.information(None, "大肥鱼桌宠", "已经有一只大肥鱼在桌面上啦～\n"
                                                   "看看屏幕右下角，或者托盘图标。")
        return 0

    _install_excepthook()

    from app.pet import PetWindow
    window = PetWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
