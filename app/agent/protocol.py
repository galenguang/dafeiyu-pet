# -*- coding: utf-8 -*-
"""Agent 协议：``<intent>`` + ``<code>`` 的解析与 AST 静态校验。

**为什么不用 JSON / function calling**：

1. 中转站对 ``tools`` / ``tool_choice`` 的支持参差不齐，一半会直接 400。
2. "先读、再写、再核验"用代码表达比一串工具调用自然得多，块与块之间还能
   共享变量（OpenGCUA 实测步数降 53%，原因就在这）。
3. 格式越简单，小模型越不容易崩。只要两个标签，容错空间大。

**这个模块只做两件事**：把一段文本变成 ``Block`` 列表；把一段代码判成
"能跑"或"不能跑"。执行是 ``sandbox.py`` 的事，别混在一起。
"""
from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass, field

MAX_CODE_CHARS = 8000
MAX_CODE_LINES = 120


class FormatError(Exception):
    """模型输出不符合协议，文案可以直接回灌给模型看。"""


class CodeRejected(Exception):
    """代码被静态校验拒绝。"""


@dataclass
class Block:
    intent: str = ""
    code: str = ""
    lang: str = "python"

    @property
    def lines(self) -> int:
        return self.code.count("\n") + 1


@dataclass
class Observation:
    """一块代码的执行回执。``ok`` 只表示"没抛异常"，**不代表业务办成了**。"""
    ok: bool = True
    intent: str = ""
    stdout: str = ""
    error: str = ""
    tool_calls: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
_CODE_TAG = re.compile(r"<code[^>]*>(.*?)(?:</code>|$)", re.S | re.I)
_INTENT_TAG = re.compile(r"<intent[^>]*>(.*?)(?:</intent>|$)", re.S | re.I)
_FENCE = re.compile(r"```[ \t]*(?:python|py)?[ \t]*\r?\n(.*?)(?:```|$)", re.S | re.I)
_ANY_TAG = re.compile(r"</?[a-zA-Z_][\w-]*[^>]*>")


def _clean(code: str) -> str:
    """去掉标签里可能又套了一层的 ``` 围栏，再统一缩进。"""
    t = (code or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if "```" in t:
        t = _FENCE.sub(lambda m: m.group(1), t)
        t = t.replace("```", "")
    # 模型常在标签里整体缩进四格，dedent 之后才像正常代码
    t = textwrap.dedent(t)
    return t.strip("\n")


def _last_intent_before(text: str, pos: int) -> str:
    found = ""
    for m in _INTENT_TAG.finditer(text[:pos]):
        found = m.group(1)
    return (found or "").strip()[:200]


def _parse_tags(text: str) -> list[Block]:
    out: list[Block] = []
    for m in _CODE_TAG.finditer(text):
        code = _clean(m.group(1))
        if not code.strip():
            continue
        out.append(Block(intent=_last_intent_before(text, m.start()), code=code))
    return out


def _parse_fences(text: str) -> list[Block]:
    out: list[Block] = []
    for m in _FENCE.finditer(text):
        code = _clean(m.group(1))
        if not code.strip():
            continue
        out.append(Block(intent="", code=code))
    return out


def _looks_like_python(text: str) -> bool:
    """整段裸代码的兜底判定：能过 ast.parse 才认。

    这一条很重要——模型如果只回一句"我不知道该怎么做"，那不该被当成代码去跑，
    也不该被当成格式错误去纠错，而是该走"卡住了"的分支。
    """
    try:
        ast.parse(text)
    except SyntaxError:
        return False
    except Exception:
        return False
    return True


def _parse_bare(text: str) -> list[Block]:
    body = text.strip()
    if not body or _ANY_TAG.search(body):
        return []
    # 只有一句话（没有换行也没有括号/等号）时不当代码
    if "\n" not in body and not re.search(r"[()=:]", body):
        return []
    if not _looks_like_python(body):
        return []
    return [Block(intent="", code=_clean(body))]


def parse(reply: str) -> list[Block]:
    """五级容错解析。全失败抛 ``FormatError``。

    1. 标准 ``<code>`` 标签（``</code>`` 缺失也算）
    2. 只有 ```python 围栏
    3. 整段裸代码
    4. （第 1 级已覆盖"有开无闭"）
    5. 失败 → ``FormatError``，交给主循环走纠错重试
    """
    text = (reply or "").strip()
    if not text:
        raise FormatError("模型没有输出")

    blocks = _parse_tags(text) or _parse_fences(text) or _parse_bare(text)
    if not blocks:
        raise FormatError("没找到 <code> 代码块")
    if len(blocks) > 6:
        blocks = blocks[:6]          # 一次给 6 块以上基本是在乱写
    return blocks


def has_tag(text: str) -> bool:
    return bool(_ANY_TAG.search(text or ""))


# --------------------------------------------------------------------------
# AST 静态校验
# --------------------------------------------------------------------------
# 这些名字一旦出现就拒——即使 builtins 里没有，也不给第二次机会
BANNED_NAMES = {
    "__import__", "eval", "exec", "compile", "open", "input", "breakpoint",
    "getattr", "setattr", "delattr", "vars", "globals", "locals", "dir",
    "type", "object", "super", "memoryview", "classmethod", "staticmethod",
    "exit", "quit", "help", "copyright", "credits", "license",
}

# 字符串里出现这些碎片，基本就是想拼出逃逸链
BANNED_FRAGMENTS = (
    "__class__", "__base__", "__subclasses__", "__mro__", "__dict__",
    "__globals__", "__builtins__", "__getattribute__", "__reduce__",
    "__code__", "__loader__", "__spec__", "__import__", "__module__",
    "__bases__", "__init_subclass__", "__subclasshook__",
)

BANNED_NODES: dict[type, str] = {
    ast.Import: "不允许 import",
    ast.ImportFrom: "不允许 import",
    ast.ClassDef: "不允许定义 class",
    ast.Global: "不允许 global",
    ast.Nonlocal: "不允许 nonlocal",
    ast.Delete: "不允许 del",
    ast.AsyncFunctionDef: "不支持 async",
    ast.Await: "不支持 await",
}


def validate(code: str) -> ast.Module:
    """静态校验。通过返回 AST，否则抛 ``CodeRejected``（文案可直接给模型看）。"""
    code = code or ""
    if not code.strip():
        raise CodeRejected("代码是空的")
    if len(code) > MAX_CODE_CHARS:
        raise CodeRejected(f"代码太长（{len(code)} 字符，上限 {MAX_CODE_CHARS}）")
    if code.count("\n") + 1 > MAX_CODE_LINES:
        raise CodeRejected(f"代码太长（{code.count(chr(10)) + 1} 行，上限 {MAX_CODE_LINES} 行）")

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise CodeRejected(f"语法错误：第 {exc.lineno} 行 {exc.msg}") from None
    except Exception as exc:
        raise CodeRejected(f"解析失败：{exc}") from None

    for node in ast.walk(tree):
        reason = BANNED_NODES.get(type(node))
        if reason:
            raise CodeRejected(reason)

        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise CodeRejected(f"不允许访问私有属性 .{node.attr}")

        elif isinstance(node, ast.Name):
            if node.id in BANNED_NAMES:
                raise CodeRejected(f"不允许使用 {node.id}")
            if node.id.startswith("__"):
                raise CodeRejected(f"不允许使用 {node.id}")

        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                low = node.value
                for frag in BANNED_FRAGMENTS:
                    if frag in low:
                        raise CodeRejected(f"字符串里不允许出现 {frag}")

        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in BANNED_NAMES:
                raise CodeRejected(f"不允许调用 {fn.id}()")

    return tree


def check(reply: str) -> list[Block]:
    """解析 + 逐块校验。任一块不过就整批拒绝——半批执行会让状态更难收拾。"""
    blocks = parse(reply)
    for i, b in enumerate(blocks, 1):
        try:
            validate(b.code)
        except CodeRejected as exc:
            raise CodeRejected(f"第 {i} 块：{exc}") from None
    return blocks
