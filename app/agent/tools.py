# -*- coding: utf-8 -*-
"""工具层：20 个函数 + 风险标注 + 路径白名单。

**主安全边界在这里，不在沙箱。** 沙箱只是让模型不能自己 `open()`；真正决定
"能不能动这个文件"的是这里的 ``resolve_safe``。所以路径校验必须做满 6 步，
少一步就是个洞。

**风险分级**：

- ``safe``    读系统信息、读白名单内的文件、调音量、发通知 —— 只记日志
- ``confirm`` 写文件、启动程序、切窗口、读剪贴板、截图、锁屏 —— 每次问
- ``danger``  白名单外路径、非白名单程序、列进程 —— 直接拒，**永不支持"本会话都允许"**
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import time
from dataclasses import dataclass
from typing import Callable

from .. import config
from . import win32

SAFE, CONFIRM, DANGER = "safe", "confirm", "danger"

# ---- 永久拒绝：这些文件里有 API Key、有记忆、有日志 ----
DENY_NAMES = {
    "config.json", "memory.json", "memory.json.bak", "agent_log.jsonl",
    "agent_apps.json", "history.json", "error.log", ".workbuddy-ai",
}
DENY_DIR_PARTS = {"windows", "program files", "program files (x86)",
                  "programdata", "system32", "syswow64", "$recycle.bin"}

# ---- 写模式额外拒绝的扩展名 ----
WRITE_EXT_DENY = {
    ".exe", ".dll", ".sys", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".lnk", ".reg", ".msi", ".scr", ".com", ".pif",
    ".hta", ".cpl", ".jar", ".py", ".pyw",
}

RESERVED_NAMES = {"con", "prn", "aux", "nul",
                  *(f"com{i}" for i in range(1, 10)),
                  *(f"lpt{i}" for i in range(1, 10))}

# ---- app_launch 硬拒绝（LOLBins：这些都是"合法系统程序"，但拿来做坏事） ----
LOLBINS = {
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "wscript", "wscript.exe", "cscript", "cscript.exe", "mshta", "mshta.exe",
    "regedit", "regedit.exe", "reg", "reg.exe", "rundll32", "rundll32.exe",
    "certutil", "certutil.exe", "bitsadmin", "bitsadmin.exe",
    "schtasks", "schtasks.exe", "wmic", "wmic.exe", "taskkill", "taskkill.exe",
    "diskpart", "diskpart.exe", "netsh", "netsh.exe", "sc", "sc.exe",
    "at", "at.exe", "curl", "curl.exe", "ftp", "ftp.exe", "telnet",
}

# 内置的常用程序（开始菜单扫不到时的兜底）
BUILTIN_APPS = {
    "记事本": "notepad.exe", "notepad": "notepad.exe",
    "计算器": "calc.exe", "calc": "calc.exe",
    "画图": "mspaint.exe", "mspaint": "mspaint.exe",
    "资源管理器": "explorer.exe", "explorer": "explorer.exe",
    "任务管理器": "taskmgr.exe", "taskmgr": "taskmgr.exe",
    "控制面板": "control.exe", "control": "control.exe",
}


# ==========================================================================
# 路径白名单
# ==========================================================================
def _known_folders() -> list[str]:
    """桌面 / 文档 / 下载。中文 Windows 目录名是中文，两种都试。"""
    home = os.path.expanduser("~")
    out = []
    for names in (("Desktop", "桌面"), ("Documents", "文档"),
                  ("Downloads", "下载")):
        for n in names:
            p = os.path.join(home, n)
            if os.path.isdir(p):
                out.append(p)
                break
    return out


def allowed_roots(cfg: dict | None = None) -> list[str]:
    cfg = cfg or {}
    roots = [config.agent_workspace_dir()]
    roots += _known_folders()
    for extra in (cfg.get("agent_allow_dirs") or []):
        if isinstance(extra, str) and extra.strip():
            roots.append(os.path.expandvars(os.path.expanduser(extra.strip())))
    out: list[str] = []
    for r in roots:
        try:
            out.append(os.path.normcase(os.path.realpath(os.path.abspath(r))))
        except Exception:
            continue
    return out


def resolve_safe(path: str, cfg: dict | None = None, write: bool = False) -> str:
    """6 步校验。任何一步不过就抛 ``PermissionError``（文案可直接给模型看）。"""
    raw = (path or "").strip().strip('"').strip("'")
    if not raw:
        raise PermissionError("路径是空的")
    if "\x00" in raw:
        raise PermissionError("路径里有非法字符")

    expanded = os.path.expandvars(os.path.expanduser(raw))

    # 1) 拒绝 NTFS 备用数据流（a.txt:evil）
    body = expanded[2:] if re.match(r"^[A-Za-z]:", expanded) else expanded
    if ":" in body:
        raise PermissionError("路径里不允许出现冒号（可能是备用数据流）")

    # 2) 拒绝保留设备名
    stem = os.path.splitext(os.path.basename(expanded))[0].strip().lower()
    if stem in RESERVED_NAMES:
        raise PermissionError(f"{stem} 是系统保留名，不能用")

    # 3) 绝对化 + 统一大小写
    full = os.path.normcase(os.path.abspath(expanded))
    if len(full) > 240:
        raise PermissionError("路径太长（超过 240 字符）")

    # 4) realpath 打掉 junction / 符号链接逃逸
    try:
        real = os.path.normcase(os.path.realpath(full))
    except Exception:
        real = full

    # 5) 永久拒绝清单
    base = os.path.basename(real)
    if base in DENY_NAMES:
        raise PermissionError(f"{base} 是受保护文件，不允许读写")
    parts = set(p for p in re.split(r"[\\/]+", real) if p)
    if parts & DENY_DIR_PARTS:
        raise PermissionError("系统目录不允许访问")

    # 6) 必须在白名单根目录之内
    ok = False
    for root in allowed_roots(cfg):
        try:
            if os.path.commonpath([root, real]) == root:
                ok = True
                break
        except Exception:
            continue
    if not ok:
        raise PermissionError(
            "这个路径不在允许范围内。只能操作工作区、桌面、文档、下载里的文件")

    if write:
        ext = os.path.splitext(real)[1].lower()
        if ext in WRITE_EXT_DENY:
            raise PermissionError(f"不允许写 {ext} 这类可执行文件")
    return real


# ==========================================================================
# 工具规格
# ==========================================================================
@dataclass
class ToolSpec:
    name: str
    sig: str
    desc: str
    risk: str
    fn: Callable


def _mask_secret(text: str) -> str:
    t = text or ""
    t = re.sub(r"(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+", r"\1***", t)
    t = re.sub(r"(AKIA[0-9A-Z]{4})[0-9A-Z]{12}", r"\1********", t)
    return t


class Toolbox:
    """一次任务一个实例。``_win_map`` 只在本次任务内有效。"""

    def __init__(self, get_cfg, *, stop_event=None,
                 on_notify: Callable[[str], None] | None = None):
        self._get_cfg = get_cfg or (lambda: {})
        self.stop_event = stop_event
        self.on_notify = on_notify
        self._win_map: dict[int, dict] = {}
        self._next_win_id = 1
        self.finished: str | None = None
        self.terminated: str | None = None
        self.pending_question: str | None = None
        self.screenshots: list[str] = []
        self.specs: dict[str, ToolSpec] = {}
        self._register_all()

    # ---------- 配置 ----------
    def cfg(self) -> dict:
        try:
            return self._get_cfg() or {}
        except Exception:
            return {}

    # ---------- 注册 ----------
    def _add(self, name: str, sig: str, desc: str, risk: str, fn) -> None:
        self.specs[name] = ToolSpec(name, sig, desc, risk, fn)

    def _register_all(self) -> None:
        A = self._add
        A("sys_info", "sys_info()", "系统概况：CPU 核数、内存、磁盘剩余、屏幕尺寸。不含进程名", SAFE, self.sys_info)
        A("top_processes", "top_processes(n=5)",
          "占用 CPU 最高的进程名（默认禁用，涉及隐私）", DANGER, self.top_processes)
        A("volume_get", "volume_get()", "当前音量（0-100）和是否静音", SAFE, self.volume_get)
        A("volume_set", "volume_set(pct)", "设置音量，pct 取 0-100", SAFE, self.volume_set)
        A("brightness_get", "brightness_get()", "当前屏幕亮度（只对笔记本内屏可靠）", SAFE, self.brightness_get)
        A("brightness_set", "brightness_set(pct)", "设置屏幕亮度，pct 取 0-100", SAFE, self.brightness_set)
        A("lock_screen", "lock_screen()", "锁定电脑", CONFIRM, self.lock_screen)
        A("file_read", "file_read(path, max_chars=4000)",
          "读一个文本文件。只能读工作区/桌面/文档/下载里的", SAFE, self.file_read)
        A("file_write", "file_write(path, content)",
          "写文件（自动备份 .bak，写完会读回校验）", CONFIRM, self.file_write)
        A("file_list", "file_list(path='', depth=1)",
          "列目录。不传 path 就列工作区", SAFE, self.file_list)
        A("app_launch", "app_launch(name)", "按名字启动程序（如 记事本 / 微信）", CONFIRM, self.app_launch)
        A("open_url", "open_url(url)", "用默认浏览器打开 http/https 网址", CONFIRM, self.open_url)
        A("clipboard_get", "clipboard_get()", "读剪贴板文字（密钥会自动打码）", CONFIRM, self.clipboard_get)
        A("clipboard_set", "clipboard_set(text)", "把文字写进剪贴板", CONFIRM, self.clipboard_set)
        A("windows_list", "windows_list()", "列出当前可见的窗口，返回带 id 的列表", CONFIRM, self.windows_list)
        A("window_activate", "window_activate(win_id)",
          "切到某个窗口。win_id 必须来自**最近一次** windows_list()", CONFIRM, self.window_activate)
        A("screenshot_save", "screenshot_save(name='shot')",
          "截屏存成 BMP 到工作区。只写盘，不返回图片内容", CONFIRM, self.screenshot_save)
        A("notify", "notify(text)", "在桌宠气泡上说一句话给主人看", SAFE, self.notify)
        A("wait", "wait(seconds)", "等待若干秒（最多 10 秒），用于等程序启动", SAFE, self.wait)
        A("ask_user", "ask_user(question)",
          "向主人提问。**不返回答案**，提问后本次执行会暂停，答案在下一步给你", SAFE, self.ask_user)
        A("finish", "finish(summary)", "任务完成。必须调用，summary 是一句话结论", SAFE, self.finish)
        A("terminate", "terminate(reason)", "做不下去时调用，说明原因", SAFE, self.terminate)

    # ---------- 对外 ----------
    def functions(self) -> dict:
        """注入沙箱的函数表。danger 级工具按配置决定给不给。"""
        cfg = self.cfg()
        out = {}
        for name, spec in self.specs.items():
            if name == "top_processes" and not cfg.get("agent_allow_process_list"):
                continue
            out[name] = spec.fn
        return out

    def specs_text(self) -> str:
        cfg = self.cfg()
        rows = []
        for name, spec in self.specs.items():
            if name == "top_processes" and not cfg.get("agent_allow_process_list"):
                continue
            rows.append(f"- {spec.sig}   [{spec.risk}]  {spec.desc}")
        return "\n".join(rows)

    def risk_of(self, name: str) -> str:
        spec = self.specs.get(name)
        return spec.risk if spec else DANGER

    # ======================================================================
    # 实现
    # ======================================================================
    def sys_info(self) -> dict:
        info = {
            "os": platform.platform(terse=True),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "user": os.environ.get("USERNAME", ""),
            "workspace": config.agent_workspace_dir(),
        }
        try:
            import psutil
            vm = psutil.virtual_memory()
            info["ram_total_gb"] = round(vm.total / 1024 ** 3, 1)
            info["ram_used_pct"] = round(vm.percent, 0)
            try:
                du = shutil.disk_usage(os.path.expanduser("~"))
                info["disk_free_gb"] = round(du.free / 1024 ** 3, 1)
            except Exception:
                pass
        except Exception:
            pass
        try:
            info["screen"] = f"{win32.screen_size()[0]}x{win32.screen_size()[1]}"
        except Exception:
            pass
        return info

    def top_processes(self, n: int = 5) -> list[dict]:
        if not self.cfg().get("agent_allow_process_list"):
            raise PermissionError("进程列表默认关闭（涉及隐私），需要主人在设置里打开")
        import psutil
        rows = []
        for p in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
            try:
                rows.append({"name": (p.info.get("name") or "")[:40],
                             "cpu": round(p.info.get("cpu_percent") or 0, 1)})
            except Exception:
                continue
        rows.sort(key=lambda r: -r["cpu"])
        return rows[:max(1, min(20, int(n)))]

    def volume_get(self) -> dict:
        try:
            return win32.volume_get()
        except Exception as exc:
            raise RuntimeError(f"读不到音量：{exc}") from None

    def volume_set(self, pct: int) -> dict:
        try:
            return win32.volume_set(int(pct))
        except Exception:
            # 兜底：按音量键。一次约 2%，所以最多按 50 下
            try:
                cur = 50
                steps = abs(int(pct) - cur) // 2
                vk = win32._VK_VOLUME_UP if int(pct) > cur else win32._VK_VOLUME_DOWN
                win32._press_vk(vk, steps)
                return {"volume": int(pct), "fallback": "keybd"}
            except Exception as exc2:
                raise RuntimeError(f"调不了音量：{exc2}") from None

    def brightness_get(self) -> dict:
        try:
            return win32.brightness_get()
        except Exception as exc:
            raise RuntimeError(str(exc)) from None

    def brightness_set(self, pct: int) -> dict:
        try:
            return win32.brightness_set(int(pct))
        except Exception as exc:
            raise RuntimeError(str(exc)) from None

    def lock_screen(self) -> dict:
        return win32.lock_screen()

    # ---------- 文件 ----------
    def file_read(self, path: str, max_chars: int = 4000) -> dict:
        full = resolve_safe(path, self.cfg())
        if not os.path.isfile(full):
            raise FileNotFoundError(f"没有这个文件：{os.path.basename(full)}")
        size = os.path.getsize(full)
        if size > 4 * 1024 * 1024:
            raise ValueError(f"文件太大（{size // 1024}KB），只支持 4MB 以内")
        with open(full, "rb") as f:
            head = f.read(4096)
        if b"\x00" in head:
            raise ValueError("这是二进制文件，读不了")
        try:
            with open(full, "r", encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError:
            with open(full, "r", encoding="gbk", errors="replace") as f:
                text = f.read()
        limit = max(200, min(20000, int(max_chars)))
        truncated = len(text) > limit
        return {"path": full, "chars": len(text), "truncated": truncated,
                "content": text[:limit]}

    def file_write(self, path: str, content: str) -> dict:
        full = resolve_safe(path, self.cfg(), write=True)
        text = content if isinstance(content, str) else str(content)
        if len(text) > 2 * 1024 * 1024:
            raise ValueError("内容太大（超过 2MB）")

        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        # 备份：三代轮转 .bak → .bak1 → .bak2，旧的直接删
        if os.path.exists(full):
            try:
                if os.path.exists(f"{full}.bak2"):
                    os.remove(f"{full}.bak2")
                if os.path.exists(f"{full}.bak1"):
                    os.replace(f"{full}.bak1", f"{full}.bak2")
                if os.path.exists(f"{full}.bak"):
                    os.replace(f"{full}.bak", f"{full}.bak1")
                shutil.copy2(full, f"{full}.bak")
            except Exception:
                pass

        tmp = full + ".tmp-agent"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, full)

        # 写后读回校验 —— "回执 ok ≠ 办成"
        with open(full, "r", encoding="utf-8") as f:
            back = f.read()
        if back != text:
            raise IOError("写完读回来内容不一致，可能被别的程序改过")
        return {"path": full, "chars": len(text), "verified": True}

    def file_list(self, path: str = "", depth: int = 1) -> list[dict]:
        base = resolve_safe(path, self.cfg()) if path else config.agent_workspace_dir()
        if not os.path.isdir(base):
            raise NotADirectoryError(f"不是目录：{os.path.basename(base)}")
        depth = max(1, min(2, int(depth)))
        out: list[dict] = []

        def walk(d: str, level: int) -> None:
            if len(out) >= 200:
                return
            try:
                entries = sorted(os.scandir(d), key=lambda e: (not e.is_dir(), e.name))
            except Exception:
                return
            for e in entries:
                if len(out) >= 200:
                    return
                try:
                    is_dir = e.is_dir()
                    out.append({
                        "name": e.name[:60],
                        "path": e.path,
                        "dir": is_dir,
                        "size": 0 if is_dir else e.stat().st_size,
                    })
                except Exception:
                    continue
                if is_dir and level < depth:
                    walk(e.path, level + 1)

        walk(base, 1)
        return out

    # ---------- 程序 / 网址 ----------
    def app_launch(self, name: str) -> dict:
        raw = (name or "").strip()
        if not raw:
            raise ValueError("没说要启动什么")
        low = raw.lower()
        base = os.path.basename(low)
        if base in LOLBINS:
            raise PermissionError(f"{base} 是系统管理工具，不允许从这里启动")

        # 1) 直接给了路径
        if os.sep in raw or "/" in raw or low.endswith(".exe"):
            full = resolve_safe(raw, self.cfg())
            if os.path.splitext(full)[1].lower() != ".exe":
                raise PermissionError("只能启动 .exe")
            return win32.shell_execute(full)

        # 2) 内置表
        if low in BUILTIN_APPS:
            return win32.shell_execute(BUILTIN_APPS[low])

        # 3) 开始菜单
        links = win32.start_menu_links()
        hit = links.get(low)
        if not hit:
            for k, v in links.items():
                if low in k or k in low:
                    hit = v
                    break
        if hit:
            return win32.shell_execute(hit)
        raise LookupError(f"没找到「{raw}」。可以试试完整路径，或者用 windows_list 看看它开没开")

    def open_url(self, url: str) -> dict:
        u = (url or "").strip()
        if not re.match(r"^https?://", u, re.I):
            raise PermissionError("只允许 http/https 网址")
        if len(u) > 2000:
            raise ValueError("网址太长")
        return win32.shell_execute(u, wait=0.3)

    # ---------- 剪贴板 ----------
    def clipboard_get(self) -> dict:
        text = win32.clipboard_get()
        return {"chars": len(text), "text": _mask_secret(text)[:2000]}

    def clipboard_set(self, text: str) -> dict:
        data = text if isinstance(text, str) else str(text)
        if len(data) > 100_000:
            raise ValueError("内容太长（超过 10 万字符）")
        win32.clipboard_set(data)
        back = win32.clipboard_get()
        if back != data:
            raise IOError("写进剪贴板后读回来不一致")
        return {"ok": True, "chars": len(data)}

    # ---------- 窗口 ----------
    def windows_list(self) -> list[dict]:
        rows = win32.windows_list()
        self._win_map = {}
        self._next_win_id = 1
        out = []
        for r in rows:
            wid = self._next_win_id
            self._next_win_id += 1
            self._win_map[wid] = r
            out.append({"id": wid, "title": r["title"],
                        "size": f"{r['w']}x{r['h']}"})
        return out

    def window_activate(self, win_id: int) -> dict:
        try:
            wid = int(win_id)
        except Exception:
            raise ValueError("win_id 要是数字") from None
        if wid not in self._win_map:
            raise LookupError("这个 id 已经失效了，请重新调用 windows_list()")
        target = self._win_map[wid]
        res = win32.window_activate(target["hwnd"])
        return {"id": wid, "title": target["title"], "ok": res["ok"]}

    # ---------- 截图 ----------
    def screenshot_save(self, name: str = "shot") -> dict:
        safe_name = re.sub(r"[^\w\u4e00-\u9fff-]", "_", (name or "shot"))[:40]
        if not safe_name.lower().endswith(".bmp"):
            safe_name += ".bmp"
        path = os.path.join(config.shots_dir(), safe_name)
        res = win32.screenshot_save(path)
        self.screenshots.append(path)
        return {"path": path, "w": res["w"], "h": res["h"],
                "note": "只保存到磁盘，内容不会进上下文"}

    # ---------- 杂项 ----------
    def notify(self, text: str) -> dict:
        msg = (text or "").strip()[:120]
        if not msg:
            return {"ok": False}
        if self.on_notify:
            try:
                self.on_notify(msg)
            except Exception:
                pass
        return {"ok": True}

    def wait(self, seconds: float) -> dict:
        sec = max(0.0, min(10.0, float(seconds)))
        end = time.monotonic() + sec
        while time.monotonic() < end:
            if self.stop_event is not None and self.stop_event.is_set():
                break
            time.sleep(0.05)
        return {"waited": round(sec, 1)}

    def ask_user(self, question: str) -> dict:
        q = (question or "").strip()[:200]
        if not q:
            raise ValueError("问题不能是空的")
        self.pending_question = q
        return {"asked": q, "note": "已提问。本次执行会暂停，主人回答后你会在下一步看到"}

    def finish(self, summary: str = "") -> dict:
        self.finished = (summary or "完成了").strip()[:200]
        return {"finished": True, "summary": self.finished}

    def terminate(self, reason: str = "") -> dict:
        self.terminated = (reason or "做不下去").strip()[:200]
        return {"terminated": True, "reason": self.terminated}
