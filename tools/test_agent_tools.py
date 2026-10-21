# -*- coding: utf-8 -*-
"""B2 验收：工具层、win32 封装、确认门。

用法：python tools/test_agent_tools.py

**路径逃逸 8 例必须全拒**——这是整套安全设计的承重墙，不是"顺手测一下"。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox  # noqa: E402


def main() -> int:
    c = Checker("B2 工具层与确认门")
    from app import config
    from app.agent import gate as gate_mod
    from app.agent import tools as T
    from app.agent.gate import Gate

    with DataSandbox() as sb:
        cfg: dict = {"agent_allow_dirs": [], "agent_confirm": "always",
                     "agent_auto_approve": []}
        box = T.Toolbox(lambda: cfg)
        ws = config.agent_workspace_dir()

        # ---------------- 1. 路径逃逸 ----------------
        c.section("1. 路径逃逸（8 例必须全拒）")
        home = os.path.expanduser("~")
        escapes = [
            ("向上跳两级到 Windows", os.path.join(ws, "..", "..", "..", "Windows", "notepad.exe")),
            ("绝对路径到系统目录", r"C:\Windows\System32\drivers\etc\hosts"),
            ("NTFS 备用数据流", os.path.join(ws, "a.txt:evil")),
            ("受保护文件名 config.json", os.path.join(ws, "config.json")),
            ("受保护文件名 memory.json", os.path.join(ws, "sub", "memory.json")),
            ("系统保留名 CON", os.path.join(ws, "CON.txt")),
            ("UNC 路径", r"\\some-server\share\a.txt"),
            ("大小写混淆", r"C:\WINDOWS\SYSTEM32\config\SAM"),
        ]
        for name, p in escapes:
            try:
                got = T.resolve_safe(p, cfg)
                c(f"拒绝 · {name}", False, f"竟然通过了：{got}")
            except PermissionError as exc:
                c(f"拒绝 · {name}", True, str(exc)[:34])
            except Exception as exc:
                c(f"拒绝 · {name}", False, f"抛了别的异常：{exc!r}")

        # 超长路径
        long_p = os.path.join(ws, "a" * 300 + ".txt")
        try:
            T.resolve_safe(long_p, cfg)
            c("拒绝 · 超长路径", False, "竟然通过了")
        except PermissionError as exc:
            c("拒绝 · 超长路径", True, str(exc))

        # junction 逃逸（能建就建，建不了就跳过）
        link = os.path.join(ws, "evil_link")
        made = False
        try:
            if not os.path.exists(link):
                # errors="replace" 不能省：中文 Windows 上 cmd 的输出是 GBK，
                # 默认按 utf-8 解码会抛 UnicodeDecodeError，把整轮测试带崩。
                r = subprocess.run(["cmd", "/c", "mklink", "/J", link,
                                    r"C:\Windows"], capture_output=True,
                                   text=True, errors="replace", timeout=10)
                made = r.returncode == 0 and os.path.exists(link)
        except Exception:
            made = False
        if made:
            try:
                T.resolve_safe(os.path.join(link, "notepad.exe"), cfg)
                c("拒绝 · junction 逃逸", False, "竟然通过了")
            except PermissionError as exc:
                c("拒绝 · junction 逃逸", True, str(exc)[:34])
            try:
                os.rmdir(link)
            except Exception:
                pass
        else:
            c("拒绝 · junction 逃逸（环境建不了链接，按通过计）", True, "skipped")

        # 正常路径要放行
        ok_path = os.path.join(ws, "note.txt")
        c("放行 · 工作区文件", T.resolve_safe(ok_path, cfg) == os.path.normcase(
            os.path.realpath(os.path.abspath(ok_path))))
        c("放行 · 工作区里绕回自己",
          bool(T.resolve_safe(os.path.join(ws, "sub", "..", "a.txt"), cfg)))

        # 写模式额外拒可执行后缀
        for ext in (".exe", ".bat", ".ps1", ".vbs", ".lnk", ".reg"):
            try:
                T.resolve_safe(os.path.join(ws, "x" + ext), cfg, write=True)
                c(f"拒绝写 {ext}", False, "竟然通过了")
            except PermissionError:
                c(f"拒绝写 {ext}", True)
        c("写 .txt 放行", bool(T.resolve_safe(os.path.join(ws, "x.txt"), cfg, write=True)))

        # ---------------- 2. 文件读写 ----------------
        c.section("2. 文件读写")
        p1 = os.path.join(ws, "笔记.txt")
        r = box.file_write(p1, "第一版内容")
        c("写文件成功", r.get("verified") is True, r)
        c("读回来一致", box.file_read(p1)["content"] == "第一版内容")

        r = box.file_write(p1, "第二版内容")
        c("覆盖写成功", r.get("verified") is True)
        c("产生了 .bak", os.path.exists(p1 + ".bak"), os.listdir(ws))
        with open(p1 + ".bak", encoding="utf-8") as f:
            c(".bak 是上一版内容", f.read() == "第一版内容")

        box.file_write(p1, "第三版")
        c("第二代备份出现", os.path.exists(p1 + ".bak1"), sorted(os.listdir(ws)))

        c("不存在的文件报错",
          _raises(lambda: box.file_read(os.path.join(ws, "nope.txt")), FileNotFoundError))

        # 二进制拒绝
        binp = os.path.join(ws, "bin.dat")
        with open(binp, "wb") as f:
            f.write(b"\x00\x01\x02binary")
        c("二进制文件被拒",
          _raises(lambda: box.file_read(binp), ValueError))

        # 编码兜底：GBK 文件
        gbkp = os.path.join(ws, "gbk.txt")
        with open(gbkp, "wb") as f:
            f.write("中文内容".encode("gbk"))
        c("GBK 文件能读", box.file_read(gbkp)["content"] == "中文内容")

        # 大文件截断
        bigp = os.path.join(ws, "big.txt")
        box.file_write(bigp, "x" * 9000)
        r = box.file_read(bigp, max_chars=500)
        c("超长内容被截", r["truncated"] and len(r["content"]) == 500, r["chars"])

        # 目录列举
        os.makedirs(os.path.join(ws, "子目录"), exist_ok=True)
        with open(os.path.join(ws, "子目录", "a.txt"), "w", encoding="utf-8") as f:
            f.write("a")
        rows = box.file_list("", depth=2)
        names = [x["name"] for x in rows]
        c("列目录有内容", len(rows) >= 3, names[:6])
        c("列目录带路径", all(x.get("path") for x in rows))
        c("目录项标了 dir", any(x["dir"] for x in rows))
        c("深度 1 不展开子目录",
          not any(x["name"] == "a.txt" for x in box.file_list("", depth=1)))
        c("深度 2 能展开", any(x["name"] == "a.txt" for x in box.file_list("", depth=2)))

        # ---------------- 3. 启动程序 ----------------
        c.section("3. 启动程序（LOLBins 必须全拒）")
        for bad in ("cmd", "cmd.exe", "powershell", "wscript", "mshta",
                    "regedit", "rundll32", "certutil", "schtasks", "wmic",
                    "taskkill", "diskpart", "C:\\Windows\\System32\\cmd.exe"):
            try:
                box.app_launch(bad)
                c(f"拒绝 · {bad}", False, "竟然启动了")
            except PermissionError as exc:
                c(f"拒绝 · {bad}", True, str(exc)[:30])
            except Exception as exc:
                c(f"拒绝 · {bad}", False, repr(exc))

        c("找不到的程序报 LookupError",
          _raises(lambda: box.app_launch("这个程序不存在啊"), LookupError))

        # 网址
        c("拒绝 file: 协议",
          _raises(lambda: box.open_url("file:///C:/Windows"), PermissionError))
        c("拒绝 javascript: 协议",
          _raises(lambda: box.open_url("javascript:alert(1)"), PermissionError))
        c("拒绝 ms- 协议",
          _raises(lambda: box.open_url("ms-settings:"), PermissionError))
        c("拒绝没有协议的",
          _raises(lambda: box.open_url("www.example.com"), PermissionError))

        # ---------------- 4. 系统工具 ----------------
        c.section("4. 系统工具")
        info = box.sys_info()
        c("sys_info 有核数", int(info.get("cpu_count") or 0) > 0, info.get("cpu_count"))
        c("sys_info 有工作区", info.get("workspace") == ws)
        c("sys_info 不含进程名",
          not any("process" in k for k in info), list(info))

        # 音量/亮度：能读就验值，不能读就要求抛出可读错误
        for name, fn in (("音量", box.volume_get), ("亮度", box.brightness_get)):
            try:
                got = fn()
                c(f"{name}可读", isinstance(got, dict), got)
            except Exception as exc:
                c(f"{name}失败时给出人话", isinstance(exc, RuntimeError), repr(exc))

        # 剪贴板
        r = box.clipboard_set("测试剪贴板内容")
        c("剪贴板写入成功", r.get("ok") is True, r)
        got = box.clipboard_get()
        c("剪贴板读回来一致", got["text"] == "测试剪贴板内容", got)
        box.clipboard_set("我的 key 是 sk-abcdefghijklmnop1234")
        got = box.clipboard_get()
        c("密钥被打码", "sk-abcdefghijklmnop1234" not in got["text"]
          and "***" in got["text"], got["text"])

        # 窗口
        wins = box.windows_list()
        c("窗口列表是 list", isinstance(wins, list), len(wins))
        if wins:
            c("窗口项有 id 和标题", all("id" in w and "title" in w for w in wins))
            c("窗口标题不超过 60 字", all(len(w["title"]) <= 60 for w in wins))
        c("失效的 window id 被拒",
          _raises(lambda: box.window_activate(99999), LookupError))

        # 截图
        r = box.screenshot_save("测试截图")
        c("截图文件写出来了", os.path.exists(r["path"]), r["path"])
        c("截图是 BMP", open(r["path"], "rb").read(2) == b"BM")
        c("截图不算小", os.path.getsize(r["path"]) > 5000,
          os.path.getsize(r["path"]))
        c("截图不进上下文", "content" not in r and "note" in r, list(r))

        # 杂项
        said: list[str] = []
        box2 = T.Toolbox(lambda: cfg, on_notify=said.append)
        box2.notify("该喝水了")
        c("notify 回调到了", said == ["该喝水了"], said)
        c("wait 生效", box2.wait(0.2).get("waited") == 0.2)
        c("wait 有上限", box2.wait(999).get("waited") == 10.0)
        c("ask_user 记下问题", box2.ask_user("要删哪个？").get("asked") == "要删哪个？")
        c("ask_user 不返回答案", box2.pending_question == "要删哪个？")
        c("finish 记下结论", box2.finish("都办好了").get("finished") is True)
        c("terminate 记下原因", box2.terminate("找不到文件").get("terminated") is True)

        # ---------------- 5. 工具注册 ----------------
        c.section("5. 工具注册")
        names = set(box.specs)
        for want in ("sys_info", "file_read", "file_write", "file_list",
                     "app_launch", "open_url", "clipboard_get", "clipboard_set",
                     "volume_get", "volume_set", "brightness_get", "brightness_set",
                     "lock_screen", "windows_list", "window_activate",
                     "screenshot_save", "notify", "wait", "ask_user",
                     "finish", "terminate", "top_processes"):
            c(f"注册了 {want}", want in names)
        c("工具总数合理", 20 <= len(names) <= 26, len(names))
        text = box.specs_text()
        c("规格文本有风险标注", "[safe]" in text and "[confirm]" in text, text[:60])
        c("top_processes 默认不给模型",
          "top_processes" not in box.functions(), list(box.functions())[:5])
        cfg["agent_allow_process_list"] = True
        c("打开开关后才给", "top_processes" in T.Toolbox(lambda: cfg).functions())
        cfg["agent_allow_process_list"] = False

        # ---------------- 6. 确认门 ----------------
        c.section("6. 确认门")
        asked: list[tuple] = []

        def ask_yes(tool, risk, detail):
            asked.append((tool, risk))
            return "yes"

        g = Gate(lambda: {"agent_confirm": "always"}, ask_yes)
        c("SAFE 不问就放行", g.check("file_read", T.SAFE) is True)
        c("SAFE 没触发询问", asked == [])
        c("CONFIRM 问了才放行", g.check("file_write", T.CONFIRM) is True)
        c("CONFIRM 确实问了", asked and asked[0][0] == "file_write", asked)

        g2 = Gate(lambda: {}, lambda *a: "no")
        c("主人拒绝就不执行", g2.check("file_write", T.CONFIRM) is False)
        c("拒绝原因写下来了", "拒绝" in g2.last_reason, g2.last_reason)
        c("记进 denied", "file_write" in g2.denied)

        g3 = Gate(lambda: {}, lambda *a: "always")
        c("always 放行", g3.check("file_write", T.CONFIRM) is True)
        c("always 记进会话", "file_write" in g3.session_allowed)
        asked2: list = []
        g3._ask = lambda *a: asked2.append(a) or "no"
        c("第二次不再问", g3.check("file_write", T.CONFIRM) is True)
        c("确实没再问", asked2 == [])

        g4 = Gate(lambda: {}, lambda *a: "always")
        c("DANGER 一律拒绝", g4.check("top_processes", T.DANGER) is False)
        c("DANGER 不给会话放行", "top_processes" not in g4.session_allowed)

        g5 = Gate(lambda: {"agent_confirm": "never"}, None)
        c("confirm=never 时不问直接放行", g5.check("file_write", T.CONFIRM) is True)
        c("confirm=never 也不放行 DANGER",
          g5.check("top_processes", T.DANGER) is False)

        g6 = Gate(lambda: {"agent_auto_approve": ["file_write"]}, None)
        c("白名单工具直接放行", g6.check("file_write", T.CONFIRM) is True)
        c("白名单外的还是要问", g6.check("app_launch", T.CONFIRM) is False)

        g7 = Gate(lambda: {}, None)
        c("没有确认通道时拒绝", g7.check("file_write", T.CONFIRM) is False)
        c("原因说明白", "确认通道" in g7.last_reason, g7.last_reason)

        def boom(*a):
            raise RuntimeError("UI 崩了")

        g8 = Gate(lambda: {}, boom)
        c("确认过程出错时拒绝", g8.check("file_write", T.CONFIRM) is False)

        g9 = Gate(lambda: {}, lambda *a: None)
        c("返回 None 当拒绝", g9.check("file_write", T.CONFIRM) is False)

        g10 = Gate(lambda: {}, lambda *a: "yes")
        c("未知风险等级按危险处理",
          g10.check("whatever", "weird") is False)

    return c.summary()


def _raises(fn, exc_type) -> bool:
    try:
        fn()
        return False
    except exc_type:
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
