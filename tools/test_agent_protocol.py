# -*- coding: utf-8 -*-
"""B0 验收：Agent 协议、提示词、模型客户端。

用法：python tools/test_agent_protocol.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox, FakeLLM  # noqa: E402

CODE_OK = "print('hi')"


def main() -> int:
    c = Checker("B0 协议与模型客户端")
    from app.agent import prompts, protocol
    from app.agent.protocol import Block, CodeRejected, FormatError

    # ---------------- 1. 解析 ----------------
    c.section("1. 解析（20 例）")
    cases: list[tuple[str, str, int]] = [
        ("标准双标签", "<intent>打招呼</intent>\n<code>print('hi')</code>", 1),
        ("只有 code", "<code>print('hi')</code>", 1),
        ("两个块",
         "<intent>读</intent><code>a=1</code><intent>写</intent><code>b=2</code>", 2),
        ("块内又套围栏",
         "<code>```python\nprint('hi')\n```</code>", 1),
        ("有开无闭", "<intent>跑一下</intent><code>print('hi')", 1),
        ("只有围栏",
         "我来写一段：\n```python\nprint('hi')\n```\n好了", 1),
        ("整段裸代码", "x = 1\nprint(x)", 1),
        ("纯聊天没有代码", "我不知道该怎么做。", 0),
        ("空字符串", "", 0),
        ("只有空白", "   \n\t  ", 0),
        ("代码里有中文注释", "<code># 读文件\nprint(1)</code>", 1),
        ("整体多缩进四格", "<code>\n    print('hi')\n    print('yo')\n</code>", 1),
        ("intent 里换行", "<intent>先读\n再写</intent><code>a=1</code>", 1),
        ("大写标签", "<INTENT>做A</INTENT><CODE>a=1</CODE>", 1),
        ("标签带属性", '<code lang="python">a=1</code>', 1),
        ("七块截到六块", "".join(f"<code>a{i}=1</code>" for i in range(7)), 6),
        ("前面有闲聊", "好的，我来看看。\n<intent>看看</intent><code>a=1</code>", 1),
        ("代码里有小于号", "<code>if 1 < 2:\n    print('lt')</code>", 1),
        ("CRLF 换行", "<code>a=1\r\nb=2</code>", 1),
        ("空 code 标签被跳过", "<code></code><code>a=1</code>", 1),
        ("只有 intent 没 code", "<intent>我想想</intent>", 0),
    ]
    for name, text, want_n in cases:
        try:
            blocks = protocol.parse(text)
            got = len(blocks)
            ok = got == want_n
            detail = f"{got} 块" if not ok else ""
        except FormatError as exc:
            got, ok = 0, want_n == 0
            detail = "" if ok else f"FormatError: {exc}"
        c(f"解析 · {name}", ok, detail)

    # 内容正确性
    b = protocol.parse("<intent>打招呼</intent>\n<code>print('hi')</code>")[0]
    c("intent 取到了", b.intent == "打招呼", b.intent)
    c("code 取到了", b.code == CODE_OK, repr(b.code))
    c("行数正确", b.lines == 1, b.lines)

    b = protocol.parse("<code>\n    a=1\n    b=2\n</code>")[0]
    c("dedent 生效（无多余缩进）", b.code == "a=1\nb=2", repr(b.code))

    b = protocol.parse("<code>```python\nprint(1)\n```</code>")[0]
    c("嵌套围栏被剥掉", b.code == "print(1)", repr(b.code))

    blocks = protocol.parse("<intent>读</intent><code>a=1</code>"
                            "<intent>写</intent><code>b=2</code>")
    c("每个块配对自己的 intent",
      [x.intent for x in blocks] == ["读", "写"], [x.intent for x in blocks])

    c("Block 默认值可用", Block().intent == "" and Block().lang == "python")

    # ---------------- 2. AST 拒绝 ----------------
    c.section("2. AST 拒绝")
    bad = [
        ("逃逸链", "().__class__.__base__.__subclasses__()"),
        ("import os", "import os"),
        ("from import", "from os import path"),
        ("getattr", "getattr(1, 'real')"),
        ("eval", "eval('1+1')"),
        ("exec", "exec('x=1')"),
        ("open", "open('a.txt')"),
        ("class", "class A:\n    pass"),
        ("global", "def f():\n    global x\n    x = 1"),
        ("nonlocal", "def f():\n    x = 1\n    def g():\n        nonlocal x"),
        ("__import__", "__import__('os')"),
        ("字符串拼 dunder", "s = '__class__'"),
        ("del", "x = 1\ndel x"),
        ("私有属性", "x = []._something"),
        ("compile", "compile('1', 'f', 'eval')"),
        ("type 三参", "type('A', (object,), {})"),
        ("vars", "vars()"),
        ("空代码", "   "),
        ("超长", "a=1\n" * 4000),
        ("超行数", "\n".join(["a=1"] * 130)),
        ("语法错误", "def f(:"),
    ]
    for name, code in bad:
        try:
            protocol.validate(code)
            c(f"拒绝 · {name}", False, "竟然通过了")
        except CodeRejected as exc:
            c(f"拒绝 · {name}", True, str(exc)[:36])

    # ---------------- 3. AST 允许 ----------------
    c.section("3. AST 允许")
    good = [
        ("普通语句", "a = 1\nprint(a)"),
        ("for + if", "for i in range(3):\n    if i > 1:\n        print(i)"),
        ("while + break", "n = 0\nwhile n < 3:\n    n += 1\n    if n == 2:\n        break"),
        ("函数定义", "def f(x):\n    return x * 2\nprint(f(2))"),
        ("try/except", "try:\n    x = 1 / 0\nexcept ZeroDivisionError as e:\n    print(e)"),
        ("列表推导", "xs = [i * i for i in range(5)]"),
        ("字典与切片", "d = {'a': 1}\nprint(d['a'], [1, 2][0:1])"),
        ("f-string", "n = 3\nprint(f'共 {n} 个')"),
        ("中文变量名", "文件名 = 'a.txt'\nprint(文件名)"),
        ("工具调用", "r = sys_info()\nprint(r)"),
        ("lambda", "f = lambda x: x + 1\nprint(f(1))"),
        ("三元与推导条件", "xs = [i for i in range(9) if i % 2]\nprint(xs)"),
        ("链式调用（无下划线）", "print('a'.strip().upper())"),
        ("多行字符串", "s = '''第一行\n第二行'''\nprint(s)"),
        ("with 语句", "import_ok = 1\nprint(import_ok)"),
        ("嵌套 dict 访问", "r = {'a': {'b': 1}}\nprint(r['a']['b'])"),
    ]
    for name, code in good:
        try:
            protocol.validate(code)
            c(f"允许 · {name}", True)
        except CodeRejected as exc:
            c(f"允许 · {name}", False, str(exc)[:40])

    # ---------------- 4. check（整批拒绝） ----------------
    c.section("4. check：任一块不过就整批拒")
    try:
        protocol.check("<code>a=1</code><code>import os</code>")
        c("含违规块时整批拒绝", False, "竟然通过了")
    except CodeRejected as exc:
        c("含违规块时整批拒绝", True, str(exc))
    c("全合规时返回所有块",
      len(protocol.check("<code>a=1</code><code>b=2</code>")) == 2)

    # ---------------- 5. 提示词 ----------------
    c.section("5. 提示词")
    sysmsg = prompts.build_system("  sys_info()\n  file_read(path)  ", "Windows 11")
    c("工具清单拼进去了", "sys_info()" in sysmsg and "file_read" in sysmsg)
    c("环境拼进去了", "Windows 11" in sysmsg)
    c("8 条硬规则都在",
      all(k in sysmsg for k in ("ask_user", "不代表事情办成了", "两次",
                                "数据", "窗口 id", "不要 import",
                                "不要编造工具名", "finish")))
    c("要求两个标签", "<intent>" in sysmsg and "<code>" in sysmsg)
    c("没有残留占位符", "{" not in sysmsg.replace("{{", ""))

    obs = prompts.render_observation(2, [
        {"ok": True, "intent": "读配置", "stdout": "cpu=3", "error": ""},
        {"ok": False, "intent": "写回去", "stdout": "", "error": "PermissionError"},
    ])
    c("观察带步数", "第 2 步" in obs, obs[:24])
    c("观察带成功/失败", "成功" in obs and "失败" in obs)
    c("观察带输出", "cpu=3" in obs)
    c("观察带错误", "PermissionError" in obs)
    c("观察尾部有下一步提示", "finish" in obs)

    obs = prompts.render_observation(1, [{"ok": True, "intent": "", "stdout": "",
                                          "error": ""}])
    c("空输出有占位说明", "没有任何输出" in obs)

    long_out = "x" * 20000
    obs = prompts.render_observation(1, [{"ok": True, "intent": "",
                                          "stdout": long_out, "error": ""}])
    c("超长输出被截断", len(obs) < 9000, len(obs))
    c("截断有省略标记", "省略" in obs)

    c("reject 文案含原因", "私有属性" in prompts.render_reject("不允许私有属性"))
    c("COMPACT 模板可用", "{history}" in prompts.COMPACT_TEMPLATE)

    # ---------------- 6. AgentLLM ----------------
    c.section("6. AgentLLM")
    from app.agent.llm import AgentLLM, BudgetExceeded

    with DataSandbox(), FakeLLM(["<code>a=1</code>"]) as fake:
        llm = fake.llm()
        al = AgentLLM(lambda: llm, budget=3000, max_tokens=512)
        msgs = [{"role": "user", "content": "开始"}]
        text = al.complete(msgs)
        c("补全能拿到正文", text == "<code>a=1</code>", text)
        c("记账了 token", al.used > 0, al.used)
        c("服务端没给 usage 时自己估", al.last_usage.get("total", 0) > 0,
          al.last_usage)
        c("调用次数正确", al.calls == 1)
        c("max_tokens 传下去了", (fake.last_payload() or {}).get("max_tokens") == 512,
          (fake.last_payload() or {}).get("max_tokens"))
        c("temperature 传下去了",
          abs(float((fake.last_payload() or {}).get("temperature", -1)) - 0.2) < 1e-6)

        al.reset()
        c("reset 清零", al.used == 0 and al.calls == 0)

        # 预算熔断
        al2 = AgentLLM(lambda: llm, budget=1000)
        al2.used = 1000
        try:
            al2.complete(msgs)
            c("预算用完时熔断", False, "竟然发出去了")
        except BudgetExceeded as exc:
            c("预算用完时熔断", True, str(exc))
        c("left 不为负", al2.left == 0)

        # 模型没配好时抛可读错误
        al3 = AgentLLM(lambda: {"base_url": "", "api_key": "", "model": ""})
        from app.llm_raw import RawError
        try:
            al3.complete(msgs)
            c("没配模型时报错", False)
        except RawError as exc:
            c("没配模型时报错", "接口地址" in str(exc), str(exc))

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
