# -*- coding: utf-8 -*-
"""通用 OpenAI 兼容补全：不截断、可调 max_tokens。

和 ``app/llm.py`` 里的 ``Chat`` 有什么区别？``Chat`` 是**给气泡用的**——
SYSTEM_PROMPT 限定 25 字，回复再硬截到 40 字（`llm.py:278-280`）。那是产品
需求，不是 bug。但记忆抽取要吐 JSON、Agent 要吐代码块，都不能被截断，所以
另起一层。

复用 ``llm.py`` 里已经调好的东西：``chat_url`` / ``ready`` / ``friendly_error``
/ ``_api_message``。不重复实现错误翻译。
"""
from __future__ import annotations

import json
import re

import requests

from . import llm as _llm


class RawError(RuntimeError):
    """文案已经翻译成人话，可以直接显示。"""


# 有些推理模型不收 temperature / max_tokens，按报错自动降级一次
def _build_payload(llm: dict, messages: list[dict],
                   max_tokens: int, temperature: float) -> dict:
    p = {
        "model": llm["model"],
        "messages": messages,
        "max_tokens": int(max_tokens),
        "stream": False,
    }
    if temperature is not None:
        p["temperature"] = float(temperature)
    return p


def _degrade(payload: dict, detail: str) -> bool:
    """按 400 的报错内容删掉不支持的参数。返回是否改动了。"""
    low = (detail or "").lower()
    changed = False
    if "temperature" in low and "temperature" in payload:
        payload.pop("temperature", None)
        changed = True
    if "max_tokens" in low and "max_completion_tokens" not in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens", 800)
        changed = True
    return changed


def complete(llm: dict, messages: list[dict], *,
             max_tokens: int = 800,
             temperature: float | None = 0.0,
             timeout: int | None = None,
             retries: int = 0) -> str:
    """发一次补全，返回正文。失败抛 ``RawError``（文案可直接冒泡）。"""
    return complete_ex(llm, messages, max_tokens=max_tokens,
                       temperature=temperature, timeout=timeout,
                       retries=retries)[0]


def estimate_tokens(text: str) -> int:
    """没有 usage 字段时的粗估。

    中文大约 1 字 ≈ 1 token，英文约 4 字符 ≈ 1 token。取 2 字符/token 是个
    中间值——只用来做预算熔断，不需要精确，**宁可高估**。
    """
    n = len(text or "")
    return max(1, int(n / 2))


def complete_ex(llm: dict, messages: list[dict], *,
                max_tokens: int = 800,
                temperature: float | None = 0.0,
                timeout: int | None = None,
                retries: int = 0) -> tuple[str, dict]:
    """同 ``complete``，但额外返回 ``{"prompt":..,"completion":..,"total":..}``。

    Agent 要靠它做 token 预算熔断；服务端不给 usage 就自己估。
    """
    ok, why = _llm.ready(llm)
    if not ok:
        raise RawError(why)

    url = _llm.chat_url(llm["base_url"])
    headers = {"Content-Type": "application/json"}
    if llm.get("api_key"):
        headers["Authorization"] = f"Bearer {llm['api_key']}"

    payload = _build_payload(llm, messages, max_tokens, temperature)
    tmo = timeout or int(llm.get("timeout") or 30)

    attempt = 0
    while True:
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=tmo)
        except requests.exceptions.Timeout:
            raise RawError("等太久了，接口有点慢")
        except requests.exceptions.SSLError:
            raise RawError("HTTPS 证书有问题")
        except requests.exceptions.ConnectionError:
            raise RawError("连不上，检查下地址和网络")
        except requests.exceptions.MissingSchema:
            raise RawError("Base URL 格式不对")
        except Exception as exc:
            raise RawError(f"请求失败：{str(exc)[:30]}")

        if resp.status_code == 400:
            detail = _llm._api_message(resp)
            if _degrade(payload, detail):
                continue

        if resp.status_code != 200:
            err = RawError(_llm.friendly_error(resp.status_code, resp))
            if attempt < retries and resp.status_code >= 500:
                attempt += 1
                continue
            raise err

        try:
            data = resp.json()
        except Exception:
            raise RawError("返回不是 JSON，确认是 OpenAI 接口")

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") for p in content if isinstance(p, dict))
        if content is None:
            # 少数推理模型只给 reasoning_content
            content = msg.get("reasoning_content") or ""
        text = str(content).strip()
        if not text:
            raise RawError("模型没说话，换个模型试试")

        usage = data.get("usage") or {}
        p_tok = int(usage.get("prompt_tokens") or 0)
        c_tok = int(usage.get("completion_tokens") or 0)
        if not p_tok:
            p_tok = sum(estimate_tokens(m.get("content", "")) for m in messages)
        if not c_tok:
            c_tok = estimate_tokens(text)
        return text, {"prompt": p_tok, "completion": c_tok,
                      "total": int(usage.get("total_tokens") or (p_tok + c_tok))}


def complete_json(llm: dict, messages: list[dict], *,
                  max_tokens: int = 400,
                  timeout: int | None = None) -> dict | None:
    """要一段 JSON。解析失败返回 None（调用方决定要不要重试）。

    注意**不发** ``response_format``：大量 one-api/new-api 中转站和本地服务
    不认这个字段，会直接 400。宁可自己容错解析。
    """
    text = complete(llm, messages, max_tokens=max_tokens,
                    temperature=0.0, timeout=timeout)
    return loads_lenient(text)


# --------------------------------------------------------------------------
# 宽松 JSON 解析
# --------------------------------------------------------------------------
def _strip_fence(text: str) -> str:
    t = text.strip()
    if "```" not in t:
        return t
    # 取第一个 ``` 之后、最后一个 ``` 之前
    start = t.find("```")
    body = t[start + 3:]
    if body[:4].lower().startswith("json"):
        body = body[4:]
    end = body.rfind("```")
    if end != -1:
        body = body[:end]
    return body.strip()


def _brace_slice(text: str) -> str:
    """从第一个 { 开始做括号配平，截出完整对象。应对"模型先说了句话再给 JSON"。"""
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def _repair(text: str) -> str:
    """轻量修补：尾逗号、中文引号、单引号。只在明显时改，不乱来。"""
    t = text
    # 尾随逗号
    t = re.sub(r",\s*([}\]])", r"\1", t)
    # 中文引号只在全文没有英文引号时替换，避免误伤字符串内容
    if '"' not in t:
        t = t.replace("“", '"').replace("”", '"').replace("「", '"').replace("」", '"')
    return t


def loads_lenient(text: str) -> dict | None:
    """五级容错：原样 → 去围栏 → 括号截取 → 修补 → 放弃。"""
    if not text:
        return None
    candidates = [text.strip()]
    stripped = _strip_fence(text)
    if stripped != candidates[0]:
        candidates.append(stripped)
    sliced = _brace_slice(stripped)
    if sliced and sliced not in candidates:
        candidates.append(sliced)
    for cand in list(candidates):
        rep = _repair(cand)
        if rep not in candidates:
            candidates.append(rep)

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            return {"__list__": obj}
    return None
