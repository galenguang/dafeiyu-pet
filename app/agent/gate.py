# -*- coding: utf-8 -*-
"""确认门：三级风险 + 默认拒绝 + 会话内放行。

设计上只有一个原则：**默认拒绝**。所有"没问清楚"的情况都走拒绝分支——
超时拒、解析失败拒、UI 没响应拒。宁可让 Agent 失败一次，也不能替主人点头。

**DANGER 永远不支持"本会话都允许"。** 这一条不是防模型，是防人：连续点几次
"允许"之后，人就会开始不看内容直接点。会话级放行只对 CONFIRM 开放。
"""
from __future__ import annotations

from .tools import CONFIRM, DANGER, SAFE

ASK_YES, ASK_NO, ASK_ALWAYS = "yes", "no", "always"


class Gate:
    """``ask(tool_name, risk, detail) -> "yes"|"no"|"always"``，超时返回 "no"。

    ``ask`` 由主窗口实现（弹确认面板），这里不碰 Qt。
    """

    def __init__(self, get_cfg, ask=None):
        self._get_cfg = get_cfg or (lambda: {})
        self._ask = ask
        self.session_allowed: set[str] = set()
        self.denied: list[str] = []
        self.last_reason = ""

    def cfg(self) -> dict:
        try:
            return self._get_cfg() or {}
        except Exception:
            return {}

    def reset(self) -> None:
        self.session_allowed.clear()
        self.denied.clear()
        self.last_reason = ""

    def check(self, tool_name: str, risk: str, detail: str = "") -> bool:
        """能不能执行。拒绝时 ``last_reason`` 里是给模型看的原因。"""
        self.last_reason = ""

        if risk == SAFE:
            return True

        if risk == DANGER:
            self.last_reason = f"{tool_name} 属于危险操作，被直接拒绝"
            self.denied.append(tool_name)
            return False

        if risk != CONFIRM:
            # 未知风险等级：当成危险处理，不要放行
            self.last_reason = f"{tool_name} 的风险等级未知，按危险处理"
            self.denied.append(tool_name)
            return False

        cfg = self.cfg()
        if cfg.get("agent_confirm") == "never":
            return True
        auto = cfg.get("agent_auto_approve") or []
        if isinstance(auto, list) and tool_name in auto:
            return True
        if tool_name in self.session_allowed:
            return True

        if self._ask is None:
            self.last_reason = "没有确认通道，操作被拒绝"
            self.denied.append(tool_name)
            return False

        try:
            answer = self._ask(tool_name, risk, detail)
        except Exception as exc:
            self.last_reason = f"确认过程出错，已拒绝：{exc}"
            self.denied.append(tool_name)
            return False

        answer = (answer or ASK_NO)
        if answer == ASK_ALWAYS:
            self.session_allowed.add(tool_name)
            return True
        if answer == ASK_YES:
            return True

        self.last_reason = "主人拒绝了这一步"
        self.denied.append(tool_name)
        return False
