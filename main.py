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

        # 记忆模块：打包后最容易因为 hiddenimport 漏掉而整块挂掉
        try:
            import tempfile as _tf
            from app.memory import MemoryStore
            from app.memory import voice as _voice
            from app.ui.memory import MemoryPanel

            tmp = os.path.join(_tf.mkdtemp(prefix="dafeiyu_st_"), "memory.json")
            st = MemoryStore(path=tmp)
            st.reset()
            st.add_weighted("likes", "冰美式", 0.9)
            st.add_episode("event", "自检写入的一条", tags=["测试"], weight=0.5)
            st.flush()
            again = MemoryStore(path=tmp)
            n = len(again.episodes())
            panel = MemoryPanel(again, {})
            panel.hide()
            say = _voice.candidates(again, "click")
            lines.append(f"记忆模块: 落盘读回 {n} 条，面板可创建，台词 {len(say)} 条")
            if n != 1 or not say:
                ok = False
                lines.append("记忆读写或台词生成异常")
        except Exception as exc:
            ok = False
            lines.append("记忆模块异常: " + repr(exc))

        # Agent 模块：hiddenimport 漏一个就整块挂掉，这里点名检查
        try:
            from app.agent.gate import Gate            # noqa: F401
            from app.agent.runner import Runner        # noqa: F401
            from app.agent.sandbox import Sandbox      # noqa: F401
            from app.agent.tools import Toolbox
            from app.ui.agent_bridge import AgentBridge  # noqa: F401
            from app.ui.agent_panel import AgentPanel
            from app.ui.confirm import ConfirmDialog

            n = len(Toolbox(lambda: {}).specs)
            panel = AgentPanel()
            panel.hide()
            dlg = ConfirmDialog("file_write", "confirm", "x")
            if not dlg.btn_deny.isDefault():
                ok = False
                lines.append("确认窗默认按钮不是拒绝")
            dlg.deleteLater()
            panel.deleteLater()
            lines.append(f"Agent 模块: 工具 {n} 个，界面组件可创建")
            if n < 20:
                ok = False
                lines.append("工具注册不全")
        except Exception as exc:
            ok = False
            lines.append("Agent 模块异常: " + repr(exc))

        app.quit()
    except Exception as exc:
        ok = False
        lines.append("异常: " + repr(exc))

    lines.append("SELFTEST " + ("PASS" if ok else "FAIL"))
    out = os.path.join(tempfile.gettempdir(), "dafeiyu_selftest.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0 if ok else 1


def _agent_selftest() -> int:
    """Agent 自检：起一个本地假模型，跑一个三步任务，全程离线。

    **为什么不打真模型。** 打包验证要的是"这套东西在冻结环境里还能不能跑通"，
    不是"模型聪不聪明"。打真接口有三宗罪：花钱、不稳定、还得有 Key。本地假
    服务能把协议解析、沙箱执行、工具注入、路径白名单、确认门、账本、日志这一
    整条链跑一遍，而且一分钱不花、每次结果一样。

    真模型的冒烟测试请手工做（见 README 的验收清单）。
    """
    import json
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    lines: list[str] = []
    ok = True

    SCRIPT = [
        "<intent>看看机器</intent>\n<code>\ninfo = sys_info()\nprint(info['cpu_count'])\n</code>",
        "<intent>记账</intent>\n<code>\ntodo_update(id='1', content='查核数', status='done')\n</code>",
        "<intent>写个文件</intent>\n<code>\nfile_write('不该写.txt', 'x')\n</code>",
        "<intent>收工</intent>\n<code>\nfinish('自检跑通了')\n</code>",
    ]
    state = {"i": 0, "lock": threading.Lock()}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):                       # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                self.rfile.read(n)
            except Exception:
                pass
            with state["lock"]:
                i = state["i"]
                state["i"] = i + 1
            reply = SCRIPT[i] if i < len(SCRIPT) else SCRIPT[-1]
            body = json.dumps({"choices": [{"message": {"role": "assistant",
                                                        "content": reply}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="selftest-llm").start()
    port = srv.server_address[1]

    try:
        from app.agent.runner import Runner, SilentBridge
        from app.agent.tools import resolve_safe

        # 1) 路径白名单：8 类逃逸必须全拒（这一条挂了就别发版）
        escapes = [r"C:\Windows\System32\drivers\etc\hosts",
                   r"\\server\share\a.txt", r"C:\WINDOWS\SYSTEM32\config\SAM"]
        bad = []
        for p in escapes:
            try:
                resolve_safe(p, {})
                bad.append(p)
            except PermissionError:
                pass
        if bad:
            ok = False
            lines.append(f"路径白名单漏了: {bad}")
        else:
            lines.append("路径白名单: 3 类逃逸全拒")

        # 2) 跑一个三步任务；确认门默认拒绝，所以 file_write 那步必须失败
        cfg = {"agent_confirm": "always", "agent_allow_dirs": [],
               "agent_auto_approve": []}
        bridge = SilentBridge(confirm="no")
        r = Runner(lambda: cfg,
                   lambda: {"base_url": f"http://127.0.0.1:{port}/v1",
                            "api_key": "k", "model": "m", "timeout": 8},
                   bridge=bridge, max_steps=6)
        res = r.run("自检：看看这台机器几核")
        lines.append(f"主循环: ok={res.ok} steps={res.steps} tokens={res.tokens} "
                     f"summary={res.summary!r} reason={res.reason!r}")
        if not res.ok:
            ok = False
            lines.append("主循环没跑通")
        if res.steps != 4:
            ok = False
            lines.append(f"步数不对（期望 4，实际 {res.steps}）")
        if not any(t == "file_write" for t, _, _ in bridge.confirmed):
            ok = False
            lines.append("确认门没拦住 file_write")
        if not any("第 1 步" in x for x in bridge.logs):
            ok = False
            lines.append("日志没推到 UI")

        # 3) 确认门的另一种情况：允许之后要真写出来
        bridge2 = SilentBridge(confirm="yes")
        ws = os.path.join(config.data_dir(), "agent_workspace")
        os.makedirs(ws, exist_ok=True)
        target = os.path.join(ws, "自检文件.txt")
        if os.path.exists(target):
            os.remove(target)
        code = (f"<intent>写</intent>\n<code>\n"
                f"file_write({target!r}, '自检内容')\n</code>")
        srv2_script = [code, "<code>\nfinish('写完了')\n</code>"]
        state["i"] = 0
        SCRIPT[:] = srv2_script
        r2 = Runner(lambda: cfg,
                    lambda: {"base_url": f"http://127.0.0.1:{port}/v1",
                             "api_key": "k", "model": "m", "timeout": 8},
                    bridge=bridge2, max_steps=4)
        res2 = r2.run("自检：写一个文件")
        if not (res2.ok and os.path.exists(target)):
            ok = False
            lines.append(f"允许后写盘失败: ok={res2.ok} reason={res2.reason!r}")
        else:
            lines.append("写文件: 允许后落盘成功")

        # 4) 急停
        srv3_script = ["<code>\nx = 1\n</code>"] * 6
        state["i"] = 0
        SCRIPT[:] = srv3_script
        bridge3 = SilentBridge(confirm="no")
        r3 = Runner(lambda: cfg,
                    lambda: {"base_url": f"http://127.0.0.1:{port}/v1",
                             "api_key": "k", "model": "m", "timeout": 8},
                    bridge=bridge3, max_steps=6)
        r3.stop()
        res3 = r3.run("自检：急停")
        if not res3.stopped:
            ok = False
            lines.append("急停没生效")
        else:
            lines.append("急停: 立刻停下")

        # 5) 日志文件
        from app.agent.journal import Journal
        j = Journal()
        j.write("selftest", note="hello")
        lines.append(f"操作日志: {j.size()} 字节 @ {j.path}")
        if j.size() <= 0:
            ok = False
            lines.append("日志写不进去")

        # 6) 界面组件
        from PySide6.QtWidgets import QApplication
        from app.ui.agent_bridge import AgentBridge
        from app.ui.agent_panel import AgentPanel
        from app.ui.confirm import ConfirmDialog
        _app = QApplication.instance() or QApplication(sys.argv)
        panel = AgentPanel()
        panel.set_state("executing", "自检")
        panel.hide()
        dlg = ConfirmDialog("file_write", "confirm", "file_write('a','b')")
        if not dlg.btn_deny.isDefault():
            ok = False
            lines.append("确认窗的默认按钮不是拒绝")
        if dlg.btn_always.autoDefault():
            ok = False
            lines.append("「本会话都允许」抢了回车键")
        dlg.deleteLater()
        panel.deleteLater()
        br = AgentBridge()
        lines.append(f"界面: 面板/确认窗/线程桥都能创建（bridge={type(br).__name__}）")
    except Exception as exc:
        ok = False
        lines.append("Agent 自检异常: " + repr(exc))
    finally:
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass

    lines.append("AGENT-SELFTEST " + ("PASS" if ok else "FAIL"))
    out = os.path.join(tempfile.gettempdir(), "dafeiyu_agent_selftest.txt")
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass
    print("\n".join(lines))
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
    if "--agent-selftest" in sys.argv:
        return _agent_selftest()
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
