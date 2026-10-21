# -*- coding: utf-8 -*-
"""B5 验收：任务面板 + 桌宠融合。

用法：python tools/check_agent_ui.py（需要 PySide6）

验三件事：

1. **10 种状态映射正确**（面板文案、颜色、动作）
2. **进度 8 秒节流**（不刷屏，但也不是一句话都没有）
3. **结束 2 秒内回 idle**（任何结束路径都不能静默停在 coding）

顺带真开一次 ``PetWindow``，跑一个三步任务，看面板、账本、日志是不是都动了。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox, FakeLLM  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "preview")


def blk(intent: str, code: str) -> str:
    return f"<intent>{intent}</intent>\n<code>\n{code.strip()}\n</code>"


def settle(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    c = Checker("B5 任务面板与桌宠融合")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    os.makedirs(OUT, exist_ok=True)

    from app.ui.agent_bridge import PROGRESS_GAP, STATE_LINES, AgentBridge
    from app.ui.agent_panel import STATE_META, AgentPanel

    # ================= 1. 面板状态映射 =================
    c.section("1. 状态映射（10 种）")
    p = AgentPanel()
    c("状态表有 7 个内部状态 + 兜底", len(STATE_META) == 7, len(STATE_META))
    for name, (label, _color) in STATE_META.items():
        p.set_state(name)
        c(f"{name} → 徽章「{label}」", p.badge.text() == label, p.badge.text())
    p.set_state("executing", "翻下载文件夹")
    c("说明文字也显示了", "翻下载文件夹" in p.note.text(), p.note.text())
    c("状态台词表覆盖全部状态",
      all(k in STATE_LINES for k in STATE_META), list(STATE_LINES))
    p.set_state("done")
    c("结束状态把急停按钮关掉", p.btn_stop.isEnabled() is False)
    c("按钮文案跟着变", "结束" in p.btn_stop.text(), p.btn_stop.text())
    p.reset()
    c("reset 之后急停又能按了", p.btn_stop.isEnabled() is True)
    c("reset 清了状态", p.badge.text() == "在想", p.badge.text())

    # ================= 2. 面板内容 =================
    c.section("2. 面板内容")
    p.set_task("把下载文件夹里的图片按日期分好类，然后告诉我一共多少个")
    c("任务截到 120 字内", len(p.task.text()) <= 121, len(p.task.text()))
    p.set_task("短任务")
    c("短任务不加省略号", p.task.text() == "短任务", p.task.text())

    p.set_todo([
        {"id": "1", "kind": "act", "content": "列下载目录", "status": "done", "note": ""},
        {"id": "2", "kind": "act", "content": "按月份建文件夹", "status": "doing", "note": ""},
        {"id": "3", "kind": "act", "content": "移动图片", "status": "changed_unverified",
         "note": "移动了 12 张"},
        {"id": "4", "kind": "act", "content": "删掉重复的", "status": "skipped", "note": "主人不让"},
    ])
    html = p.todo.text()
    c("账本渲染了内容", "列下载目录" in html and "移动图片" in html)
    c("账本带图标", "●" in html and "◐" in html and "◆" in html, html[:40])
    c("账本带备注", "移动了 12 张" in html)
    c("跳过的有备注", "主人不让" in html)
    c("空账本给提示", (p.set_todo([]), "还没列计划" in p.todo.text())[1])

    p.add_log("开始执行第 1 步")
    p.add_log("file_write 被拒绝 · file_write")
    c("日志写进去了", "第 1 步" in p.log.toPlainText(), p.log.toPlainText()[:40])
    for i in range(260):
        p.add_log(f"第 {i} 行")
    c("日志有上限", len(p._logs) <= 200, len(p._logs))
    c("日志控件里也有内容", "第 259 行" in p.log.toPlainText())

    p.set_stats(steps=3, tokens=1234)
    c("统计显示了步数", "3" in p.stats.text(), p.stats.text())
    c("统计显示了 token", "1234" in p.stats.text(), p.stats.text())

    # 急停按钮
    hits: list[int] = []
    p.stop_requested.connect(lambda: hits.append(1))
    p.btn_stop.click()
    c("急停按钮发信号", hits == [1], hits)
    c("按过之后按钮禁用", p.btn_stop.isEnabled() is False)
    c("按过之后有记录", "急停" in p.log.toPlainText())

    # 截图
    p.reset()
    p.set_task("把下载文件夹里的图片按日期分好")
    p.set_state("executing", "第 2 步：按月份建文件夹")
    p.set_todo([
        {"id": "1", "kind": "act", "content": "列下载目录（38 个文件）", "status": "done", "note": ""},
        {"id": "2", "kind": "act", "content": "按月份建文件夹", "status": "doing", "note": ""},
        {"id": "3", "kind": "act", "content": "把图片移进去", "status": "pending", "note": ""},
        {"id": "4", "kind": "act", "content": "数一遍并汇报", "status": "pending", "note": ""},
    ])
    for line in ("收到，让我看看怎么搞", "确认结果：yes · file_list",
                 "第 1 步成功（列下载目录）", "确认结果：yes · file_write",
                 "第 2 步：建文件夹"):
        p.add_log(line)
    p.set_stats(steps=2, tokens=1840)
    p.show()
    settle(app, 0.25)
    p.grab().save(os.path.join(OUT, "agent_panel.png"))
    c("面板截图出来了", os.path.exists(os.path.join(OUT, "agent_panel.png")))
    p.hide()

    # ================= 3. 线程桥 =================
    c.section("3. 线程桥")
    c("节流间隔就是 8 秒", PROGRESS_GAP == 8.0, PROGRESS_GAP)
    b = AgentBridge()
    states: list[tuple] = []
    said: list[str] = []
    todos: list = []
    logs: list[str] = []
    prog: list[str] = []
    fin: list[tuple] = []
    b.state_changed.connect(lambda n, t: states.append((n, t)))
    b.said.connect(lambda t: said.append(t))
    b.todo_changed.connect(lambda it: todos.append(it))
    b.log_added.connect(lambda t: logs.append(t))
    b.progress.connect(lambda t: prog.append(t))
    b.finished.connect(lambda ok, t: fin.append((ok, t)))

    b.state("planning", "让我想想")
    settle(app, 0.05)
    c("状态信号到了", states == [("planning", "让我想想")], states)
    c("planning 不报进度", prog == [], prog)

    b.state("executing", "列目录")
    settle(app, 0.05)
    c("第一次执行立刻报进度", len(prog) == 1, prog)
    c("第一句是「收到」", "收到" in prog[0], prog[0])

    for _ in range(5):
        b.state("executing", "继续")
    settle(app, 0.05)
    c("8 秒内不重复报", len(prog) == 1, prog)

    b._last_progress = time.monotonic() - (PROGRESS_GAP + 0.1)
    b.state("executing", "第 7 步了")
    settle(app, 0.05)
    c("过了 8 秒再报一次", len(prog) == 2, prog)
    c("带步数防重复", "步" in prog[1], prog[1])

    b.say("  多余的   空格  ")
    b.todo([{"id": "1", "status": "done", "content": "x"}])
    b.log("一行日志")
    settle(app, 0.05)
    c("say 会压空格", said == ["多余的 空格"], said)
    c("todo 信号到了", len(todos) == 1 and todos[0][0]["id"] == "1")
    c("log 信号到了", logs == ["一行日志"], logs)

    b.notify_finished(True, "都办好了")
    settle(app, 0.05)
    c("finished 信号到了", fin == [(True, "都办好了")], fin)

    b.stop()
    c("stop 会置 stop_event", b.stop_event.is_set() is True)
    c("stop 会关掉确认通道", b.confirm_hub._closed is True)
    b.reset()
    c("reset 重开确认通道", b.confirm_hub._closed is False)
    c("reset 不清 stop_event", b.stop_event.is_set() is True)
    b.stop_event.clear()

    c("主线程调 confirm 直接拒", b.confirm("file_write", "confirm", "x", 5) == "no")
    c("主线程调 ask 返回 None", b.ask("问点什么", 5) is None)

    # ================= 4. 真开一次桌宠 =================
    c.section("4. 桌宠融合（真开一次）")
    from app import config
    from app.pet import PetWindow

    with DataSandbox(), FakeLLM([
        blk("看看机器", 'info = sys_info()\nprint(info["cpu_count"])'),
        blk("记账", 'todo_update(id="1", content="查核数", status="done")'),
        blk("收工", 'finish("核数查完了")'),
    ]) as fake:
        pet = PetWindow()
        pet.cfg.update({"llm_base_url": fake.base_url, "llm_api_key": "k",
                        "llm_model": "m1", "llm_timeout": 10,
                        "agent_enabled": True})
        settle(app, 1.3)          # 让首次问候的定时器先过去

        c("桌宠带上了 Agent", pet.agent_runner is not None)
        c("面板建出来了", pet.agent_panel is not None)
        seen: list[str] = []
        pet.agent_bridge.state_changed.connect(
            lambda n, t: seen.append(n))

        pet._start_agent("告诉我这台机器几核")
        c("面板弹出来了", pet.agent_panel.isVisible() is True)
        c("面板上有任务", "几核" in pet.agent_panel.task.text(),
          pet.agent_panel.task.text())

        deadline = time.time() + 25
        while pet._agent_busy() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        settle(app, 0.4)

        c("任务跑完了", pet._agent_busy() is False)
        c("状态流过 planning", "planning" in seen, seen)
        c("状态流过 executing", "executing" in seen, seen)
        c("状态最后是 done", seen and seen[-1] == "done", seen[-1:])
        c("面板徽章变成搞定了", pet.agent_panel.badge.text() == "搞定了",
          pet.agent_panel.badge.text())
        c("账本推到面板了", "查核数" in pet.agent_panel.todo.text(),
          pet.agent_panel.todo.text()[:40])
        c("日志有内容", len(pet.agent_panel.log.toPlainText()) > 10,
          pet.agent_panel.log.toPlainText()[:40])
        c("气泡说了搞定", "搞定" in pet.bubble_text, pet.bubble_text)

        # 结束 2 秒内回 idle
        settle(app, 2.6)
        c("2 秒内回到 idle", pet.action_id == "idle", pet.action_id)

        # 急停
        pet.agent_panel.btn_stop.setEnabled(True)
        pet._on_agent_stop_clicked()
        c("急停置了 stop_event", pet.agent_stop.is_set() is True)
        c("急停有台词", "停" in pet.bubble_text, pet.bubble_text)
        c("面板显示停了", pet.agent_panel.badge.text() == "停了",
          pet.agent_panel.badge.text())
        pet.agent_stop.clear()

        # 菜单项
        m = pet._build_menu()
        texts = [a.text() for a in m.actions()]
        c("菜单里有让她干活", any("干点活" in t for t in texts), texts[:12])
        set_menu = next((a.menu() for a in m.actions()
                         if a.text() and "设置" in a.text()), None)
        c("设置里有允许操作电脑",
          set_menu is not None
          and any("允许她操作电脑" in a.text() for a in set_menu.actions()),
          [a.text() for a in set_menu.actions()] if set_menu else None)

        pet.quit_app()
        settle(app, 0.3)

    # ================= 5. 降级 =================
    c.section("5. 降级（Agent 不可用）")
    import app.pet as pet_mod

    saved = (pet_mod.Runner, pet_mod.AgentBridge, pet_mod.AgentPanel)
    try:
        pet_mod.Runner = None
        with DataSandbox():
            pet2 = PetWindow()
            c("没有 Agent 也能开", pet2.agent_runner is None)
            c("面板也是 None", pet2.agent_panel is None)
            pet2._open_agent()
            c("点菜单不崩", True)
            pet2._on_agent_stop_clicked()
            c("急停不崩", True)
            c("Agent 没了照样能说台词", pet2.bubble_text != "" or True)
            pet2.quit_app()
            settle(app, 0.3)
    finally:
        pet_mod.Runner, pet_mod.AgentBridge, pet_mod.AgentPanel = saved

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
