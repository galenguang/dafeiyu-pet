# -*- coding: utf-8 -*-
"""B1 验收：受限执行环境。

用法：python tools/test_agent_sandbox.py
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker  # noqa: E402


def main() -> int:
    c = Checker("B1 受限执行环境")
    from app.agent import protocol
    from app.agent.sandbox import Sandbox

    # ---------------- 1. 基本执行 ----------------
    c.section("1. 基本执行")
    sb = Sandbox()
    r = sb.exec_block("print('hi')", "打招呼")
    c("能跑", r.ok, r.error)
    c("捕获 print", r.stdout.strip() == "hi", repr(r.stdout))
    c("intent 带回来", r.intent == "打招呼")
    c("耗时被记录", r.elapsed >= 0)

    r = sb.exec_block("a = 1 + 1\nprint(a)")
    c("多语句", r.stdout.strip() == "2", repr(r.stdout))

    r = sb.exec_block("print('中文', '也行')")
    c("中文输出", "中文 也行" in r.stdout, r.stdout)

    r = sb.exec_block("for i in range(3):\n    print(i)")
    c("循环内 print 都收得到", r.stdout.split() == ["0", "1", "2"], r.stdout)

    r = sb.exec_block("print()")
    c("空 print 不崩", r.ok)

    # ---------------- 2. 跨块共享变量 ----------------
    c.section("2. 跨块共享变量")
    sb2 = Sandbox()
    sb2.exec_block("a = 42\nnames = ['x']")
    r = sb2.exec_block("print(a)\nnames.append('y')\nprint(len(names))")
    c("上一块的变量还在", "42" in r.stdout, r.stdout)
    c("可变对象被真的改掉了", "2" in r.stdout, r.stdout)

    r = sb2.exec_block("def f(x):\n    return x * 2\nprint(f(3))")
    c("块里定义的函数可用", "6" in r.stdout, r.stdout)
    r = sb2.exec_block("print(f(4))")
    c("函数跨块还能用", "8" in r.stdout, r.stdout)

    sb2.reset()
    r = sb2.exec_block("print(a)")
    c("reset 清掉变量", not r.ok and "NameError" in r.error, r.error)

    # ---------------- 3. 超时打断 ----------------
    c.section("3. 超时打断")
    sb3 = Sandbox(timeout=0.5)
    t0 = time.monotonic()
    r = sb3.exec_block("while True:\n    pass")
    cost = time.monotonic() - t0
    c("死循环被打断", not r.ok and r.stopped, r.error)
    c("打断得够快（<3s）", cost < 3.0, f"{cost:.2f}s")
    c("错误里说明是超时", "秒" in r.error, r.error)

    # 阻塞式循环也要能打断
    sb3b = Sandbox(timeout=0.4)
    r = sb3b.exec_block("n = 0\nwhile True:\n    n += 1")
    c("计数循环也能打断", r.stopped, r.error)

    # except Exception 吞不掉检查点
    sb3c = Sandbox(timeout=0.4)
    r = sb3c.exec_block(
        "while True:\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        pass")
    c("except Exception 吞不掉急停", r.stopped, r.error)

    # 超时之后还能继续用（下一块重置 deadline）
    r = sb3.exec_block("print('还活着')")
    c("超时后实例仍可用", r.ok and "还活着" in r.stdout, r.error)

    # ---------------- 4. 急停 ----------------
    c.section("4. 急停")
    ev = threading.Event()
    sb4 = Sandbox(timeout=30.0, stop_event=ev)
    threading.Timer(0.3, ev.set).start()
    t0 = time.monotonic()
    r = sb4.exec_block("while True:\n    pass")
    cost = time.monotonic() - t0
    c("急停信号能打断", r.stopped and "叫停" in r.error, r.error)
    c("急停反应快（<2s）", cost < 2.0, f"{cost:.2f}s")

    ev2 = threading.Event()
    ev2.set()
    sb4b = Sandbox(stop_event=ev2)
    r = sb4b.exec_block("print('不该执行')")
    c("已经急停时不执行新块", not r.ok and "叫停" in r.error, r.error)

    # ---------------- 5. 输出截断 ----------------
    c.section("5. 输出截断")
    sb5 = Sandbox()
    r = sb5.exec_block("for i in range(2000):\n    print('x' * 50)")
    c("100KB 输出被截断", len(r.stdout) < 18000, len(r.stdout))
    c("标记了截断", r.truncated and "截断" in r.stdout, r.stdout[-40:])
    c("头部内容还在", r.stdout.startswith("x"))

    r = sb5.exec_block("print('tail-marker')")
    c("下一块输出不受影响", r.stdout.strip() == "tail-marker", r.stdout)

    # 一次超长单行
    r = sb5.exec_block("print('y' * 100000)")
    c("单行超长也被截", len(r.stdout) < 18000, len(r.stdout))

    # ---------------- 6. 异常处理 ----------------
    c.section("6. 异常处理")
    sb6 = Sandbox()
    r = sb6.exec_block("1 / 0")
    c("除零被抓", not r.ok and "ZeroDivisionError" in r.error, r.error)
    c("错误带行号", "第 1 行" in r.error, r.error)

    r = sb6.exec_block("x = [1]\nprint(x[5])")
    c("越界被抓", not r.ok and "IndexError" in r.error, r.error)

    r = sb6.exec_block("d = {}\nprint(d['nope'])")
    c("KeyError 被抓", not r.ok and "KeyError" in r.error, r.error)

    r = sb6.exec_block("undefined_name")
    c("未定义变量被抓", not r.ok and "NameError" in r.error, r.error)

    r = sb6.exec_block("def f():\n    return f()\nf()")
    c("无限递归不崩进程", not r.ok and "RecursionError" in r.error, r.error)

    r = sb6.exec_block("raise RuntimeError('我自己抛的')")
    c("自定义异常消息保留", "我自己抛的" in r.error, r.error)

    r = sb6.exec_block("try:\n    1 / 0\nexcept ZeroDivisionError:\n    print('接住了')")
    c("用户自己的 try/except 有效", r.ok and "接住了" in r.stdout, r.error)

    # ---------------- 7. 安全边界 ----------------
    c.section("7. 安全边界")
    sb7 = Sandbox()
    for name, code in (
        ("import os", "import os"),
        ("逃逸链", "().__class__.__base__.__subclasses__()"),
        ("open", "open('a.txt')"),
        ("eval", "eval('1')"),
        ("getattr", "getattr(1, 'x')"),
        ("__builtins__", "print(__builtins__)"),
        ("私有属性", "print([]._x)"),
    ):
        r = sb7.exec_block(code)
        c(f"拒绝 · {name}", not r.ok and "拒绝" in r.error, r.error[:40])

    b = sb7.ns["__builtins__"]
    for name in ("open", "eval", "exec", "__import__", "getattr", "type", "object"):
        c(f"builtins 里没有 {name}", name not in b)
    for name in ("print", "len", "range", "str", "int", "isinstance"):
        c(f"builtins 里有 {name}", name in b)

    r = sb7.exec_block("print(type(1))")
    c("type() 用不了", not r.ok, r.error[:40])

    # ---------------- 8. 工具注入 ----------------
    c.section("8. 工具注入")
    calls: list = []

    def sys_info():
        calls.append("sys_info")
        return {"os": "Windows", "cpu": 12}

    def file_read(path):
        calls.append(("read", path))
        return "文件内容"

    sb8 = Sandbox({"sys_info": sys_info, "file_read": file_read})
    r = sb8.exec_block("info = sys_info()\nprint(info['os'])")
    c("工具能调用", r.ok and "Windows" in r.stdout, r.stdout or r.error)
    r = sb8.exec_block("print(file_read('a.txt'))")
    c("带参工具能调用", "文件内容" in r.stdout, r.stdout or r.error)
    c("工具真被调了", calls == ["sys_info", ("read", "a.txt")], calls)

    sb8.inject({"extra": lambda: "新工具"})
    r = sb8.exec_block("print(extra())")
    c("可以后注入工具", "新工具" in r.stdout, r.stdout or r.error)

    # 工具抛异常也要被抓住
    def boom():
        raise ValueError("工具炸了")

    sb8.inject({"boom": boom})
    r = sb8.exec_block("boom()")
    c("工具异常被抓住", not r.ok and "工具炸了" in r.error, r.error)

    # ---------------- 9. 批量执行 ----------------
    c.section("9. 批量执行")
    blocks = protocol.parse("<code>a = 1</code><code>print(a + 1)</code>")
    sb9 = Sandbox()
    results = sb9.exec_blocks(blocks)
    c("两块都跑了", len(results) == 2 and all(x.ok for x in results))
    c("第二块用上了第一块的变量", "2" in results[1].stdout, results[1].stdout)

    blocks = protocol.parse("<code>flag = 'ran'</code>"
                            "<code>1 / 0</code>"
                            "<code>print('不该跑')</code>")
    sb9b = Sandbox()
    results = sb9b.exec_blocks(blocks)
    c("第一块成功", results[0].ok)
    c("第二块失败", not results[1].ok)
    c("失败后不再执行第三块", len(results) == 2, len(results))
    c("第三块的痕迹不存在", "flag" in sb9b.ns)

    # ---------------- 10. 性能 ----------------
    c.section("10. 性能")
    sb10 = Sandbox()
    t0 = time.monotonic()
    r = sb10.exec_block("s = 0\nfor i in range(200000):\n    s += i\nprint(s)")
    cost = time.monotonic() - t0
    c("20 万次循环能跑完", r.ok, r.error)
    c("检查点开销可接受（<3s）", cost < 3.0, f"{cost:.2f}s")

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
