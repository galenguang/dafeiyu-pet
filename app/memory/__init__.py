# -*- coding: utf-8 -*-
"""长期记忆与人格。

对外只暴露 ``Memory`` 一个门面；内部的 schema / store / rules / extract /
behavior / recall / voice 都是实现细节。

**这个包不许 import 任何 Qt 模块**，否则 headless 测试跑不了。
"""
from __future__ import annotations

__all__ = ["Memory", "MemoryStore", "schema"]

from . import schema  # noqa: F401
from .store import MemoryStore  # noqa: F401


def __getattr__(name):
    # Memory 依赖 extract/recall/voice，延迟导入，避免早期模块缺失时整个包不可用
    if name == "Memory":
        from .core import Memory
        return Memory
    raise AttributeError(name)
