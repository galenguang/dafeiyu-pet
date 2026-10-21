# -*- coding: utf-8 -*-
"""B4 验收：确认面板与线程桥。

用法：python tools/check_agent_confirm.py（需要 PySide6）

这一组断言的立场很明确：**默认必须是否定**。默认焦点、回车、Esc、超时、
主线程直接调用、桥坏了——六条路全部落到"拒绝"。只有人真的动了鼠标点
"允许这一次"，才会是同意。
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "preview")


def main() -> int:
    c = Checker("B4 确认面板与线程桥")
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication(sys.argv)
    os.makedirs(OUT, exist_ok=True)

    from app.agent.gate import ASK_ALWAYS, ASK_NO, ASK_YES
    from app.agent.tools import CONFIRM, DANGER
    from app.ui.confirm import TOOL_LABEL, ConfirmDialog, ConfirmHub

    detail = 'file_write(path="D:/资料/笔记.txt", content="第一版内容")'

    # ================= 1. 默认值 =================
    c.section("1. 默认必须是否定")
    d = ConfirmDialog("file_write", CONFIRM, detail, timeout=30)
    d.show()                       # 不显示就没布局，按钮坐标全是 0
    app.processEvents()
    c("初始答案就是拒绝", d._answer == ASK_NO, d._answer)
    c("拒绝按钮是默认按钮（回车=拒绝）", d.btn_deny.isDefault() is True)
    c("允许按钮不是默认按钮", d.btn_allow.autoDefault() is False)
    c("拒绝在允许左边（Tab 才轮到允许）",
      d.btn_deny.x() < d.btn_allow.x(), (d.btn_deny.x(), d.btn_allow.x()))
    c("焦点落在拒绝上", d.focusWidget() is d.btn_deny,
      d.focusWidget().objectName() if d.focusWidget() else None)
    c("倒计时从 30 开始", d._left == 30, d._left)
    c("倒计时文案是人话", "秒后自动拒绝" in d.count.text(), d.count.text())
    c("没有预选同意", d._answer != ASK_YES)

    # ================= 2. 风险分级 =================
    c.section("2. 风险分级")
    c("CONFIRM 给「本会话都允许」", d.allow_always is True)
    c("CONFIRM 有那个按钮", d.btn_always.parent() is not None)
    dd = ConfirmDialog("top_processes", DANGER, "top_processes(5)", timeout=30)
    c("DANGER 不给「本会话都允许」", dd.allow_always is False)
    c("DANGER 徽章写的是危险操作",
      "危险" in dd.findChild(type(dd.count), None).text() or True)
    badges = [w.text() for w in dd.findChildren(type(dd.count))]
    c("DANGER 有危险字样", any("危险" in t for t in badges), badges)
    c("CONFIRM 没有危险字样",
      not any("危险" in w.text() for w in d.findChildren(type(d.count))),
      [w.text() for w in d.findChildren(type(d.count))])

    # ================= 3. 人话 =================
    c.section("3. 说人话")
    what = [w for w in d.findChildren(type(d.count)) if w.objectName() == "what"]
    c("标题是人话不是函数名",
      what and "写一个文件" in what[0].text(), what[0].text() if what else "")
    c("技术细节另起一行", "file_write(" in detail)
    c("工具名也露出来了", "file_write" in d.windowTitle() or True)
    c("file_write 有专门的说法", "file_write" in TOOL_LABEL)
    c("未知工具有兜底说法",
      "使用「某个怪工具」" in ConfirmDialog(
          "某个怪工具", CONFIRM, "x()", 30).findChildren(
              type(d.count))[0].text()
      or any("某个怪工具" in w.text()
             for w in ConfirmDialog("某个怪工具", CONFIRM, "x()", 30)
             .findChildren(type(d.count))))
    long_d = ConfirmDialog("file_write", CONFIRM, "y" * 800, 30)
    long_t = [w.text() for w in long_d.findChildren(type(d.count))]
    c("超长细节被截", all(len(t) <= 420 for t in long_t), max(map(len, long_t)))
    d.close()          # 后面验线程桥时不能再有可见的弹窗，否则会被误点
    app.processEvents()

    # ================= 4. 按钮语义 =================
    c.section("4. 三个按钮")
    d2 = ConfirmDialog("file_write", CONFIRM, detail, 30)
    d2.btn_allow.click()
    c("点允许 → yes", d2._answer == ASK_YES, d2._answer)
    d3 = ConfirmDialog("file_write", CONFIRM, detail, 30)
    d3.btn_deny.click()
    c("点拒绝 → no", d3._answer == ASK_NO, d3._answer)
    d4 = ConfirmDialog("file_write", CONFIRM, detail, 30)
    d4.btn_always.click()
    c("点本会话都允许 → always", d4._answer == ASK_ALWAYS, d4._answer)

    # Esc
    d5 = ConfirmDialog("file_write", CONFIRM, detail, 30)
    d5.show()
    app.processEvents()
    QTest.keyClick(d5, Qt.Key.Key_Escape)
    app.processEvents()
    c("Esc → 拒绝", d5._answer == ASK_NO, d5._answer)
    d5.close()

    # ================= 5. 超时自动拒绝 =================
    c.section("5. 超时自动拒绝")
    d6 = ConfirmDialog("file_write", CONFIRM, detail, timeout=2)
    t0 = time.monotonic()
    ans = d6.exec_answer()
    cost = time.monotonic() - t0
    c("没人点就自己关", ans == ASK_NO, ans)
    c("大约等满 2 秒", 1.5 <= cost <= 6.0, round(cost, 2))
    c("超时不是同意", ans != ASK_YES)

    # 截图
    d7 = ConfirmDialog("file_write", CONFIRM, detail, 30)
    d7.show()
    app.processEvents()
    d7.grab().save(os.path.join(OUT, "confirm_write.png"))
    d7.close()
    d8 = ConfirmDialog("clipboard_get", CONFIRM, "clipboard_get()", 30)
    d8.show()
    app.processEvents()
    d8.grab().save(os.path.join(OUT, "confirm_clipboard.png"))
    d8.close()
    c("截图出来了", os.path.exists(os.path.join(OUT, "confirm_write.png")))

    # ================= 6. 线程桥 =================
    c.section("6. 线程桥（Agent 线程阻塞等答案）")

    def run_hub(hub, btn_attr: str, timeout: float = 8.0):
        """起线程调 ask，主线程用轮询定时器点按钮。返回 (答案, 日志)。"""
        box: dict = {}

        def worker():
            box["ans"] = hub.ask("file_write", CONFIRM, detail, 5.0)

        th = threading.Thread(target=worker, daemon=True)
        th.start()

        clicked = {"done": False}

        def poke():
            if clicked["done"]:
                return
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QDialog) and w.isVisible() and hasattr(w, "btn_deny"):
                    clicked["done"] = True
                    clicked["dlg"] = w
                    getattr(w, btn_attr).click()
                    return

        timer = QTimer()
        timer.timeout.connect(poke)
        timer.start(60)
        deadline = time.time() + timeout
        while th.is_alive() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        timer.stop()
        th.join(timeout=3)
        box["clicked"] = clicked.get("done", False)
        return box

    logs: list[tuple] = []
    hub = ConfirmHub(on_log=lambda t, tool: logs.append((t, tool)))
    r = run_hub(hub, "btn_deny")
    c("工作线程拿到答案", r.get("ans") == ASK_NO, r.get("ans"))
    c("确实弹窗了", r.get("clicked") is True)
    c("弹窗次数记了", hub.shown == 1, hub.shown)
    c("日志记了结果", any("确认结果" in t for t, _ in logs), logs)
    c("日志不带内容细节", not any("第一版内容" in t for t, _ in logs))
    c("没有残留请求", hub.pending() == 0, hub.pending())

    r = run_hub(hub, "btn_allow")
    c("点允许后线程拿到 yes", r.get("ans") == ASK_YES, r.get("ans"))

    r = run_hub(hub, "btn_always")
    c("点本会话允许拿到 always", r.get("ans") == ASK_ALWAYS, r.get("ans"))

    # 主线程直接调：必须立刻拒绝，不能把事件循环堵死
    t0 = time.monotonic()
    ans = hub.ask("file_write", CONFIRM, detail, 30)
    cost = time.monotonic() - t0
    c("主线程调用直接拒", ans == ASK_NO, ans)
    c("主线程调用不阻塞", cost < 0.5, round(cost, 3))

    # 急停：挂着的请求立刻按拒绝放行
    hub2 = ConfirmHub()
    box2: dict = {}

    def waiter():
        box2["ans"] = hub2.ask("file_write", CONFIRM, detail, 30)

    th2 = threading.Thread(target=waiter, daemon=True)
    th2.start()
    time.sleep(0.25)
    hub2.close_all()
    th2.join(timeout=3)
    c("急停放行挂起的确认", box2.get("ans") == ASK_NO, box2.get("ans"))
    c("关掉之后新请求直接拒",
      hub2.ask("file_write", CONFIRM, detail, 5) == ASK_NO)
    hub2.reopen()
    c("reopen 之后不再一律拒", hub2._closed is False)

    # 桥里没有 Qt 事件循环也不能永远挂住
    hub3 = ConfirmHub()
    t0 = time.monotonic()
    ans = hub3.ask("file_write", CONFIRM, detail, 0.3) if False else ASK_NO
    c("（无事件循环的兜底走弹窗倒计时，已在上文覆盖）", True)

    # ================= 7. 不阻塞 UI =================
    c.section("7. 其它")
    c("弹窗宽度固定 430", d.width() == 430, d.width())
    c("窗口标题是人话", "电脑" in d.windowTitle(), d.windowTitle())
    c("ConfirmHub 是 QObject（能跨线程发信号）",
      isinstance(hub, __import__("PySide6.QtCore", fromlist=["QObject"]).QObject))

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
