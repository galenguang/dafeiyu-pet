# -*- coding: utf-8 -*-
"""B3 验收：主循环、账本、卡死检测、上下文压缩、操作日志。

用法：python tools/test_agent_runner.py

四条硬指标（计划里的验收线）：

1. 3 步完成调 finish
2. 垃圾输出纠错后成功
3. 连续 6 次同代码 → stop
4. 预算超限立即停

另外验三个"结构性防线"是否真的挡得住：
确认门挂在工具代理上（不是静态扫函数名）、账本未核验就驳回 finish、
日志只存代码指纹不存内容。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox, FakeLLM  # noqa: E402


def blk(intent: str, code: str) -> str:
    return f"<intent>{intent}</intent>\n<code>\n{code.strip()}\n</code>"


class StopBridge:
    """跑到"执行"就把急停按下去的桥，用来验 stop_event 是否真的打断了执行。"""

    def __init__(self, holder: dict):
        from app.agent.runner import SilentBridge
        self._base = SilentBridge()
        self.holder = holder

    def __getattr__(self, name):
        return getattr(self._base, name)

    def state(self, name, text=""):
        self._base.state(name, text)
        if name == "executing" and self.holder.get("r") is not None:
            self.holder["r"].stop()


def main() -> int:
    c = Checker("B3 主循环与编排")
    from app import config
    from app.agent import prompts
    from app.agent.context import Context
    from app.agent.journal import Journal, _scrub, code_fingerprint
    from app.agent.runner import Runner, SilentBridge, TaskResult
    from app.agent.stall import STOP, StallDetector, code_signature, error_signature
    from app.agent.todo import CHANGED, DONE, TodoLedger

    with DataSandbox() as sb:
        cfg: dict = {"agent_confirm": "always", "agent_allow_dirs": [],
                     "agent_auto_approve": []}
        ws = config.agent_workspace_dir()

        def make(fake, bridge=None, **kw):
            return Runner(lambda: cfg, lambda: fake.llm(), bridge=bridge, **kw)

        # ================= 1. 三步完成 =================
        c.section("1. 三步完成调 finish")
        with FakeLLM([
            blk("看看机器", 'info = sys_info()\nprint(info["cpu_count"])'),
            blk("记账", 'todo_update(id="1", content="查核数", status="done")'),
            blk("收工", 'finish("核数查完了")'),
        ]) as fake:
            br = SilentBridge()
            r = make(fake, br)
            res = r.run("告诉我这台机器几核")
            c("任务成功", res.ok is True, res.reason)
            c("结论传出来了", res.summary == "核数查完了", res.summary)
            c("正好 3 步", res.steps == 3, res.steps)
            c("调了 3 次模型", len(fake.calls) == 3, len(fake.calls))
            c("token 有记账", res.tokens > 0, res.tokens)
            c("耗时记了", res.elapsed > 0, res.elapsed)
            c("结束状态是 done",
              any(s[0] == "done" for s in br.states), [s[0] for s in br.states])
            c("过程里出现过 executing",
              any(s[0] == "executing" for s in br.states))
            c("账本推给了 UI", bool(br.todos) and br.todos[-1][0]["status"] == DONE,
              br.todos[-1] if br.todos else None)
            c("第一步真的执行了（回执里有输出）",
              "输出" in fake.payloads[1]["messages"][-1]["content"],
              fake.payloads[1]["messages"][-1]["content"][:60])
            c("日志写出来了", os.path.exists(config.agent_log_path()),
              config.agent_log_path())
            c("返回里带日志路径", res.journal_path.endswith("agent_log.jsonl"),
              res.journal_path)

        # ================= 2. 垃圾输出纠错 =================
        c.section("2. 垃圾输出纠错后成功")
        with FakeLLM([
            "我觉得应该先看看有什么文件。",
            blk("收工", 'finish("好了")'),
        ]) as fake:
            br = SilentBridge()
            res = make(fake, br).run("随便看看")
            c("纠错后仍然成功", res.ok is True, res.reason)
            c("只花了 1 步", res.steps == 1, res.steps)
            c("一共问了 2 次", len(fake.calls) == 2, len(fake.calls))
            last = fake.payloads[1]["messages"][-1]["content"]
            c("第二次带的是格式纠正", last == prompts.FORMAT_FIX, last[:40])
            c("垃圾输出也留在上下文里",
              any("我觉得应该" in str(m.get("content"))
                  for m in fake.payloads[1]["messages"]))

        # 连续 3 次都不合格式 → 收摊
        with FakeLLM(["瞎说一句", "又瞎说一句", "还是瞎说"]) as fake:
            res = make(fake, SilentBridge()).run("随便看看")
            c("连续 3 次格式错就放弃", res.ok is False and "格式" in res.reason,
              res.reason)
            c("没有无限重试", len(fake.calls) == 3, len(fake.calls))

        # ================= 3. 连续 6 次同代码 =================
        c.section("3. 连续同代码 → stop")
        same = blk("再看看", 'x = 1\nprint(x)')
        with FakeLLM([same] * 12) as fake:
            br = SilentBridge()
            res = make(fake, br).run("一直重复")
            c("停下来了", res.ok is False)
            c("原因是原地打转", "同一段代码" in res.reason, res.reason)
            c("第 6 步停的", res.steps == 6, res.steps)
            c("状态是卡住", any(s[0] == "stuck" for s in br.states),
              [s[0] for s in br.states])

        # 换个注释不算换代码
        with FakeLLM([blk("再看看", f'x = 1  # 第 {i} 次') for i in range(12)]) as fake:
            res = make(fake, SilentBridge()).run("加注释骗检测")
            c("只改注释也算重复", res.ok is False and res.steps == 6, res.steps)

        # 同一个错误出现两次就停
        bad = blk("读文件", 'file_read(r"%s")' % os.path.join(ws, "根本不存在.txt"))
        with FakeLLM([bad] * 12) as fake:
            res = make(fake, SilentBridge()).run("读一个不存在的文件")
            c("同一个错误两次就停", res.ok is False and res.steps == 2, res.steps)
            c("理由是同一个错误", "同一个错误" in res.reason, res.reason)

        # ================= 4. 预算超限 =================
        c.section("4. 预算超限立即停")
        cfg["agent_budget"] = 2000
        huge = blk("干点啥", "# " + "a" * 4000)
        with FakeLLM([huge] * 12) as fake:
            res = make(fake, SilentBridge()).run("烧 token")
            c("预算用完就停", res.ok is False and "预算" in res.reason, res.reason)
            c("没有跑满 12 步", res.steps < 12, res.steps)
            c("token 记在结果里", res.tokens >= 2000, res.tokens)
        cfg.pop("agent_budget", None)

        # ================= 5. 确认门挂在工具代理上 =================
        c.section("5. 确认门（挂在代理上，绕不过）")
        target = os.path.join(ws, "gate_test.txt")
        if os.path.exists(target):
            os.remove(target)
        write_code = blk("写文件", f'file_write(r"{target}", "hello")')

        with FakeLLM([write_code, blk("收工", 'finish("写好了")')]) as fake:
            br = SilentBridge(confirm="no")
            res = make(fake, br).run("写个文件")
            c("拒绝时不落盘", not os.path.exists(target))
            c("确实问过主人",
              any(t == "file_write" and rk == "confirm" for t, rk, _ in br.confirmed),
              br.confirmed)
            c("拒绝理由传回模型",
              any("拒绝" in str(m.get("content"))
                  for m in fake.payloads[1]["messages"]), "ok")
            # 模型被拒之后自己调 finish 收摊，这一步它是诚实的还是硬说"写好了"，
            # runner 管不了——主人刚才亲手点了"拒绝"，他知道发生了什么。
            # 这里只验"失败状态确实推给了桌宠"，让桌宠能演一下翻车。
            c("失败状态推给了桌宠",
              any(s[0] == "failed" for s in br.states), [s[0] for s in br.states])

        with FakeLLM([write_code, blk("收工", 'finish("写好了")')]) as fake:
            br = SilentBridge(confirm="yes")
            res = make(fake, br).run("写个文件")
            c("允许后落盘", os.path.exists(target))
            c("内容是写进去的",
              open(target, encoding="utf-8").read() == "hello")
            c("任务成功", res.ok is True, res.reason)

        # 把工具赋给变量再调用，一样要过门
        alias = blk("绕一下", 'f = file_write\nf(r"%s", "绕过去")'
                    % os.path.join(ws, "alias.txt"))
        with FakeLLM([alias, blk("收工", 'finish("x")')]) as fake:
            br = SilentBridge(confirm="no")
            make(fake, br).run("试试绕门")
            c("赋给变量也绕不过",
              not os.path.exists(os.path.join(ws, "alias.txt")))
            c("代理确实被调到了",
              any(t == "file_write" for t, _, _ in br.confirmed), br.confirmed)

        # top_processes 开了开关就降到"每次问"
        cfg["agent_allow_process_list"] = True
        with FakeLLM([blk("看进程", 'top_processes(1)'),
                      blk("收工", 'finish("x")')]) as fake:
            br = SilentBridge(confirm="no")
            make(fake, br).run("看进程")
            c("开了开关就问而不是一票否决",
              any(t == "top_processes" and rk == "confirm"
                  for t, rk, _ in br.confirmed), br.confirmed)
        cfg["agent_allow_process_list"] = False

        # ================= 6. ask_user 往返 =================
        c.section("6. ask_user 往返")
        with FakeLLM([blk("问一下", 'ask_user("要整理哪个文件夹？")'),
                      blk("收工", 'finish("好")')]) as fake:
            br = SilentBridge(answers=["下载文件夹"])
            res = make(fake, br).run("整理文件夹")
            c("问题问出去了", br.asked == ["要整理哪个文件夹？"], br.asked)
            c("拿到回答后继续", res.ok is True, res.reason)
            c("回答喂回了上下文",
              "主人的回答：下载文件夹" in fake.payloads[1]["messages"][-1]["content"],
              fake.payloads[1]["messages"][-1]["content"][:80])

        with FakeLLM([blk("问一下", 'ask_user("选哪个？")')]) as fake:
            br = SilentBridge(answers=[])
            res = make(fake, br).run("问到底")
            c("主人不回答就收摊", res.ok is False and "没有回答" in res.reason,
              res.reason)

        # ================= 7. 账本未核验就驳回 finish =================
        c.section("7. 未核验不许 finish（只驳一次）")
        with FakeLLM([
            blk("记一笔", 'todo_update(id="1", content="改了配置", '
                         'status="changed_unverified")'),
            blk("收工", 'finish("改好了")'),
            blk("收工", 'finish("改好了")'),
        ]) as fake:
            br = SilentBridge()
            res = make(fake, br).run("改个配置")
            c("第一次 finish 被驳回", res.steps == 3, res.steps)
            c("第二次放行", res.ok is True, res.reason)
            pushed = fake.payloads[2]["messages"][-1]["content"]
            c("驳回话说清了原因", "changed_unverified" in pushed, pushed[:60])
            c("提醒只发一次",
              sum(1 for p in fake.payloads
                  if "先别急着结束" in str(p["messages"][-1].get("content"))) == 1)

        # ================= 8. 急停 =================
        c.section("8. 急停")
        holder: dict = {"r": None}
        with FakeLLM([blk("干活", 'x = 1')] * 5) as fake:
            br = StopBridge(holder)
            r = make(fake, br)
            holder["r"] = r
            res = r.run("干个活")
            c("停下了", res.ok is False and res.stopped is True, res.reason)
            c("理由是叫停", "叫停" in res.reason, res.reason)
            c("只跑了 1 步", res.steps == 1, res.steps)
            c("没有继续调模型", len(fake.calls) == 1, len(fake.calls))

        # ================= 9. 日志不记内容 =================
        c.section("9. 操作日志（只存指纹）")
        marker = "Z" * 400
        long_code = blk("长代码", f'x = 1  # {marker}')
        with FakeLLM([long_code, blk("收工", 'finish("x")')]) as fake:
            make(fake, SilentBridge()).run("跑段长代码")
        raw = open(config.agent_log_path(), encoding="utf-8").read()
        c("日志不存代码全文", marker not in raw)
        c("日志里没有 content 字段原文", '"content": "' not in raw, raw[:80])
        recs = [json.loads(x) for x in raw.splitlines() if x.strip()]
        c("每条都是合法 json", len(recs) > 5, len(recs))
        execs = [x for x in recs if x.get("ev") == "exec"]
        c("exec 记录有代码指纹", execs and isinstance(execs[0].get("code"), dict),
          execs[0].get("code") if execs else None)
        c("指纹带 sha", execs and len(execs[0]["code"].get("sha", "")) == 16)
        c("预览不超过 200 字",
          execs and len(execs[0]["code"].get("preview", "")) <= 200)
        c("有 result 收尾记录", any(x.get("ev") == "result" for x in recs))

        c("_scrub 隐去内容字段",
          _scrub({"content": "秘密", "text": "秘密"})["content"] == "<已隐去>")
        c("_scrub 隐去 api_key",
          _scrub({"api_key": "sk-123"})["api_key"] == "<已隐去>")
        fp = code_fingerprint("a = 1\nb = 2")
        c("指纹行数对", fp["lines"] == 2, fp)

        j = Journal()
        j.write("unit", msg="hello")
        c("tail 能读回来", any(r.get("msg") == "hello" for r in j.tail(5)))
        c("size 有数", j.size() > 0, j.size())

        # ================= 10. 账本 =================
        c.section("10. Todo 账本")
        L = TodoLedger()
        r1 = L.update("", content="列文件")
        c("空 id 自动编号", r1["id"] == "1", r1)
        c("默认 pending", r1["status"] == "pending")
        L.update("1", status=DONE)
        c("能改成 done", L.stats()[DONE] == 1, L.stats())
        L.update("2", content="改配置", status=CHANGED)
        c("changed_unverified 被记下", len(L.unverified()) == 1, L.unverified())
        c("summary 提到待核验", "待核验" in L.summary(), L.summary())
        r3 = L.update("3", status="乱写")
        c("未知状态给出可读错误", "未知状态" in r3.get("error", ""), r3)
        c("未知状态回落成 pending", r3["status"] == "pending", r3)
        c("文本里有图标和内容", "列文件" in L.text(), L.text())
        L.reset()
        c("reset 清空", L.stats()["total"] == 0, L.stats())

        small = TodoLedger(max_items=2)
        small.update("", content="a")
        small.update("", content="b")
        over = small.update("", content="c")
        c("超出上限被拒", "最多" in over.get("error", ""), over)
        c("上限内没被塞进去", small.stats()["total"] == 2)

        # ================= 11. 卡死检测 =================
        c.section("11. 卡死检测")
        c("注释不影响代码指纹",
          code_signature("x = 1  # 再试一次") == code_signature("x = 1  # 换个说法"))
        c("空白不影响代码指纹",
          code_signature("x = 1\n\n\n") == code_signature("x = 1"))
        c("真改了代码指纹就变",
          code_signature("x = 1") != code_signature("x = 2"))
        c("行号不影响错误指纹",
          error_signature("ValueError: 第 3 行 出错了")
          == error_signature("ValueError: 第 9 行 出错了"))
        c("不同错误指纹不同",
          error_signature("ValueError: a") != error_signature("TypeError: a"))

        d = StallDetector()
        for i in range(5):
            v = d.observe("y = 2", True)
        c("第 5 次还没停", v.level != STOP, v.level)
        v = d.observe("y = 2", True)
        c("第 6 次停", v.level == STOP and "6 次" in v.reason, v.reason)

        d2 = StallDetector()
        d2.observe("z = 1", False, "OSError: 文件不存在")
        v = d2.observe("z = 2", False, "OSError: 文件不存在")
        c("同错两次停", v.level == STOP and "同一个错误" in v.reason, v.reason)

        d3 = StallDetector()
        d3.observe_reject("不让 import")
        d3.observe_reject("不让 import")
        v = d3.observe_reject("不让 import")
        c("连拒三次停", v.level == STOP, v.reason)
        d3.observe_accept()
        c("合规一次就清零", d3.observe_reject("x").level != STOP)

        # ================= 12. 上下文压缩 =================
        c.section("12. 上下文压缩")
        ctx = Context("SYS", max_chars=1200, keep_recent=2)
        ctx.add_user("任务：整理文件", digest="任务：整理文件")
        for i in range(6):
            ctx.add_assistant("回复" * 30)
            ctx.add_user("观察" * 100, digest=f"【第{i}步结果】成功 1 块")
        c("压之前超了", ctx.chars() > 1200, ctx.chars())
        saved = ctx.trim()
        c("第 1 层省下字符", saved > 0, saved)
        c("压完在预算内", ctx.chars() <= 1200, ctx.chars())
        c("系统提示没被动", ctx.messages()[0]["content"] == "SYS")
        c("最近两轮原样保留",
          ctx.messages()[-1]["content"].startswith("观察"),
          ctx.messages()[-1]["content"][:10])
        c("旧轮留了摘要",
          any("【第0步结果】" in str(m["content"]) for m in ctx.messages()))

        ctx2 = Context("SYS", max_chars=100000, keep_recent=2)
        for i in range(5):
            ctx2.add_user("观察" * 20, digest="d")
            ctx2.add_assistant("回复" * 20)
        c("压缩前有 10 轮", ctx2.turns() == 10, ctx2.turns())
        ok = ctx2.compact(lambda m: "已经把前四步读完了，文件在 D 盘")
        c("第 2 层压缩成功", ok is True)
        c("压成摘要 + 最近两轮", ctx2.turns() == 3, ctx2.turns())
        c("摘要带标记",
          ctx2.messages()[1]["content"].startswith("【前情摘要】"),
          ctx2.messages()[1]["content"][:20])
        c("已经压过就不再压",
          ctx2.compact(lambda m: "再压一次") is False)

        ctx3 = Context("SYS", max_chars=100000, keep_recent=2)
        ctx3.add_user("a")
        ctx3.add_assistant("b")
        c("轮次太少不压", ctx3.compact(lambda m: "摘要") is False)

        def boom(_m):
            raise RuntimeError("模型挂了")

        ctx4 = Context("SYS", max_chars=100000, keep_recent=2)
        for i in range(4):
            ctx4.add_user("观察" * 20)
            ctx4.add_assistant("回复" * 20)
        before = ctx4.turns()
        c("压缩失败返回 False", ctx4.compact(boom) is False)
        c("失败时不动原文", ctx4.turns() == before, ctx4.turns())

        # ================= 13. 边界 =================
        c.section("13. 边界")
        with FakeLLM(["x"]) as fake:
            res = make(fake, SilentBridge()).run("   ")
            c("空任务直接拒", res.ok is False and "空的" in res.reason, res.reason)
            c("空任务不调模型", len(fake.calls) == 0, len(fake.calls))

        with FakeLLM([blk("干", 'terminate("这个真做不到")')]) as fake:
            res = make(fake, SilentBridge()).run("做不到的事")
            c("terminate 算失败", res.ok is False)
            c("原因传出来了", "做不到" in res.reason, res.reason)

        with FakeLLM([blk("干", 'import os\nos.remove("x")')] * 3) as fake:
            res = make(fake, SilentBridge()).run("试试 import")
            c("import 被拒", res.ok is False)
            c("连拒三次收摊", "不合规" in res.reason, res.reason)
            c("被拒的代码没有执行", len(fake.calls) == 3, len(fake.calls))

        with FakeLLM([(500, "上游炸了")] * 2) as fake:
            res = make(fake, SilentBridge()).run("调用会失败")
            c("模型调用失败有话说",
              res.ok is False and "模型调用失败" in res.reason, res.reason)

        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "app", "agent", "runner.py"),
            encoding="utf-8").read()
        c("主循环不 import Qt", "PySide6" not in src)

        c("TaskResult 能转 dict",
          isinstance(TaskResult(ok=True).as_dict(), dict))

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
