# -*- coding: utf-8 -*-
"""对话上下文与两层压缩。

Agent 的上下文增长跟聊天完全不是一回事：一步能产出 4000 字工具输出，12 步就是
五万字，还没算代码本身。而这里最贵的东西恰恰是**最近的观察结果**——模型下一步
要基于它做决定。所以压缩的顺序必须反过来：先砍旧的，绝不砍新的。

**两层，成本和风险完全不同：**

- 第 1 层（``trim``）本地裁剪，不花 token、不丢信息形状：把旧观察结果的正文
  换成一行摘要（"第 3 步：成功 2 块"）。它保留了"这一步发生过、成没成"，
  丢掉的是那些用完就没用的行。**先跑它，因为它免费。**
- 第 2 层（``compact``）调模型压成 300 字。它会花钱、会失真、可能把关键路径
  写丢，所以只在预算 70% 以上才动，而且失败就退回第 1 层。

**两条硬约束：**

1. 系统提示永远在第 0 条，任何压缩都不许碰它——它包含工具清单和 8 条硬规则，
   压掉它等于让模型裸奔。
2. 最近 ``keep_recent`` 轮永远不压。模型下一步要看的就在这里。
"""
from __future__ import annotations

import threading

from . import prompts

SUMMARY_HEAD = "【前情摘要】"
DIGEST_LIMIT = 90


class Context:
    """``system`` 由调用方拼好传进来（工具清单 + 环境 + 记忆）。"""

    def __init__(self, system: str, *, max_chars: int = 24000,
                 keep_recent: int = 6, digest_limit: int = DIGEST_LIMIT):
        self._lock = threading.Lock()
        self.system = system or ""
        # 下限给 600 而不是几千：真跑起来没人会把上下文配到这么小，但测试要能
        # 用小预算验证裁剪逻辑。夹得太死会让"压了但没效果"变成一个查不出的 bug。
        self.max_chars = max(600, int(max_chars))
        self.keep_recent = max(2, int(keep_recent))
        self.digest_limit = max(20, int(digest_limit))
        # (role, text, digest)
        self._turns: list[list[str]] = []
        self.compactions = 0
        self.trims = 0

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def add_assistant(self, text: str) -> None:
        with self._lock:
            self._turns.append(["assistant", text or "", ""])

    def add_user(self, text: str, digest: str = "") -> None:
        with self._lock:
            self._turns.append(["user", text or "", (digest or "")[:self.digest_limit]])

    def add_system_note(self, text: str) -> None:
        """中途追加的规则提醒（比如纠错指令）。当成 user 消息，但不算"轮"。"""
        with self._lock:
            self._turns.append(["user", text or "", ""])

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def messages(self) -> list[dict]:
        with self._lock:
            out = [{"role": "system", "content": self.system}]
            out.extend({"role": r, "content": t} for r, t, _ in self._turns)
            return out

    def chars(self) -> int:
        with self._lock:
            return len(self.system) + sum(len(t) for _, t, _ in self._turns)

    def turns(self) -> int:
        with self._lock:
            return len(self._turns)

    def history_text(self) -> str:
        """给第 2 层压缩用的纯文本。"""
        with self._lock:
            rows = []
            for role, text, _ in self._turns:
                who = "我" if role == "assistant" else "回执"
                rows.append(f"[{who}] {text}")
            return "\n\n".join(rows)

    # ------------------------------------------------------------------
    # 第 1 层：本地裁剪（免费）
    # ------------------------------------------------------------------
    def trim(self, force: bool = False) -> int:
        """把旧观察正文换成一行的摘要。返回省下的字符数。

        ``force`` 用于第 2 层失败后的兜底：一路砍到只剩最近几轮。
        """
        with self._lock:
            before = len(self.system) + sum(len(t) for _, t, _ in self._turns)
            if not force and before <= self.max_chars:
                return 0

            cut_end = len(self._turns) - self.keep_recent
            saved = 0
            for i in range(max(0, cut_end)):
                role, text, digest = self._turns[i]
                if not digest or len(text) <= len(digest):
                    continue
                saved += len(text) - len(digest)
                self._turns[i][1] = digest

            if force:
                # 还是超：把更老的非摘要轮直接丢掉，但摘要本身留着
                while (len(self.system) + sum(len(t) for _, t, _ in self._turns)
                       > self.max_chars and len(self._turns) > self.keep_recent):
                    for i in range(len(self._turns) - self.keep_recent):
                        if not self._turns[i][1].startswith(SUMMARY_HEAD):
                            saved += len(self._turns[i][1])
                            self._turns.pop(i)
                            break
                    else:
                        break

            if saved:
                self.trims += 1
            return saved

    # ------------------------------------------------------------------
    # 第 2 层：模型压缩（花钱）
    # ------------------------------------------------------------------
    def compact(self, complete, keep: int | None = None) -> bool:
        """把旧轮次压成一段摘要。``complete(messages) -> str``。

        失败一律返回 False 并且**不动原有内容**——压缩是优化，不是前提。
        """
        keep = self.keep_recent if keep is None else max(2, int(keep))
        with self._lock:
            cut = len(self._turns) - keep
            if cut <= 0:
                return False
            old = self._turns[:cut]
            if all(t.startswith(SUMMARY_HEAD) for _, t, _ in old):
                return False              # 已经压过了，没得压
            body = "\n\n".join(
                f"[{'我' if r == 'assistant' else '回执'}] {t}" for r, t, _ in old)

        prompt = prompts.COMPACT_TEMPLATE.format(history=body)
        try:
            summary = complete([{"role": "user", "content": prompt}]) or ""
        except Exception:
            return False

        summary = " ".join(summary.split()).strip()
        if len(summary) < 8:
            return False

        with self._lock:
            keep_tail = self._turns[cut:]
            self._turns = [["user", f"{SUMMARY_HEAD}{summary[:600]}", ""]]
            self._turns.extend(keep_tail)
            self.compactions += 1
        return True

    # ------------------------------------------------------------------
    def reset(self, system: str | None = None) -> None:
        with self._lock:
            if system is not None:
                self.system = system
            self._turns.clear()
            self.compactions = 0
            self.trims = 0

    def state(self) -> dict:
        return {"chars": self.chars(), "turns": self.turns(),
                "compactions": self.compactions, "trims": self.trims}
