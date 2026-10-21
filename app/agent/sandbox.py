# -*- coding: utf-8 -*-
"""受限执行环境。

**不叫沙箱。** CPython 里没有安全沙箱，这是业界共识——任何声称"我们做了
Python 沙箱"的项目，要么在骗人，要么在骗自己。所以这里的产品文案统一叫
"受限执行环境"，README 里也把防不住的东西原样写出来。

**威胁模型**：防"模型手滑"，不防"恶意逃逸"。在这个场景下（本地桌宠 + 用户
自己的机器 + 用户自己配的模型 + **默认关闭** + 每次写操作都要确认），
in-process 执行 + AST 静态校验 + 工具白名单是可接受的等级。

**为什么不用子进程**：``multiprocessing`` 在 PyInstaller 下是经典坑——Windows
用 spawn，冻结后子进程会**重新执行整个 exe**，必须 ``freeze_support()`` 且必须
是 ``main.py`` 的第一条语句（现在顶部就 import PySide6，子进程白付 300ms 还可能
触发 Qt 初始化）。``PyThreadState_SetAsyncExc`` 会破坏解释器状态，
``sys.settrace`` 有 5–20 倍性能损失且对 C 调用无效。

**防不住什么（README 必须原样写）**：

1. 挡不住专门构造的逃逸链（白名单 builtins 仍有组合空间）
2. 挡不住 C 层阻塞（COM 卡死只能重启桌宠）
3. 挡不住白名单工具造成的真实破坏（覆盖的是真文件）
4. 挡不住数据外流（工具返回值会发往云端模型）
5. 挡不住内存炸弹（``[0] * 10**9`` 会直接把进程撑死）
6. 挡不住提示注入（只能靠 prompt 约束 + 确认门降低概率）
"""
from __future__ import annotations

import ast
import threading
import time
import traceback
from dataclasses import dataclass, field

from . import protocol

MAX_OUTPUT = 16 * 1024          # 单块输出硬顶
MAX_TAIL = 4 * 1024             # 其中尾部保留这么多（错误信息通常在最后）


class _Stop(BaseException):
    """超时/急停信号。

    继承 ``BaseException`` 而不是 ``Exception``，这样用户代码里的
    ``except Exception:`` 拦不住它——否则一句 ``while True: try: pass
    except Exception: pass`` 就能把检查点吃掉。
    """


@dataclass
class BlockResult:
    ok: bool = True
    intent: str = ""
    stdout: str = ""
    error: str = ""
    elapsed: float = 0.0
    stopped: bool = False
    truncated: bool = False

    def as_observation(self) -> dict:
        return {"ok": self.ok, "intent": self.intent,
                "stdout": self.stdout, "error": self.error}


# --------------------------------------------------------------------------
# 输出收集
# --------------------------------------------------------------------------
class _Collector:
    def __init__(self, limit: int = MAX_OUTPUT):
        self.limit = limit
        self.head_limit = max(1024, limit - MAX_TAIL)
        self.head: list[str] = []
        self.tail: list[str] = []
        self.head_size = 0
        self.tail_size = 0
        self.dropped = 0

    def write(self, s: str) -> None:
        if not s:
            return
        room = self.head_limit - self.head_size
        if room > 0:
            chunk = s[:room]
            self.head.append(chunk)
            self.head_size += len(chunk)
            s = s[room:]
        if not s:
            return
        # 超了就往尾部环形缓冲里塞，保留最后 4KB
        self.tail.append(s)
        self.tail_size += len(s)
        while self.tail_size > MAX_TAIL and self.tail:
            self.dropped += len(self.tail[0])
            self.tail_size -= len(self.tail[0])
            self.tail.pop(0)

    def text(self) -> tuple[str, bool]:
        body = "".join(self.head)
        if self.tail:
            body += f"\n…（中间省略 {self.dropped} 字符）…\n" + "".join(self.tail)
            return body, True
        return body, False


# --------------------------------------------------------------------------
# AST 注入：每个语句前插一个 __tick__()
# --------------------------------------------------------------------------
class _Instrument(ast.NodeTransformer):
    """在每一层语句块前插检查点。

    必须**钻进** while / for / if 的 body，否则 ``while True: pass`` 永远
    等不到下一次检查。
    """

    @staticmethod
    def _tick_expr() -> ast.Expr:
        return ast.Expr(value=ast.Call(
            func=ast.Name(id="__tick__", ctx=ast.Load()), args=[], keywords=[]))

    def generic_visit(self, node):
        super().generic_visit(node)
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            new: list[ast.stmt] = []
            for stmt in body:
                new.append(self._tick_expr())
                new.append(stmt)
            node.body = new
        return node


# --------------------------------------------------------------------------
# 受限执行环境
# --------------------------------------------------------------------------
def _safe_builtins(printer) -> dict:
    """白名单 builtins。

    **不给**：``__import__`` / ``open`` / ``eval`` / ``exec`` / ``compile`` /
    ``getattr`` / ``setattr`` / ``delattr`` / ``vars`` / ``globals`` /
    ``locals`` / ``type`` / ``object`` / ``super`` / ``input`` / ``breakpoint``。
    没有 ``object`` 和 ``type``，``().__class__`` 那条经典逃逸链就断在起点。
    """
    names = [
        "abs", "all", "any", "bool", "bytes", "chr", "dict", "divmod",
        "enumerate", "filter", "float", "format", "frozenset", "hash", "hex",
        "int", "isinstance", "issubclass", "iter", "len", "list", "map",
        "max", "min", "next", "oct", "ord", "pow", "range", "repr",
        "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple",
        "zip",
        # 异常类型：不给的话用户代码连 except 都写不了
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "AttributeError", "RuntimeError", "ZeroDivisionError", "OSError",
        "TimeoutError", "StopIteration", "ArithmeticError", "LookupError",
        "AssertionError", "NotImplementedError", "UnicodeDecodeError",
        "PermissionError", "FileNotFoundError",
        "True", "False", "None",
    ]
    out = {}
    import builtins as _b
    for n in names:
        if hasattr(_b, n):
            out[n] = getattr(_b, n)
    out["print"] = printer
    return out


class Sandbox:
    """一个任务一个实例。块与块之间共享 ``self.ns``。"""

    def __init__(self, tools: dict | None = None, *,
                 timeout: float = 45.0,
                 stop_event: threading.Event | None = None,
                 max_output: int = MAX_OUTPUT):
        self.timeout = max(1.0, float(timeout))
        self.stop_event = stop_event or threading.Event()
        self.max_output = max(1024, int(max_output))
        self._tools = dict(tools or {})
        self.ns: dict = {}
        self._deadline = 0.0
        self.last_error = ""
        self.reset()

    # ---------- 生命周期 ----------
    def reset(self) -> None:
        self.ns = {}
        self._rebuild()

    def inject(self, tools: dict) -> None:
        self._tools.update(tools or {})
        self._rebuild()

    def _rebuild(self) -> None:
        def printer(*args, sep=" ", end="\n", **_kw):
            # 动态读 self._collector：每块执行前会换一个新的，否则输出会累积
            self._collector.write(sep.join(str(a) for a in args) + end)

        self._collector = _Collector(self.max_output)
        self.ns.clear()
        self.ns["__builtins__"] = _safe_builtins(printer)
        self.ns["__tick__"] = self._tick
        self.ns.update(self._tools)

    # ---------- 检查点 ----------
    def _tick(self) -> None:
        if self.stop_event.is_set():
            raise _Stop("主人叫停了")
        if time.monotonic() > self._deadline:
            raise _Stop(f"这一步超过 {self.timeout:.0f} 秒了")

    # ---------- 执行 ----------
    def exec_block(self, code: str, intent: str = "") -> BlockResult:
        res = BlockResult(intent=intent)
        started = time.monotonic()
        try:
            tree = protocol.validate(code)          # 调用方可能已经校验过，这里再兜一层
        except protocol.CodeRejected as exc:
            res.ok = False
            res.error = f"安全检查拒绝：{exc}"
            res.elapsed = time.monotonic() - started
            self.last_error = res.error
            return res

        tree = ast.fix_missing_locations(_Instrument().visit(tree))
        try:
            code_obj = compile(tree, "<agent>", "exec")
        except SyntaxError as exc:
            res.ok = False
            res.error = f"编译失败：第 {exc.lineno} 行 {exc.msg}"
            res.elapsed = time.monotonic() - started
            self.last_error = res.error
            return res

        self._collector = _Collector(self.max_output)
        self._deadline = started + self.timeout
        tb = None
        try:
            exec(code_obj, self.ns)                  # noqa: S102 —— 这是本模块的全部意义
            res.ok = True
        except _Stop as exc:
            res.ok = False
            res.stopped = True
            res.error = f"已中止：{exc}"
        except BaseException as exc:                 # noqa: BLE001
            res.ok = False
            tb = exc.__traceback__
            res.error = _fmt_error(exc, tb, code)

        res.elapsed = time.monotonic() - started
        res.stdout, res.truncated = self._collector.text()
        if res.truncated:
            res.stdout += f"\n（输出超过 {self.max_output // 1024}KB，已截断）"
        if not res.ok:
            self.last_error = res.error
        return res

    def exec_blocks(self, blocks: list[protocol.Block]) -> list[BlockResult]:
        """逐块执行，**任一块失败就停掉后面的**。

        半批执行比整批失败更难收拾：模型会以为后面的块跑过了。
        """
        out: list[BlockResult] = []
        for b in blocks:
            if self.stop_event.is_set():
                out.append(BlockResult(ok=False, intent=b.intent,
                                       error="已中止：主人叫停了", stopped=True))
                break
            r = self.exec_block(b.code, b.intent)
            out.append(r)
            if not r.ok:
                break
        return out


def _fmt_error(exc: BaseException, tb, code: str = "") -> str:
    """错误只留"类型 + 消息 + 出错那行"，完整 traceback 对模型是噪音。

    行号不能靠 ``fr.line``：代码是 ``compile(ast)`` 出来的，没进 linecache，
    ``fr.line`` 永远是空串。所以拿 lineno 回原源码里取。
    """
    name = type(exc).__name__
    msg = str(exc).strip().replace("\n", " ")[:300]
    lineno = 0
    try:
        for fr in traceback.extract_tb(tb):
            if fr.filename == "<agent>":
                lineno = fr.lineno or lineno
    except Exception:
        lineno = 0
    src = ""
    if lineno and code:
        rows = code.split("\n")
        if 1 <= lineno <= len(rows):
            src = rows[lineno - 1].strip()[:60]
    where = f"（第 {lineno} 行：{src}）" if lineno else ""
    return f"{name}: {msg}{where}" if msg else f"{name}{where}"


def describe_limits() -> list[str]:
    """给 README / 知情页用的"丑话"，别删。"""
    return [
        "挡不住专门构造的逃逸链",
        "挡不住 C 层阻塞（COM 卡死只能重启桌宠）",
        "挡不住白名单工具造成的真实破坏（覆盖的是真文件）",
        "挡不住数据外流（工具返回值会发往云端模型）",
        "挡不住内存炸弹",
        "挡不住提示注入",
    ]
