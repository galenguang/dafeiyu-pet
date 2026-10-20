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

DEFAULT_CONFIG = {
    "mode": "corner",          # corner 右下角 | wander 全屏 | follow 跟随 | still 原地
    "scale": 0.70,             # 相对 640x360 画布的缩放
    "topmost": True,
    "passthrough": False,
    "autostart": False,
    "x": None,
    "y": None,
    "volume": 0.8,
    "ds_api_key": "",
    "city": "汕头",
    "idle_actions": True,      # 空闲时自动表演
    "idle_interval": 45,       # 秒，两次自动表演之间的基础间隔
    "health_monitor": True,
    "health_alerts": True,     # 指标超阈值时冒泡提醒
    "show_fps": False,
    "first_run": True,
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
    return cfg


def save_config(cfg: dict) -> None:
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as exc:  # 配置写不进去不该让程序崩
        print("[config] 保存失败:", exc)
