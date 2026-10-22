# -*- coding: utf-8 -*-
"""给真机联调用的假 OpenAI 服务。

和 ``_fake_llm.py`` 里的 ``FakeLLM`` 不同：那个是给进程内测试用的，
这个是**独立跑**的，配合 ``adb reverse tcp:8899 tcp:8899`` 让手机上的
App 以为自己在访问 localhost:8899，从而在没有任何真实 Key 的情况下
把「设置 → 聊天 → 记忆抽取」整条链路跑通。

用法::

    python tools/fake_llm_server.py            # 监听 127.0.0.1:8899
    adb reverse tcp:8899 tcp:8899

然后在 App 设置里填：Base URL = http://localhost:8899/v1
                    API Key  = test
                    模型名    = fake

它会把每个请求体打印出来（含 messages 条数和最后一条用户消息），
所以能直接看出 App 有没有正确带上记忆块。
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8899

# 聊天回复：轮换，避免每次都是同一句
REPLIES = [
    "在的在的",
    "你又来啦",
    "说嘛，我听着",
    "这事儿我记下了",
]

# 记忆抽取的回复：模拟一个正常模型会抽出来的东西
MEMORY_JSON = {
    "profile": {
        "call_me": "阿伟",
        "occupation": "程序员",
        "likes": ["咖啡"],
        "dislikes": ["加班"],
    },
    "facts": ["生日是 3 月 12 号"],
    "episodes": [{"kind": "preference", "text": "在学 Flutter"}],
}

_counter = {"chat": 0, "extract": 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 关掉默认的逐请求日志，太吵
        pass

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        """读请求体。兼容 Content-Length 和 chunked 两种传输编码。

        为什么两种都要支持：dart:io 的 HttpClient 在不显式设 contentLength
        时会用 chunked，而很多手写的测试服务只认 Content-Length，
        表现就是「App 发了请求，服务回 400 bad json」。
        """
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            buf = bytearray()
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                size = int(line.split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()  # 吃掉结尾的空行
                    break
                buf.extend(self.rfile.read(size))
                self.rfile.read(2)  # CRLF
            return bytes(buf)
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self):  # noqa: N802
        raw = self._read_body().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except Exception:
            self._send(400, {"error": {"message": "bad json"}})
            return

        msgs = payload.get("messages") or []
        joined = "\n".join(str(m.get("content", "")) for m in msgs)

        # 抽取请求的特征：system 里写着「记忆整理器」
        is_extract = "记忆整理器" in joined
        if is_extract:
            _counter["extract"] += 1
            print(f"[extract #{_counter['extract']}] messages={len(msgs)}")
            self._send(200, {
                "choices": [{"message": {
                    "role": "assistant",
                    "content": json.dumps(MEMORY_JSON, ensure_ascii=False),
                }}]
            })
            return

        _counter["chat"] += 1
        last_user = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                last_user = str(m.get("content", ""))
                break
        has_memory_block = any(
            "主人的资料" in str(m.get("content", "")) for m in msgs)
        print(f"[chat #{_counter['chat']}] messages={len(msgs)} "
              f"记忆块={'有' if has_memory_block else '无'} "
              f"用户={last_user[:20]}")
        reply = REPLIES[(_counter["chat"] - 1) % len(REPLIES)]
        self._send(200, {
            "choices": [{"message": {"role": "assistant", "content": reply}}]
        })


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"假 OpenAI 服务已启动: http://127.0.0.1:{port}/v1")
    print(f"手机侧配 Base URL = http://localhost:{port}/v1")
    print("按 Ctrl+C 结束")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
