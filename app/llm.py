# -*- coding: utf-8 -*-
"""DeepSeek 对话与账户余额查询。

所有网络请求都在后台线程跑，结果通过回调送回主线程（回调里只入队，
由主线程定时器消费），避免跨线程碰 Qt 对象。
"""
from __future__ import annotations

import json
import os
import threading

import requests

from . import config

API_BASE = "https://api.deepseek.com"
CHAT_URL = f"{API_BASE}/chat/completions"
BALANCE_URL = f"{API_BASE}/user/balance"

SYSTEM_PROMPT = (
    "你是桌面宠物「大肥鱼」，一只 DeepSeek 鲸鱼娘。"
    "性格贱兮兮但很贴心，说话短、口语化、有点毒舌但从不真伤人。"
    "每句话不超过 25 个字，不加引号，不说教，偶尔吐槽主人摸鱼。"
    "你可以关心主人的电脑状态和身体，但别啰嗦。"
)

MAX_HISTORY = 40


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
    def ask(self, key: str, text: str, on_reply, on_error) -> None:
        """on_reply(str) / on_error(str) 会在后台线程里被调用。"""
        if self.busy:
            on_error("上一句还没回完呢")
            return
        if not key:
            on_error("先去右键菜单设置 DeepSeek Key 吧")
            return
        self.busy = True

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self.history[-MAX_HISTORY:])
        messages.append({"role": "user", "content": text})

        def worker() -> None:
            try:
                resp = requests.post(
                    CHAT_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": messages,
                        "max_tokens": 120,
                        "temperature": 0.95,
                        "stream": False,
                    },
                    timeout=20,
                )
                if resp.status_code != 200:
                    try:
                        msg = resp.json().get("error", {}).get("message", "")
                    except Exception:
                        msg = ""
                    on_error(msg[:24] or f"API {resp.status_code}")
                    return
                data = resp.json()
                reply = data["choices"][0]["message"]["content"].strip()
                reply = reply.replace("\n", " ").strip("\"'“”")
                if len(reply) > 40:
                    reply = reply[:38] + "…"
                self.history.append({"role": "user", "content": text})
                self.history.append({"role": "assistant", "content": reply})
                if len(self.history) > MAX_HISTORY:
                    self.history = self.history[-MAX_HISTORY:]
                self._save()
                on_reply(reply)
            except requests.exceptions.Timeout:
                on_error("请求超时了，网不太行")
            except requests.exceptions.ConnectionError:
                on_error("连不上，检查下网络")
            except Exception as exc:
                on_error(f"出错了：{str(exc)[:20]}")
            finally:
                self.busy = False

        threading.Thread(target=worker, daemon=True, name="ds-chat").start()


# ---------------- 余额 ----------------
def query_balance(key: str, on_ok, on_error) -> None:
    """查询 DeepSeek 账户余额，回调在后台线程执行。"""
    if not key:
        on_error("没设置 Key，查不了余额")
        return

    def worker() -> None:
        try:
            resp = requests.get(
                BALANCE_URL,
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
