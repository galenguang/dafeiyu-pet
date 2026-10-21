# -*- coding: utf-8 -*-
"""Agent 专用补全：不截断、带 token 记账、超预算立刻停。

和 ``app/llm.py`` 的 ``Chat`` 是两条完全不同的路子：

- ``Chat`` 给气泡用，回复硬截到 40 字（产品需求，不是 bug）
- 这里给 Agent 用，``max_tokens`` 默认 1800，一个字符都不许截

和 ``app/llm_raw.py`` 的关系：底层就用它，这里只加"记账"和"熔断"。为什么
要单独一层而不是直接用 ``llm_raw``——因为 token 预算必须**跟着任务走**，
一个任务跑完就清零，而 ``llm_raw`` 是无状态的函数。
"""
from __future__ import annotations

from .. import llm_raw


class BudgetExceeded(RuntimeError):
    """本次任务的 token 预算用完了。文案可以直接说给主人听。"""


class AgentLLM:
    """``get_llm`` 是个可调用对象，返回 ``{"base_url","api_key","model","timeout"}``。

    传函数不传字典：用户在设置里换了模型，下一次调用自动生效，不用重建对象。
    """

    def __init__(self, get_llm, budget: int = 40000, max_tokens: int = 1800,
                 temperature: float = 0.2, timeout: int | None = None,
                 retries: int = 1):
        self._get_llm = get_llm
        self.budget = max(1000, int(budget))
        self.max_tokens = max(256, int(max_tokens))
        self.temperature = float(temperature)
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.used = 0
        self.calls = 0
        self.last_usage: dict = {}

    # ---------- 预算 ----------
    def reset(self) -> None:
        self.used = 0
        self.calls = 0
        self.last_usage = {}

    @property
    def left(self) -> int:
        return max(0, self.budget - self.used)

    @property
    def ratio(self) -> float:
        """已用比例。主循环拿它在 70% 时触发上下文压缩。"""
        return min(1.0, self.used / float(self.budget))

    # ---------- 补全 ----------
    def complete(self, messages: list[dict], max_tokens: int | None = None) -> str:
        if self.left <= 0:
            raise BudgetExceeded(
                f"这个任务的 token 预算（{self.budget}）用完了")

        llm = self._get_llm() or {}
        text, usage = llm_raw.complete_ex(
            llm, messages,
            max_tokens=max_tokens or self.max_tokens,
            temperature=self.temperature,
            timeout=self.timeout,
            retries=self.retries,
        )
        self.calls += 1
        self.used += int(usage.get("total") or 0)
        self.last_usage = usage
        return text
