# -*- coding: utf-8 -*-
"""端到端验证：起一个本地假 OpenAI 服务器，把 llm.py 的所有分支都跑一遍。

用法：python tools/test_llm.py
不依赖真实 API Key，也不会联网。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm  # noqa: E402

PORT = 18765
CALLS: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/user/balance"):
            ok = self.headers.get("Authorization") == "Bearer good-key"
            if not ok:
                return self._send(401, {"error": {"message": "invalid key"}})
            return self._send(200, {"is_available": True,
                                    "balance_infos": [{"currency": "CNY",
                                                       "total_balance": "42.50"}]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            payload = {}
        CALLS.append({"path": self.path,
                      "auth": self.headers.get("Authorization"),
                      "payload": payload})

        if not self.path.endswith("/chat/completions"):
            return self._send(404, {"error": {"message": "unknown route"}})
        if "/wrong" in self.path:
            return self._send(404, {"error": {"message": "unknown route"}})
        if self.headers.get("Authorization") != "Bearer good-key" and \
                self.headers.get("Authorization") is not None:
            return self._send(401, {"error": {"message": "bad key"}})
        if payload.get("model") == "nope":
            return self._send(400, {"error": {
                "message": "The model `nope` does not exist", "type": "invalid_request_error"}})
        # 模拟只认 max_completion_tokens 的推理模型
        if payload.get("model") == "reasoner":
            if "max_tokens" in payload:
                return self._send(400, {"error": {
                    "message": "Unsupported parameter: 'max_tokens' is not supported with this model. "
                               "Use 'max_completion_tokens' instead."}})
        if payload.get("model") == "boom":
            return self._send(503, {"error": {"message": "overloaded"}})
        # 正常返回
        return self._send(200, {"choices": [{"message": {
            "role": "assistant",
            "content": "在呢，主人又在摸鱼是吧"}}]})


def start() -> HTTPServer:
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _balance_against_local(cb, base, key):
    """query_balance 只做 DeepSeek 判定，这里临时把判定和 URL 都指向本地。"""
    old_is, old_url = llm.is_deepseek, llm.balance_url
    llm.is_deepseek = lambda b: True
    llm.balance_url = lambda b: base + "/user/balance"
    try:
        llm.query_balance({"base_url": base, "api_key": key, "model": "x", "timeout": 5},
                          lambda t, a: cb(t), lambda e: cb("ERR:" + e))
    finally:
        llm.is_deepseek, llm.balance_url = old_is, old_url


def wait(pred, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


def main() -> int:
    srv = start()
    base = f"http://127.0.0.1:{PORT}"
    results: list[tuple[str, bool, str]] = []

    def run(name, fn, expect_ok=True, timeout=8.0):
        box = {}

        def done(*args):
            box["args"] = args

        fn(done)
        ok = wait(lambda: "args" in box, timeout)
        got = box.get("args")
        if not ok:
            results.append((name, False, "超时无回调"))
            return None
        good = (len(got) >= 1) if expect_ok else True
        results.append((name, good, str(got)))
        return got

    # ---- 1. 正常对话（无 Key 的本地服务）----
    got = run("本地无 Key 对话",
              lambda cb: llm.Chat().ask({"base_url": base, "api_key": "", "model": "m1",
                                         "timeout": 5}, "在吗", lambda r: cb(r),
                                        lambda e: cb("ERR:" + e)))
    results[-1] = ("本地无 Key 对话", bool(got and got[0].startswith("在呢")), str(got))

    # ---- 2. 带 Key 正常 ----
    got = run("带 Key 对话",
              lambda cb: llm.Chat().ask({"base_url": base, "api_key": "good-key",
                                         "model": "m1", "timeout": 5}, "hi",
                                        lambda r: cb(r), lambda e: cb("ERR:" + e)))
    results[-1] = ("带上正确 Key 对话", bool(got and got[0].startswith("在呢")), str(got))

    # ---- 3. 错误 Key -> 401 文案 ----
    got = run("错误 Key",
              lambda cb: llm.Chat().ask({"base_url": base, "api_key": "bad-key",
                                         "model": "m1", "timeout": 5}, "hi",
                                        lambda r: cb("OK:" + r),
                                        lambda e: cb("ERR:" + e)))
    results[-1] = ("错误 Key → 401 友好提示",
                   bool(got and "Key 不对" in got[0]), str(got))

    # ---- 4. 模型名错 -> 400 文案 ----
    got = run("模型名错",
              lambda cb: llm.Chat().ask({"base_url": base, "api_key": "good-key",
                                         "model": "nope", "timeout": 5}, "hi",
                                        lambda r: cb("OK:" + r),
                                        lambda e: cb("ERR:" + e)))
    results[-1] = ("模型名错 → 提示检查 model",
                   bool(got and "模型名不对" in got[0]), str(got))

    # ---- 5. 参数降级：reasoner 第一次 400，自动换 max_completion_tokens ----
    before = len(CALLS)
    got = run("参数自动降级",
              lambda cb: llm.Chat().ask({"base_url": base, "api_key": "good-key",
                                         "model": "reasoner", "timeout": 5}, "hi",
                                        lambda r: cb(r), lambda e: cb("ERR:" + e)))
    new_calls = CALLS[before:]
    retried = len(new_calls) == 2 and "max_completion_tokens" in new_calls[-1]["payload"]
    results[-1] = ("temperature/max_tokens 自动降级重试",
                   bool(retried and got and got[0].startswith("在呢")),
                   f"请求 {len(new_calls)} 次，末次含 max_completion_tokens={retried}")

    # ---- 6. 5xx ----
    got = run("服务端 5xx",
              lambda cb: llm.Chat().ask({"base_url": base, "api_key": "good-key",
                                         "model": "boom", "timeout": 5}, "hi",
                                        lambda r: cb("OK:" + r),
                                        lambda e: cb("ERR:" + e)))
    results[-1] = ("503 → 服务端抽风提示",
                   bool(got and "503" in got[0]), str(got))

    # ---- 7. Base URL 写错（404）----
    got = run("地址写错",
              lambda cb: llm.Chat().ask({"base_url": "http://127.0.0.1:18765/wrong",
                                         "api_key": "", "model": "m1", "timeout": 5}, "hi",
                                        lambda r: cb("OK:" + r),
                                        lambda e: cb("ERR:" + e)))
    results[-1] = ("Base URL 错 → 提示检查地址",
                   bool(got and "地址不对" in got[0]), str(got))

    # ---- 8. 没配地址 ----
    got = run("未配置",
              lambda cb: llm.Chat().ask({"base_url": "", "api_key": "", "model": "",
                                         "timeout": 5}, "hi",
                                        lambda r: cb("OK:" + r),
                                        lambda e: cb("ERR:" + e)))
    results[-1] = ("未配置 → 引导去设置",
                   bool(got and "模型设置" in got[0]), str(got))

    # ---- 9. 余额查询（DeepSeek 域名判定走不通，直接测底层）----
    got = run("余额解析",
              lambda cb: _balance_against_local(cb, base, "good-key"),
              timeout=6)
    results[-1] = ("余额接口解析 42.50 CNY",
                   bool(got and got[0] == "¥42.50"), str(got))

    got = run("余额非 DeepSeek",
              lambda cb: llm.query_balance({"base_url": "https://api.openai.com/v1",
                                            "api_key": "k", "model": "x", "timeout": 5},
                                           lambda t, a: cb(t, a),
                                           lambda e: cb("ERR:" + e)),
              timeout=3)
    results[-1] = ("非 DeepSeek 地址拒绝查余额",
                   bool(got and str(got[0]).startswith("ERR:") and "查不了" in str(got[0])),
                   str(got))

    # ---- 10. ping 正常 ----
    got = run("ping 正常",
              lambda cb: llm.ping({"base_url": base, "api_key": "", "model": "m1",
                                   "timeout": 5}, lambda m: cb(m), lambda e: cb("ERR:" + e)))
    results[-1] = ("测试连接成功", bool(got and got[0].startswith("连上了")), str(got))

    # ---- 11. ping 失败 ----
    got = run("ping 失败",
              lambda cb: llm.ping({"base_url": base, "api_key": "bad", "model": "m1",
                                   "timeout": 5}, lambda m: cb("OK:" + m),
                                  lambda e: cb("ERR:" + e)))
    results[-1] = ("测试连接失败被捕获", bool(got and str(got[0]).startswith("ERR:")), str(got))

    # ---- 12. 请求体是否带上了 system prompt 和 model ----
    chat_calls = [c for c in CALLS if c["payload"].get("messages")
                  and len(c["payload"]["messages"]) > 1]
    has_sys = bool(chat_calls) and all(
        any(m.get("role") == "system" for m in c["payload"]["messages"]) for c in chat_calls)
    models = {c["payload"].get("model") for c in chat_calls}
    results.append(("请求体含 system + model",
                    has_sys and "m1" in models,
                    f"models={sorted(models)} system={has_sys}"))

    # ---- 13. 鉴权头 ----
    auth_ok = any(c["auth"] == "Bearer good-key" for c in CALLS)
    results.append(("Authorization 头正确", auth_ok, "Bearer good-key 已发送" if auth_ok else "缺"))

    srv.shutdown()

    print("=" * 68)
    passed = 0
    for name, ok, detail in results:
        print(("  PASS  " if ok else "  FAIL  ") + name.ljust(30) + "  " + detail[:60])
        passed += bool(ok)
    print("=" * 68)
    print(f"{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
