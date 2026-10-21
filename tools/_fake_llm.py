# -*- coding: utf-8 -*-
"""测试基建：本地假 OpenAI 服务 + 数据目录沙盒。

两个东西，所有新测试都基于它们：

- ``FakeLLM``     起一个本地 HTTP 服务，按脚本返回预设回复，记录每次请求体。
                  完全不联网，不需要真实 Key。
- ``DataSandbox`` 把 ``config.data_dir()`` 指到临时目录，测试怎么写都碰不到
                  ``%APPDATA%/DafeiyuPet`` 里的真实配置、记忆和聊天记录。

用法::

    with DataSandbox() as sb, FakeLLM(["好的呀"]) as fake:
        llm = {"base_url": fake.base_url, "api_key": "k", "model": "m1", "timeout": 5}
        ...调你的代码...
        assert fake.calls[-1]["model"] == "m1"
        assert os.path.exists(sb.path("memory.json"))

为什么要这么小心：早前有一次测试模拟点「保存」，把用户真实的 DeepSeek Key
写成了空串，靠翻会话记录才找回来。**任何会写盘的测试都必须套 DataSandbox。**
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# --------------------------------------------------------------------------
# 数据目录沙盒
# --------------------------------------------------------------------------
class DataSandbox:
    """把整个数据目录搬到临时目录。

    ``config`` 里的 ``CONFIG_PATH`` / ``HISTORY_PATH`` 是导入时算好的模块级常量，
    新模块则统一用 ``config.memory_path()`` 这类函数在调用时算。所以这里既要
    替换函数，也要重算那两个常量。
    """

    def __init__(self, seed: bool = False):
        # seed=True 时把真实配置复制进来，适合需要"沿用用户设置"的测试
        self.seed = seed
        self._tmpdir: str | None = None
        self._orig: dict = {}

    # -- 上下文管理 ------------------------------------------------------
    def __enter__(self) -> "DataSandbox":
        from app import config

        self._tmpdir = tempfile.mkdtemp(prefix="dafeiyu_data_")
        self._orig = {
            "data_dir": config.data_dir,
            "CONFIG_PATH": config.CONFIG_PATH,
            "HISTORY_PATH": config.HISTORY_PATH,
        }

        if self.seed and os.path.exists(config.CONFIG_PATH):
            try:
                shutil.copy2(config.CONFIG_PATH, os.path.join(self._tmpdir, "config.json"))
            except Exception:
                pass

        tmp = self._tmpdir
        config.data_dir = lambda: tmp  # type: ignore[assignment]
        config.CONFIG_PATH = os.path.join(tmp, "config.json")
        config.HISTORY_PATH = os.path.join(tmp, "chat_history.json")
        return self

    def __exit__(self, *exc):
        from app import config

        if self._orig:
            config.data_dir = self._orig["data_dir"]  # type: ignore[assignment]
            config.CONFIG_PATH = self._orig["CONFIG_PATH"]
            config.HISTORY_PATH = self._orig["HISTORY_PATH"]
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        return False

    # -- 便捷方法 --------------------------------------------------------
    @property
    def root(self) -> str:
        assert self._tmpdir, "DataSandbox 还没进入"
        return self._tmpdir

    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def read_json(self, name: str):
        with open(self.path(name), "r", encoding="utf-8") as f:
            return json.load(f)

    def write_json(self, name: str, data) -> str:
        p = self.path(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return p

    def ls(self) -> list[str]:
        try:
            return sorted(os.listdir(self.root))
        except FileNotFoundError:
            return []


# --------------------------------------------------------------------------
# 假 OpenAI 服务
# --------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # 由 FakeLLM 在 start() 时挂上去
    owner: "FakeLLM" = None  # type: ignore[assignment]

    def log_message(self, *a):  # 别往 stderr 刷
        pass

    def _send(self, status: int, obj) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"__raw__": body.decode("utf-8", "replace")}

        owner = type(self).owner
        owner.record(self.path, payload)

        # 路由：/chat/completions 或 /user/balance
        if self.path.rstrip("/").endswith("/user/balance"):
            status, obj = owner.next_balance()
        elif "/wrong" in self.path:
            status, obj = 404, {"error": {"message": "unknown route"}}
        elif self.path.rstrip("/").endswith("/chat/completions"):
            status, obj = owner.next_chat(payload)
        else:
            status, obj = 404, {"error": {"message": "unknown route"}}

        self._send(status, obj)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"ok": True})


def _ok_reply(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _err_reply(status: int, message: str) -> dict:
    return {"error": {"message": message, "type": "fake_error", "code": status}}


class FakeLLM:
    """本地假 OpenAI 服务。

    ``script`` 是一串"剧本"，每次对话请求消费一条。每条可以是：

    - ``str``               → 200，``choices[0].message.content`` 就是它
    - ``(status, message)`` → 指定状态码的错误响应
    - ``callable(payload)`` → 自己决定返回什么，返回 ``str`` 或 ``(status, obj)``

    剧本用完后重复最后一条（除非 ``repeat=False``，那就一直返回 ``default``）。
    """

    def __init__(self, script=None, port: int = 0, default: str = "好的",
                 repeat: bool = True):
        self.script = list(script or [])
        self.default = default
        self.repeat = repeat
        self.port = port
        self._idx = 0
        self._balance_script: list = []
        self._calls: list[dict] = []
        self._lock = threading.Lock()
        self._srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- 生命周期 --------------------------------------------------------
    def __enter__(self) -> "FakeLLM":
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    def start(self) -> "FakeLLM":
        handler = type("_BoundHandler", (_Handler,), {"owner": self})
        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(
            target=self._srv.serve_forever, daemon=True, name="fake-llm")
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    # -- 访问 ------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def root_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def llm(self, model: str = "m1", api_key: str = "k", timeout: int = 10) -> dict:
        """直接产出一份能喂给 llm.py / agent 的配置字典。"""
        return {"base_url": self.base_url, "api_key": api_key,
                "model": model, "timeout": timeout}

    @property
    def calls(self) -> list[dict]:
        with self._lock:
            return list(self._calls)

    @property
    def payloads(self) -> list[dict]:
        return [c["payload"] for c in self.calls]

    def last_payload(self) -> dict:
        return self.calls[-1]["payload"] if self._calls else {}

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()
            self._idx = 0

    # -- 剧本 ------------------------------------------------------------
    def push(self, *items) -> "FakeLLM":
        """往剧本尾部追加。"""
        self.script.extend(items)
        return self

    def push_balance(self, *items) -> "FakeLLM":
        self._balance_script.extend(items)
        return self

    # -- 供 Handler 调用 -------------------------------------------------
    def record(self, path: str, payload: dict) -> None:
        with self._lock:
            self._calls.append({"path": path, "payload": payload})

    def next_chat(self, payload: dict) -> tuple[int, dict]:
        item = self._pick(self.script)
        if callable(item):
            item = item(payload)
        if item is None:
            return 200, _ok_reply(self.default)
        if isinstance(item, tuple):
            status, msg = item
            return int(status), _err_reply(int(status), str(msg))
        if isinstance(item, dict):
            return 200, item
        return 200, _ok_reply(str(item))

    def next_balance(self) -> tuple[int, dict]:
        item = self._pick(self._balance_script) if self._balance_script else None
        if item is None:
            return 200, {"is_available": True, "balance_infos": [
                {"currency": "CNY", "total_balance": "42.50",
                 "granted_balance": "0.00", "topped_up_balance": "42.50"}]}
        if callable(item):
            item = item({})
        if isinstance(item, tuple):
            status, msg = item
            return int(status), _err_reply(int(status), str(msg))
        return 200, item

    def _pick(self, seq: list):
        with self._lock:
            if not seq:
                return None
            if self._idx < len(seq):
                item = seq[self._idx]
                self._idx += 1
                return item
            return seq[-1] if self.repeat else None


# --------------------------------------------------------------------------
# 简易断言器（各测试脚本共用，输出风格统一）
# --------------------------------------------------------------------------
class Checker:
    def __init__(self, title: str = ""):
        self.title = title
        self.passed = 0
        self.total = 0
        self.failed: list[str] = []

    def __call__(self, name: str, cond, detail="") -> bool:
        self.total += 1
        ok = bool(cond)
        if ok:
            self.passed += 1
        else:
            self.failed.append(name)
        line = f"  {'PASS' if ok else 'FAIL'}  {name}"
        if detail and not ok:
            line += f"   ← {detail}"
        print(line)
        return ok

    def section(self, name: str) -> None:
        print(f"\n=== {name} ===")

    def summary(self) -> int:
        print(f"\n{self.passed}/{self.total}")
        if self.failed:
            print("失败项：" + "、".join(self.failed))
        return 0 if self.passed == self.total else 1
