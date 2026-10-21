# -*- coding: utf-8 -*-
"""路径解析与配置读写。

配置放在 %APPDATA%/DafeiyuPet/config.json，这样程序放在任何目录（含
Program Files）都能正常保存设置。
"""
from __future__ import annotations

import json
import os
import sys

APP_NAME = "DafeiyuPet"


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_dir() -> str:
    """只读资源目录（打包后是 _MEIPASS）。"""
    if _is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir() -> str:
    """可执行文件所在目录。"""
    if _is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def asset_path(*parts: str) -> str:
    return os.path.join(resource_dir(), "assets", *parts)


def clip_dir() -> str:
    return asset_path("webm")


def data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


CONFIG_PATH = os.path.join(data_dir(), "config.json")
HISTORY_PATH = os.path.join(data_dir(), "chat_history.json")


# ---- 新增路径一律用函数返回，不要用模块级常量 ----
# 原因：测试要用 DataSandbox 把 data_dir() 指到临时目录，模块级常量在 import
# 时就定死了，改不动。新模块（记忆、Agent）都走下面这些函数。
def memory_path() -> str:
    return os.path.join(data_dir(), "memory.json")


def agent_log_path() -> str:
    return os.path.join(data_dir(), "agent_log.jsonl")


def agent_apps_path() -> str:
    return os.path.join(data_dir(), "agent_apps.json")


def agent_workspace_dir() -> str:
    p = os.path.join(data_dir(), "agent_workspace")
    os.makedirs(p, exist_ok=True)
    return p


def agent_runs_dir() -> str:
    p = os.path.join(data_dir(), "agent_runs")
    os.makedirs(p, exist_ok=True)
    return p


def shots_dir() -> str:
    p = os.path.join(data_dir(), "shots")
    os.makedirs(p, exist_ok=True)
    return p

DEFAULT_CONFIG = {
    "mode": "corner",          # corner 右下角 | wander 全屏 | follow 跟随 | still 原地
    "scale": 0.70,             # 相对 640x360 画布的缩放
    "topmost": True,
    "passthrough": False,
    "autostart": False,
    "x": None,
    "y": None,
    "volume": 0.8,
    # ---- 大模型（任何兼容 OpenAI 接口的服务都能用）----
    "llm_preset": "DeepSeek",
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_api_key": "",
    "llm_model": "deepseek-chat",
    "llm_timeout": 30,
    "city": "汕头",
    "idle_actions": True,      # 空闲时自动表演
    "idle_interval": 45,       # 秒，两次自动表演之间的基础间隔
    "health_monitor": True,
    "health_alerts": True,     # 指标超阈值时冒泡提醒
    "show_fps": False,
    "first_run": True,

    # ---- 记忆与人格 ----
    "memory_enabled": True,          # 记住聊天内容
    "memory_llm_extract": True,      # 用模型抽取（关掉就只走本地规则）
    "memory_extract_every": 6,       # 攒够几轮用户发言才抽一次
    "memory_extract_min_gap": 90,    # 两次抽取最小间隔（秒）
    "memory_watch_activity": True,   # 记录使用习惯（熬夜/长时段）
    "memory_speak": True,            # 允许在台词里提起记得的事
    "memory_debug_log": False,       # 抽取失败时写日志，默认静默
    "memory_max_episodes": 200,

    # ---- 电脑操作 Agent ----
    "agent_enabled": False,          # 默认关闭，首次开启要走知情页
    "agent_confirm": "always",       # always | safe_only | never
    "agent_auto_approve": [],        # 工具名，仅本会话有效，退出即清空
    "agent_allow_dirs": [],          # 用户追加的可读写目录
    "agent_max_steps": 12,
    "agent_step_timeout": 45,
    "agent_max_tokens": 1800,
    "agent_task_budget_tokens": 40000,
    "agent_log": True,
    "agent_keep_transcripts": False,  # 保存完整对话（含你的话），默认关
    "agent_hotkey_stop": "Ctrl+Alt+Q",
    "agent_allow_process_list": False,  # 列出进程名，默认禁止
    "agent_protocol_mode": "multi",     # multi | single
    "agent_capability_checked": False,  # 是否跑过模型能力自检
}

# 老版本的配置项，读进来时迁移掉
_LEGACY_KEYS = {
    "ds_api_key": "llm_api_key",
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            cfg.update(saved)
    except Exception:
        pass

    # 老配置迁移：ds_api_key -> llm_api_key（只在新的还是空的时候迁）
    for old, new in _LEGACY_KEYS.items():
        if old in cfg:
            if not cfg.get(new):
                cfg[new] = cfg[old]
            cfg.pop(old, None)

    # base_url 存的一律是规范化后的形式，这里兜一下空值
    if not cfg.get("llm_base_url"):
        cfg["llm_base_url"] = DEFAULT_CONFIG["llm_base_url"]
    if not cfg.get("llm_model"):
        cfg["llm_model"] = DEFAULT_CONFIG["llm_model"]
    return cfg


def save_config(cfg: dict) -> None:
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as exc:  # 配置写不进去不该让程序崩
        print("[config] 保存失败:", exc)
