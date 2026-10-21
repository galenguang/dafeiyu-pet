# -*- coding: utf-8 -*-
"""大模型接入：任何兼容 OpenAI 接口格式的服务都能用。

不绑定厂商，只认三样东西：base_url / api_key / model。
DeepSeek、OpenAI、通义千问、Kimi、智谱、硅基流动、Ollama、LM Studio、vLLM、
各种 one-api / new-api 中转站，只要是 OpenAI 格式就都能跑。

所有网络请求都在后台线程跑，结果通过回调送回主线程（回调里只入队，
由主线程定时器消费），避免跨线程碰 Qt 对象。
"""
from __future__ import annotations

import json
import threading
from urllib.parse import urlsplit

import requests

from . import config

SYSTEM_PROMPT = (
    "你是桌面宠物「大肥鱼」，一只 DeepSeek 鲸鱼娘。"
    "性格贱兮兮但很贴心，说话短、口语化、有点毒舌但从不真伤人。"
    "每句话不超过 25 个字，不加引号，不说教，偶尔吐槽主人摸鱼。"
    "你可以关心主人的电脑状态和身体，但别啰嗦。"
)

MAX_HISTORY = 40

# 预设只是帮用户少打几个字，选完仍然可以随手改
PRESETS = [
    {
        "name": "DeepSeek",
        "base": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "hint": "platform.deepseek.com 申请；支持查余额",
    },
    {
        "name": "OpenAI",
        "base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "hint": "platform.openai.com 申请",
    },
    {
        "name": "通义千问（DashScope）",
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "hint": "阿里云百炼控制台申请",
    },
    {
        "name": "Kimi（月之暗面）",
        "base": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "hint": "platform.moonshot.cn 申请",
    },
    {
        "name": "智谱 GLM",
        "base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "hint": "open.bigmodel.cn 申请，flash 免费",
    },
    {
        "name": "硅基流动 SiliconFlow",
        "base": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "hint": "cloud.siliconflow.cn 申请",
    },
    {
        "name": "Ollama（本地）",
        "base": "http://localhost:11434/v1",
        "model": "qwen2.5:7b",
        "hint": "本地跑，Key 随便填或留空；模型名用 ollama list 看",
    },
    {
        "name": "LM Studio（本地）",
        "base": "http://localhost:1234/v1",
        "model": "local-model",
        "hint": "本地跑，Key 留空即可",
    },
    {
        "name": "vLLM / 其他本地服务",
        "base": "http://localhost:8000/v1",
        "model": "",
        "hint": "自建 OpenAI 兼容服务",
    },
    {
        "name": "自定义",
        "base": "",
        "model": "",
        "hint": "直接填服务商给的 Base URL，一般以 /v1 结尾",
    },
]

PRESET_BY_NAME = {p["name"]: p for p in PRESETS}


# ---------------- 地址处理 ----------------
def normalize_base(base: str) -> str:
    """把用户填的 Base URL 收拾成标准形式（末尾不带斜杠）。

    容忍这些写法：
        api.deepseek.com              -> https://api.deepseek.com/v1
        https://api.openai.com        -> https://api.openai.com/v1
        https://api.openai.com/v1/    -> https://api.openai.com/v1
        https://x/v1/chat/completions -> https://x/v1
        http://localhost:11434        -> http://localhost:11434/v1
    """
    b = (base or "").strip().rstrip("/")
    if not b:
        return ""
    if not b.startswith(("http://", "https://")):
        # 本机地址补 http，其他补 https
        host = b.split("/")[0].split(":")[0].lower()
        local = host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal")
        b = ("http://" if local else "https://") + b
    # 用户直接把完整端点贴进来了
    for suffix in ("/chat/completions", "/completions"):
        if b.endswith(suffix):
            b = b[: -len(suffix)]
            break
    b = b.rstrip("/")
    # 只填了域名没带路径的，补 /v1
    parts = urlsplit(b)
    if not parts.path or parts.path == "/":
        b = b + "/v1"
    return b


def chat_url(base: str) -> str:
    b = normalize_base(base)
    return f"{b}/chat/completions" if b else ""


def balance_url(base: str) -> str:
    """DeepSeek 的余额接口在根路径下，不在 /v1 里。"""
    b = normalize_base(base)
    if not b:
        return ""
    if b.endswith("/v1"):
        b = b[: -len("/v1")]
    return f"{b}/user/balance"


def is_deepseek(base: str) -> bool:
    b = normalize_base(base).lower()
    return "deepseek" in b


def is_local(base: str) -> bool:
    b = normalize_base(base).lower()
    return any(h in b for h in ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "host.docker.internal"))


# ---------------- 配置打包 ----------------
def llm_from_cfg(cfg: dict) -> dict:
    """从整份配置里抽出大模型那几项，避免到处传 cfg。"""
    return {
        "base_url": normalize_base(cfg.get("llm_base_url", "")),
        "api_key": (cfg.get("llm_api_key") or "").strip(),
        "model": (cfg.get("llm_model") or "").strip(),
        "timeout": int(cfg.get("llm_timeout") or 30),
    }


def ready(llm: dict) -> tuple[bool, str]:
    """能不能直接开聊；(False, 原因) 时由调用方去提示用户。"""
    if not llm.get("base_url"):
        return False, "还没配接口地址，右键 → 模型设置"
    if not llm.get("model"):
        return False, "还没填模型名，右键 → 模型设置"
    if not llm.get("api_key") and not is_local(llm["base_url"]):
        return False, "还没填 API Key，右键 → 模型设置"
    return True, ""


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}{'•' * 6}{key[-4:]}"


# ---------------- 错误文案 ----------------
def _api_message(resp) -> str:
    """从各家五花八门的错误体里挖出一句人话。"""
    text = ""
    try:
        data = resp.json()
    except Exception:
        return (resp.text or "").strip()[:120]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            text = err.get("message") or err.get("msg") or ""
        elif isinstance(err, str):
            text = err
        if not text:
            text = data.get("message") or data.get("msg") or data.get("detail") or ""
        if not text and isinstance(data.get("errors"), list) and data["errors"]:
            first = data["errors"][0]
            text = first.get("message", "") if isinstance(first, dict) else str(first)
    elif isinstance(data, list) and data:
        text = str(data[0])
    return str(text).strip()[:120]


def friendly_error(status: int, resp=None) -> str:
    """把 HTTP 状态码翻译成桌宠能说出口的话（气泡最多 40 字）。"""
    detail = _api_message(resp) if resp is not None else ""
    if status == 401:
        return "Key 不对或没权限"
    if status == 403:
        return "这个 Key 没权限用这个模型"
    if status == 404:
        return "接口地址不对，检查 Base URL"
    if status == 429:
        return "请求太频繁，等下再说"
    if status == 400:
        low = detail.lower()
        if "model" in low and ("not exist" in low or "not found" in low or "invalid" in low):
            return "模型名不对，检查 model"
        if "context" in low or "too long" in low:
            return "聊太长了，清一下历史吧"
        return detail[:30] or "请求参数被拒了"
    if status >= 500:
        return f"服务端抽风了（{status}）"
    return detail[:30] or f"请求失败（{status}）"


# ---------------- 对话 ----------------
class Chat:
    def __init__(self):
        self.history: list[dict] = []
        self.busy = False
        self._load()

    # ---------- 历史持久化 ----------
    def _load(self) -> None:
        try:
            with open(config.HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.history = data[-MAX_HISTORY:]
        except Exception:
            self.history = []

    def _save(self) -> None:
        try:
            with open(config.HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(self.history[-MAX_HISTORY:], f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def clear(self) -> None:
        self.history = []
        self._save()

    # ---------- 对话 ----------
    def ask(self, llm: dict, text: str, on_reply, on_error,
            memory: str = "") -> None:
        """on_reply(str) / on_error(str) 会在后台线程里被调用。

        ``memory`` 是记忆层拼好的背景资料，**单独作为一条 system 消息**放在
        SYSTEM_PROMPT 之后、历史之前。不能拼进 user 消息里——那样模型会把它
        当成主人说的话，转头就复述出来。
        """
        if self.busy:
            on_error("上一句还没回完呢")
            return
        ok, why = ready(llm)
        if not ok:
            on_error(why)
            return
        self.busy = True

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if memory:
            messages.append({"role": "system", "content": memory})
        messages.extend(self.history[-MAX_HISTORY:])
        messages.append({"role": "user", "content": text})

        def worker() -> None:
            try:
                reply = self._complete(llm, messages)
                reply = reply.replace("\n", " ").strip("\"'“”")
                if len(reply) > 40:
                    reply = reply[:38] + "…"
                self.history.append({"role": "user", "content": text})
                self.history.append({"role": "assistant", "content": reply})
                if len(self.history) > MAX_HISTORY:
                    self.history = self.history[-MAX_HISTORY:]
                self._save()
                on_reply(reply)
            except _LLMError as exc:
                on_error(str(exc))
            except requests.exceptions.Timeout:
                on_error("等太久了，接口有点慢")
            except requests.exceptions.ConnectionError:
                on_error("连不上，检查下地址和网络")
            except Exception as exc:
                on_error(f"出错了：{str(exc)[:20]}")
            finally:
                self.busy = False

        threading.Thread(target=worker, daemon=True, name="llm-chat").start()

    def _complete(self, llm: dict, messages: list[dict]) -> str:
        """发一次对话请求，返回正文；失败抛 _LLMError（文案可直接冒泡）。"""
        url = chat_url(llm["base_url"])
        headers = {"Content-Type": "application/json"}
        if llm["api_key"]:
            headers["Authorization"] = f"Bearer {llm['api_key']}"

        payload = {
            "model": llm["model"],
            "messages": messages,
            "max_tokens": 120,
            "temperature": 0.95,
            "stream": False,
        }
        resp = self._post(url, headers, payload, llm["timeout"])

        # 有些推理模型不收 temperature / max_tokens，按报错自动降级重试一次
        if resp.status_code == 400:
            detail = _api_message(resp).lower()
            changed = False
            if "temperature" in detail:
                payload.pop("temperature", None)
                changed = True
            if "max_tokens" in detail and "max_completion_tokens" not in payload:
                payload["max_completion_tokens"] = payload.pop("max_tokens", 120)
                changed = True
            if changed:
                resp = self._post(url, headers, payload, llm["timeout"])

        if resp.status_code != 200:
            raise _LLMError(friendly_error(resp.status_code, resp))

        try:
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):     # 少数服务返回分段内容
                content = "".join(
                    p.get("text", "") for p in content if isinstance(p, dict))
            if not content:
                raise _LLMError("模型没说话，换个模型试试")
            return str(content).strip()
        except _LLMError:
            raise
        except Exception:
            raise _LLMError("返回格式看不懂，确认是 OpenAI 接口")

    @staticmethod
    def _post(url, headers, payload, timeout):
        try:
            return requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.SSLError:
            raise _LLMError("HTTPS 证书有问题")
        except requests.exceptions.MissingSchema:
            raise _LLMError("Base URL 格式不对")


class _LLMError(RuntimeError):
    pass


# ---------------- 连接测试 ----------------
def ping(llm: dict, on_ok, on_error) -> None:
    """发一个最小请求验证 base_url / key / model 是否配套。回调在后台线程。"""
    ok, why = ready(llm)
    if not ok:
        on_error(why)
        return

    def worker() -> None:
        try:
            url = chat_url(llm["base_url"])
            headers = {"Content-Type": "application/json"}
            if llm["api_key"]:
                headers["Authorization"] = f"Bearer {llm['api_key']}"
            resp = requests.post(
                url, headers=headers,
                json={"model": llm["model"],
                      "messages": [{"role": "user", "content": "说个“在”"}],
                      "max_tokens": 16, "stream": False},
                timeout=min(llm["timeout"], 45),
            )
            if resp.status_code != 200:
                on_error(friendly_error(resp.status_code, resp))
                return
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            msg = (choice.get("message") or {}).get("content") or ""
            on_ok(f"连上了：{str(msg).strip()[:16] or '（空回复）'}")
        except requests.exceptions.Timeout:
            on_error("超时了，接口没响应")
        except requests.exceptions.ConnectionError:
            on_error("连不上，检查地址和服务是否在跑")
        except Exception as exc:
            on_error(f"失败：{str(exc)[:24]}")

    threading.Thread(target=worker, daemon=True, name="llm-ping").start()


# ---------------- 余额 ----------------
def query_balance(llm: dict, on_ok, on_error) -> None:
    """查余额。目前只有 DeepSeek 有这套接口，其他厂商直接给个说明。"""
    if not is_deepseek(llm.get("base_url", "")):
        on_error("这个接口查不了余额")
        return
    key = llm.get("api_key") or ""
    if not key:
        on_error("没设置 Key，查不了余额")
        return

    def worker() -> None:
        try:
            resp = requests.get(
                balance_url(llm["base_url"]),
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                timeout=12,
            )
            if resp.status_code != 200:
                on_error(f"查询失败 {resp.status_code}")
                return
            data = resp.json()
            infos = data.get("balance_infos") or []
            if not infos:
                on_error("没查到余额信息")
                return
            info = infos[0]
            total = info.get("total_balance", "0")
            currency = info.get("currency", "CNY")
            symbol = "¥" if currency == "CNY" else "$"
            on_ok(f"{symbol}{total}", data.get("is_available", True))
        except requests.exceptions.Timeout:
            on_error("查询超时")
        except Exception as exc:
            on_error(f"查询出错：{str(exc)[:18]}")

    threading.Thread(target=worker, daemon=True, name="ds-balance").start()


# ---------------- 天气 ----------------
def query_weather(city: str, on_ok, on_error) -> None:
    def worker() -> None:
        try:
            r = requests.get(
                f"https://wttr.in/{city}?format=j1",
                timeout=12,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            data = r.json()
            cur = data["current_condition"][0]
            temp = cur["temp_C"]
            desc_en = cur["weatherDesc"][0]["value"]
            mapping = {
                "Sunny": "晴", "Clear": "晴", "Partly cloudy": "多云",
                "Cloudy": "阴", "Overcast": "阴", "Mist": "雾",
                "Light rain": "小雨", "Moderate rain": "中雨",
                "Heavy rain": "大雨", "Patchy rain possible": "可能有雨",
                "Light drizzle": "毛毛雨", "Thundery outbreaks possible": "可能有雷阵雨",
                "Light snow": "小雪", "Moderate snow": "中雪", "Heavy snow": "大雪",
            }
            desc = mapping.get(desc_en, desc_en)
            on_ok(f"{city}今天 {temp}°，{desc}")
        except Exception:
            on_error("天气获取失败")

    threading.Thread(target=worker, daemon=True, name="weather").start()
