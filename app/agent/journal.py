# -*- coding: utf-8 -*-
"""操作日志（jsonl）。

**这个文件的每一条都会躺在主人的硬盘上，所以"不记什么"比"记什么"重要。**

不记：文件内容、剪贴板内容、窗口标题、API Key、完整代码。
记：时间、步骤、代码的 sha + 200 字预览、工具名、成功与否、错误摘要。

代码只存 sha 的原因：出问题时要能核对"这一步到底跑的是哪段"，sha 够用；
存全文等于把主人电脑上所有被读写过的内容抄了一份到日志里，那是负债不是资产。
200 字预览是给人看的——出事时能认出"哦这一步是在整理下载目录"就够。

轮转按 2MB，只留一代 ``.1``。桌宠是个常驻程序，日志不能无限长。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

MAX_BYTES = 2 * 1024 * 1024
PREVIEW = 200

# 这些键一旦出现就整条丢掉——宁可日志缺点信息，也不能把内容写进磁盘
DROP_KEYS = {
    "content", "text", "clipboard", "clip", "title", "window_title",
    "api_key", "key", "token", "secret", "password", "prompt",
}


def code_fingerprint(code: str) -> dict:
    """代码只留指纹和预览。"""
    c = code or ""
    flat = " ".join(c.split())
    return {"sha": hashlib.sha1(c.encode("utf-8", "replace")).hexdigest()[:16],
            "len": len(c),
            "lines": c.count("\n") + 1,
            "preview": flat[:PREVIEW]}


def _scrub(fields: dict) -> dict:
    out: dict = {}
    for k, v in (fields or {}).items():
        lk = str(k).lower()
        if lk in DROP_KEYS:
            out[str(k)] = "<已隐去>"
            continue
        if lk == "code":
            out["code"] = code_fingerprint(v if isinstance(v, str) else str(v))
            continue
        if isinstance(v, str):
            out[str(k)] = v[:400]
        elif isinstance(v, (int, float, bool)) or v is None:
            out[str(k)] = v
        elif isinstance(v, (list, tuple)):
            out[str(k)] = [str(x)[:60] for x in list(v)[:10]]
        elif isinstance(v, dict):
            out[str(k)] = {str(a): str(b)[:60] for a, b in list(v.items())[:10]}
        else:
            out[str(k)] = str(v)[:120]
    return out


class Journal:
    """``path`` 传 None 时用 ``config.data_dir()/agent_log.jsonl``。

    延迟取路径（不在 import 时算），否则测试里的 ``DataSandbox`` 来不及改
    ``data_dir``，日志就会写到主人的真实 AppData 里去。
    """

    def __init__(self, path: str | None = None, max_bytes: int = MAX_BYTES):
        self._lock = threading.Lock()
        self.max_bytes = max(64 * 1024, int(max_bytes))
        self.path = path or self._default_path()
        self.error = ""
        self.count = 0
        if self.path:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
            except Exception as exc:
                self.error = f"建目录失败：{exc}"
                self.path = ""

    @staticmethod
    def _default_path() -> str:
        try:
            from .. import config
            return os.path.join(config.data_dir(), "agent_log.jsonl")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    def write(self, event: str, **fields) -> None:
        if not self.path:
            return
        rec = {"t": round(time.time(), 3), "ev": str(event)[:40]}
        rec.update(_scrub(fields))
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            try:
                self._rotate_if_needed(len(line) + 2)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                self.count += 1
            except Exception as exc:      # 日志写不进去不能影响任务本身
                self.error = f"写日志失败：{exc}"

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size + incoming <= self.max_bytes:
            return
        old = self.path + ".1"
        try:
            if os.path.exists(old):
                os.remove(old)
            os.replace(self.path, old)
        except Exception:
            # 轮转失败就别轮了，大不了这一个文件超一点
            pass

    # ------------------------------------------------------------------
    def tail(self, n: int = 50) -> list[dict]:
        """面板上"最近的记录"用。读失败返回空表，不抛。"""
        if not self.path or not os.path.exists(self.path):
            return []
        rows: list[dict] = []
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return rows[-max(1, int(n)):]

    def clear(self) -> None:
        with self._lock:
            for p in (self.path, self.path + ".1"):
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
            self.count = 0

    def size(self) -> int:
        try:
            return os.path.getsize(self.path) if self.path else 0
        except OSError:
            return 0
