# -*- coding: utf-8 -*-
"""Agent 主循环。

一次任务 = 一次 ``Runner.run()``，跑在 ``agent-runner`` 线程里，**绝不碰 Qt**。
它跟 UI 之间只有一个 ``bridge`` 对象（鸭子类型，不继承），B5 会用
``AgentBridge(QObject)`` 去实现它。

主循环的形状刻意做得很笨——固定步数、固定顺序、没有花活：

    拿回复 → 解析 → 静态校验 → 逐块执行 → 渲染回执 → 判终态 → 查卡死

每一步都可能失败，每种失败都有明确的"往哪走"，没有"再试一次看看"这种分支。
Agent 跑飞的时候能一眼看出卡在哪一环，比省那两步 token 值钱得多。

**三条结构性防线**（不靠提示词劝）：

1. **确认门挂在工具代理上，不是挂在静态分析上。** 模型把 ``file_write`` 赋给
   变量再调用，也一样要过门——静态扫函数名是能被绕过的，代理不能。
2. **账本里还有 ``changed_unverified`` 就不许 finish。** 把"回执 ok ≠ 办成"
   从提示词第 2 条规则变成一次真实的驳回（且只驳一次，不做成陷阱）。
3. **``stop_event`` 同时喂给沙箱检查点、``wait()``、每轮循环开头。** 急停按下去
   最多再走完当前这一块。

**这个模块不 import Qt，也不 import ``app.config`` 之外的东西**——它要能在
没有 QApplication 的进程里被测试。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .. import config
from . import prompts, protocol
from .context import Context
from .gate import ASK_NO, Gate
from .journal import Journal
from .llm import AgentLLM, BudgetExceeded
from .sandbox import Sandbox
from .stall import STOP, StallDetector
from .todo import TodoLedger
from .tools import CONFIRM, DANGER, SAFE, Toolbox

MAX_STEPS = 12
MAX_FORMAT_RETRY = 2          # 格式错最多再要 2 次，第 3 次失败就收摊
MAX_CTX_CHARS = 24000
COMPACT_AT = 0.7              # 预算用掉 70% 触发第 2 层压缩
COMPACT_GAP = 4000            # 两次压缩之间至少再花这么多 token


@dataclass
class TaskResult:
    ok: bool = False
    summary: str = ""
    reason: str = ""
    steps: int = 0
    tokens: int = 0
    stopped: bool = False
    elapsed: float = 0.0
    journal_path: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "summary": self.summary, "reason": self.reason,
                "steps": self.steps, "tokens": self.tokens,
                "stopped": self.stopped, "elapsed": self.elapsed}


# --------------------------------------------------------------------------
# 无 UI 时的桥。默认全拒——没人在场就不该动主人的电脑。
# --------------------------------------------------------------------------
class SilentBridge:
    """同时兼作测试替身：所有调用都记下来，答案可预设。"""

    def __init__(self, confirm: str = ASK_NO, answers: list[str] | None = None):
        self.confirm_answer = confirm
        self.answers = list(answers or [])
        self.states: list[tuple[str, str]] = []
        self.todos: list[list[dict]] = []
        self.said: list[str] = []
        self.asked: list[str] = []
        self.confirmed: list[tuple[str, str, str]] = []
        self.logs: list[str] = []

    def state(self, name: str, text: str = "") -> None:
        self.states.append((name, text))

    def say(self, text: str) -> None:
        self.said.append(text)

    def todo(self, items: list[dict]) -> None:
        self.todos.append(list(items))

    def confirm(self, tool: str, risk: str, detail: str, timeout: float) -> str:
        self.confirmed.append((tool, risk, detail))
        return self.confirm_answer

    def ask(self, question: str, timeout: float):
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else None

    def log(self, text: str) -> None:
        self.logs.append(text)


# --------------------------------------------------------------------------
def _describe_call(name: str, args: tuple, kwargs: dict) -> str:
    """确认面板上给人看的一句话。路径要原样露出来，那是主人判断的依据。"""
    bits: list[str] = []
    for a in list(args)[:3]:
        s = a if isinstance(a, str) else repr(a)
        bits.append(s if len(s) <= 60 else s[:57] + "…")
    for k, v in list(kwargs.items())[:3]:
        s = v if isinstance(v, str) else repr(v)
        bits.append(f"{k}={s if len(s) <= 40 else s[:37] + '…'}")
    return f"{name}({', '.join(bits)})"[:200]


class Runner:
    """``get_llm`` 返回 ``{"base_url","api_key","model","timeout"}``，传函数不传字典。"""

    def __init__(self, get_cfg, get_llm, *, bridge=None, stop_event=None,
                 memory_ctx=None, journal_path: str | None = None,
                 max_steps: int = MAX_STEPS, budget: int = 40000,
                 block_timeout: float = 45.0, confirm_timeout: float = 30.0,
                 ask_timeout: float = 120.0, max_output_tokens: int = 1800,
                 max_ctx_chars: int = MAX_CTX_CHARS):
        self._get_cfg = get_cfg or (lambda: {})
        self._get_llm = get_llm or (lambda: {})
        self.bridge = bridge if bridge is not None else SilentBridge()
        self.stop_event = stop_event or threading.Event()
        self.memory_ctx = memory_ctx
        self.journal_path = journal_path
        self.max_steps = max(1, int(max_steps))
        self.budget = max(2000, int(budget))
        self.block_timeout = float(block_timeout)
        self.confirm_timeout = float(confirm_timeout)
        self.ask_timeout = float(ask_timeout)
        self.max_output_tokens = int(max_output_tokens)
        self.max_ctx_chars = int(max_ctx_chars)

        self.gate = Gate(self._get_cfg, self._ask)
        self.stall = StallDetector()
        self.llm = AgentLLM(self._get_llm, budget=self.budget,
                            max_tokens=self.max_output_tokens)
        self.running = False
        self.task = ""

        self._journal: Journal | None = None
        self._calls: list[str] = []
        self._pushed = False
        self._compacted_at = 0
        self._box: Toolbox | None = None
        self._ledger: TodoLedger | None = None
        self._ctx: Context | None = None

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    def cfg(self) -> dict:
        try:
            return self._get_cfg() or {}
        except Exception:
            return {}

    def _j(self, event: str, **fields) -> None:
        if self._journal is not None:
            self._journal.write(event, **fields)

    def _emit(self, name: str, *args) -> None:
        """调 bridge 的方法。桥坏了不能把任务带崩。"""
        fn = getattr(self.bridge, name, None)
        if fn is None:
            return
        try:
            fn(*args)
        except Exception as exc:
            self._j("bridge_error", fn=name, error=str(exc)[:160])

    def stop(self) -> None:
        """急停。可从任意线程调。"""
        self.stop_event.set()

    # ------------------------------------------------------------------
    # 确认门
    # ------------------------------------------------------------------
    def _ask(self, tool: str, risk: str, detail: str) -> str:
        fn = getattr(self.bridge, "confirm", None)
        if fn is None:
            return ASK_NO
        try:
            ans = fn(tool, risk, detail, self.confirm_timeout)
        except Exception as exc:
            self._j("confirm_error", tool=tool, error=str(exc)[:200])
            return ASK_NO
        ans = ans or ASK_NO
        self._j("confirm", tool=tool, risk=risk, detail=detail, answer=ans)
        return ans

    def _risk_of(self, name: str, box: Toolbox) -> str:
        risk = box.risk_of(name)
        # top_processes 在工具层是 DANGER（默认一票否决）。主人显式打开了开关，
        # 说明他知情，就降到"每次问"——不然开了开关也用不了，等于骗人。
        if name == "top_processes" and self.cfg().get("agent_allow_process_list"):
            return CONFIRM
        return risk

    def _wrap(self, name: str, fn, box: Toolbox):
        """工具代理。**确认门就挂在这里**，不挂静态分析。"""
        risk = self._risk_of(name, box)

        def gated(*args, **kwargs):
            if risk != SAFE:
                detail = _describe_call(name, args, kwargs)
                if not self.gate.check(name, risk, detail):
                    raise PermissionError(
                        self.gate.last_reason or f"{name} 被拒绝")
            self._calls.append(name)
            return fn(*args, **kwargs)

        gated.__name__ = name
        gated.__doc__ = getattr(fn, "__doc__", None)
        return gated

    def _build_tools(self, box: Toolbox, ledger: TodoLedger) -> dict:
        out: dict = {"todo_update": ledger.update, "todo_list": ledger.items}
        for name, fn in box.functions().items():
            out[name] = self._wrap(name, fn, box)
        return out

    # ------------------------------------------------------------------
    # 提示词环境块
    # ------------------------------------------------------------------
    def _env_text(self, box: Toolbox, memory: str) -> str:
        rows: list[str] = []
        try:
            info = box.sys_info()
        except Exception:
            info = {}
        ws = info.get("workspace") or config.agent_workspace_dir()
        rows.append(f"工作区（文件操作的根）：{ws}")
        if info.get("os"):
            rows.append(f"系统：{info['os']}")
        if info.get("cpu_count"):
            rows.append(f"CPU：{info['cpu_count']} 核")
        if info.get("ram_total_gb"):
            rows.append(f"内存：{info['ram_total_gb']} GB")
        if info.get("disk_free_gb"):
            rows.append(f"磁盘剩余：{info['disk_free_gb']} GB")
        if info.get("screen"):
            rows.append(f"屏幕：{info['screen']}")
        rows.append("今天：" + time.strftime("%Y-%m-%d %H:%M"))
        if memory:
            rows.append("关于主人：" + " ".join(memory.split())[:300])
        return "\n".join(rows)

    # ------------------------------------------------------------------
    # 回执摘要（给第 1 层压缩用）
    # ------------------------------------------------------------------
    @staticmethod
    def _digest(step: int, results: list) -> str:
        okn = sum(1 for r in results if r.ok)
        bad = len(results) - okn
        s = f"【第 {step} 步结果】成功 {okn} 块"
        if bad:
            s += f"，失败 {bad} 块"
        intents = "；".join((r.intent or "").strip()[:16]
                           for r in results if (r.intent or "").strip())
        if intents:
            s += f"：{intents[:60]}"
        return s

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------
    def run(self, task: str) -> TaskResult:
        self.task = (task or "").strip()
        if not self.task:
            return TaskResult(ok=False, reason="任务描述是空的")
        if self.running:
            return TaskResult(ok=False, reason="上一个任务还在跑")
        self.running = True
        try:
            return self._run()
        except Exception as exc:            # 兜底：主循环自己崩了也要有话说
            self._j("crash", error=repr(exc)[:300])
            self._emit("state", "failed", "我这边出岔子了")
            return TaskResult(ok=False, reason=f"内部错误：{exc}",
                              tokens=self.llm.used)
        finally:
            self.running = False
            self._journal = None

    # ------------------------------------------------------------------
    def _run(self) -> TaskResult:
        started = time.monotonic()
        cfg = self.cfg()
        # **这里故意不清 stop_event。** 清了的话，"刚点开始就按急停"会把急停
        # 抹掉——而人正是会在觉得不对劲的第一秒按下去。清场的责任在调用方
        # （开新任务前自己 clear），Runner 只负责"看到停了就停"。
        self.llm.reset()
        self.llm.budget = int(cfg.get("agent_budget") or self.budget)
        self.gate.reset()
        self.stall.reset()
        self._calls = []
        self._pushed = False
        self._compacted_at = 0

        self._journal = Journal(self.journal_path)
        self._journal.write("start", task=self.task[:200],
                            steps=self.max_steps, budget=self.llm.budget)

        ledger = TodoLedger()
        box = Toolbox(self._get_cfg, stop_event=self.stop_event,
                      on_notify=lambda t: self._emit("say", t))
        self._box, self._ledger = box, ledger

        sandbox = Sandbox(tools=self._build_tools(box, ledger),
                          timeout=self.block_timeout,
                          stop_event=self.stop_event)

        memory = ""
        if self.memory_ctx is not None:
            try:
                memory = (self.memory_ctx() or "").strip()
            except Exception as exc:
                self._j("memory_error", error=str(exc)[:160])

        system = prompts.build_system(
            box.specs_text() + "\n" + prompts.TODO_SPECS,
            self._env_text(box, memory))
        ctx = Context(system, max_chars=self.max_ctx_chars)
        self._ctx = ctx
        ctx.add_user(prompts.TASK_HEAD.format(task=self.task),
                     digest=f"任务：{self.task[:60]}")

        self._emit("state", "planning", "收到，让我看看怎么搞")
        self._j("plan", tools=len(box.functions()) + 2, chars=ctx.chars())

        step = 0
        last_rev = ledger.revision

        while step < self.max_steps:
            step += 1

            if self.stop_event.is_set():
                return self._fail("主人叫停了", steps=step - 1, started=started,
                                  stopped=True, state="stopped", say="停了停了")

            # ---- 预算：70% 以上压一次上下文（花钱的那一层）----
            if (self.llm.ratio >= COMPACT_AT
                    and self.llm.used - self._compacted_at >= COMPACT_GAP):
                ok = ctx.compact(
                    lambda m: self.llm.complete(m, max_tokens=500))
                self._compacted_at = self.llm.used
                self._j("compact", ok=ok, **ctx.state())

            ctx.trim()

            # ---- 拿回复 ----
            self._emit("state", "planning")
            try:
                reply = self.llm.complete(ctx.messages())
            except BudgetExceeded as exc:
                return self._fail(str(exc), steps=step, started=started,
                                  state="stuck", say="我的脑力用完了")
            except Exception as exc:
                return self._fail(f"模型调用失败：{exc}", steps=step,
                                  started=started, state="failed",
                                  say="连不上大脑了")

            # ---- 解析（格式错重试，最多 2 次）----
            blocks = None
            for attempt in range(MAX_FORMAT_RETRY + 1):
                try:
                    blocks = protocol.parse(reply)
                    break
                except protocol.FormatError as exc:
                    self._j("format_error", step=step, attempt=attempt + 1,
                            reason=str(exc))
                    if attempt >= MAX_FORMAT_RETRY:
                        return self._fail(
                            f"模型连续 {MAX_FORMAT_RETRY + 1} 次没按格式输出：{exc}",
                            steps=step, started=started, state="stuck",
                            say="我说话都说不利索了")
                    self._emit("log", "格式不对，让她重说一次")
                    ctx.add_assistant(reply)
                    ctx.add_user(prompts.FORMAT_FIX)
                    try:
                        reply = self.llm.complete(ctx.messages())
                    except BudgetExceeded as exc2:
                        return self._fail(str(exc2), steps=step,
                                          started=started, state="stuck")
                    except Exception as exc2:
                        return self._fail(f"模型调用失败：{exc2}", steps=step,
                                          started=started, state="failed")

            # ---- 静态校验（整批，任一块不过就整批不跑）----
            try:
                for b in blocks:
                    protocol.validate(b.code)
            except protocol.CodeRejected as exc:
                verdict = self.stall.observe_reject(str(exc))
                self._j("reject", step=step, reason=str(exc))
                self._emit("log", f"代码被拒：{str(exc)[:60]}")
                ctx.add_assistant(reply)
                ctx.add_user(prompts.render_reject(str(exc)))
                if verdict.stop:
                    return self._fail(verdict.reason, steps=step,
                                      started=started, state="stuck",
                                      say="这条路走不通")
                self._emit("state", "planning", "这样写不行，我换个法子")
                continue
            self.stall.observe_accept()

            # ---- 执行 ----
            self._calls = []
            intents = "；".join(b.intent for b in blocks if b.intent)[:60]
            self._emit("state", "executing", intents)
            joined = "\n".join(b.code for b in blocks)
            self._j("exec", step=step, blocks=len(blocks), code=joined,
                    intents=intents)
            results = sandbox.exec_blocks(blocks)

            if any(getattr(r, "stopped", False) for r in results):
                return self._fail("主人叫停了", steps=step, started=started,
                                  stopped=True, state="stopped", say="停了停了")

            for i, r in enumerate(results, 1):
                self._j("block", step=step, idx=i, ok=r.ok, intent=r.intent,
                        elapsed=round(r.elapsed, 2), error=r.error,
                        out_len=len(r.stdout or ""), tools=self._calls)
            okn = sum(1 for r in results if r.ok)
            self._emit("log", f"第 {step} 步：{okn}/{len(results)} 块成功"
                       + (f" · {intents[:30]}" if intents else ""))

            ctx.add_assistant(reply)
            ctx.add_user(prompts.render_observation(
                step, [r.as_observation() for r in results]),
                digest=self._digest(step, results))

            # ---- 账本变了就推 UI ----
            if ledger.revision != last_rev:
                last_rev = ledger.revision
                self._emit("todo", ledger.items())

            # ---- 终态判定 ----
            if box.finished:
                unver = ledger.unverified()
                if unver and not self._pushed:
                    self._pushed = True
                    names = "；".join(f"{u['id']}.{u['content']}"
                                     for u in unver[:4])
                    ctx.add_user(prompts.FINISH_PUSH.format(
                        n=len(unver), items=names))
                    self._j("finish_push", step=step, n=len(unver))
                    self._emit("log", f"还有 {len(unver)} 项改了没核验")
                    self._emit("state", "planning", "等一下，我得先核验")
                    continue
                return self._ok(box.finished, steps=step, started=started)

            if box.terminated:
                return self._fail(box.terminated, steps=step, started=started,
                                  state="stuck", say="这个我做不了")

            if box.pending_question:
                q = box.pending_question
                box.pending_question = None
                self._j("ask_user", step=step, question=q)
                self._emit("state", "confirming", "这一步得你回答一下")
                ans = self._ask_user(q)
                if ans is None:
                    return self._fail("主人没有回答，任务停下了", steps=step,
                                      started=started, state="stuck",
                                      say="那我先等着")
                ctx.add_user(prompts.ASK_ANSWER.format(answer=ans),
                             digest=f"主人答：{ans[:50]}")
                continue

            # ---- 卡死检测 ----
            last = results[-1] if results else None
            verdict = self.stall.observe(
                joined, bool(last and last.ok), (last.error if last else ""))
            if verdict.stop:
                self._j("stall", step=step, reason=verdict.reason,
                        **self.stall.state())
                return self._fail(verdict.reason, steps=step, started=started,
                                  state="stuck", say="我卡住了")
            if verdict.level != "ok":
                self._j("stall_warn", step=step, reason=verdict.reason)

            if not results or not all(r.ok for r in results):
                self._emit("state", "failed", "翻车了，我换个法子")

        return self._fail(f"跑了 {self.max_steps} 步还没完成", steps=step,
                          started=started, state="stuck", say="步数用完了")

    # ------------------------------------------------------------------
    def _ask_user(self, question: str):
        fn = getattr(self.bridge, "ask", None)
        if fn is None:
            return None
        try:
            ans = fn(question, self.ask_timeout)
        except Exception as exc:
            self._j("ask_error", error=str(exc)[:200])
            return None
        if ans is None:
            return None
        ans = " ".join(str(ans).split()).strip()
        return ans[:400] or None

    def _ok(self, summary: str, *, steps: int, started: float) -> TaskResult:
        res = TaskResult(ok=True, summary=summary or "完成了", steps=steps)
        self._finalize(res, started)
        self._emit("log", f"完成：{res.summary[:60]}")
        self._emit("state", "done", res.summary)
        return res

    def _fail(self, reason: str, *, steps: int, started: float,
              stopped: bool = False, state: str = "failed",
              say: str = "") -> TaskResult:
        res = TaskResult(ok=False, reason=reason or "失败了",
                         steps=steps, stopped=stopped)
        self._finalize(res, started)
        self._emit("log", f"停下：{(reason or '')[:60]}")
        self._emit("state", state, say or (reason or "")[:20])
        return res

    def _finalize(self, res: TaskResult, started: float) -> None:
        res.tokens = self.llm.used
        res.elapsed = round(time.monotonic() - started, 2)
        res.journal_path = getattr(self._journal, "path", "") or ""
        self._j("result", ok=res.ok, steps=res.steps, tokens=res.tokens,
                stopped=res.stopped, elapsed=res.elapsed,
                reason=res.reason, summary=res.summary,
                stall=self.stall.state())
