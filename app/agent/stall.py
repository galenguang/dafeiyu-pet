# -*- coding: utf-8 -*-
"""卡死检测：同一段代码反复跑、同一个错误反复出，就停下。

Agent 最常见的两种死法都不是"崩溃"，而是**原地打转**：

1. 同样的代码跑第 N 次（模型以为再试一次就好了）
2. 同样的错误换了个行号，模型以为是新错误

第二种特别隐蔽——错误信息里带行号，换个写法行号就变了，肉眼看着像"新错误"，
其实一个字的区别都没有。所以错误签名要把数字抹掉再比。

**判定只有三档**：OK / WARN / STOP。WARN 只记日志，不改行为——中间过程抖一下
很正常，不该因为一次重复就打断。真正拦的是 STOP，而且理由要写给主人看。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

OK, WARN, STOP = "ok", "warn", "stop"

MAX_SAME_CODE = 6          # 同一段代码跑第 6 次还不停，就是死循环
MAX_SAME_ERROR = 2         # 同一个错误出现第 2 次就停（系统提示第 3 条硬规则）
MAX_REJECT = 3             # 连续 3 次被静态校验拒绝，说明模型没理解规则

_COMMENT = re.compile(r"#[^\n]*")
_WS = re.compile(r"[ \t\r\n]+")
_NUM = re.compile(r"\d+")


@dataclass
class Verdict:
    level: str = OK
    reason: str = ""
    hint: str = ""

    @property
    def stop(self) -> bool:
        return self.level == STOP


def code_signature(code: str) -> str:
    """把注释和空白全部抹掉再取哈希。

    不这么做的话，模型每次在末尾加一句 ``# 再试一次`` 就能绕过重复检测——
    而它恰恰最爱这么干。
    """
    t = _COMMENT.sub("", code or "")
    t = _WS.sub(" ", t).strip()
    return hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()[:16]


def error_signature(error: str) -> str:
    """错误签名：抹掉数字（行号、坐标、大小），只留形状。

    ``ValueError: 第 3 行 ...`` 和 ``ValueError: 第 4 行 ...`` 是同一个错误。
    """
    t = _NUM.sub("#", (error or "").strip())
    t = _WS.sub(" ", t)
    return t[:160]


class StallDetector:
    """一次任务一个实例。``observe`` 每步调一次。"""

    def __init__(self, max_same_code: int = MAX_SAME_CODE,
                 max_same_error: int = MAX_SAME_ERROR,
                 max_reject: int = MAX_REJECT):
        self.max_same_code = max(2, int(max_same_code))
        self.max_same_error = max(1, int(max_same_error))
        self.max_reject = max(1, int(max_reject))
        self._codes: dict[str, int] = {}
        self._errors: dict[str, int] = {}
        self._rejects = 0
        self._last_code = ""
        self._last_error = ""
        self.steps = 0

    def reset(self) -> None:
        self._codes.clear()
        self._errors.clear()
        self._rejects = 0
        self._last_code = ""
        self._last_error = ""
        self.steps = 0

    # ------------------------------------------------------------------
    def observe(self, code: str, ok: bool, error: str = "") -> Verdict:
        """一步跑完调一次。``code`` 可以传整批拼起来的代码。"""
        self.steps += 1
        sig = code_signature(code)
        self._last_code = sig
        n = self._codes.get(sig, 0) + 1
        self._codes[sig] = n
        if n >= self.max_same_code:
            return Verdict(STOP,
                           f"同一段代码已经跑了 {n} 次，没有进展",
                           "换思路，或者用 ask_user 问主人")

        if ok:
            return Verdict(WARN if n >= 3 else OK,
                           f"这段代码第 {n} 次执行" if n >= 3 else "")

        esig = error_signature(error)
        if esig:
            self._last_error = esig
            m = self._errors.get(esig, 0) + 1
            self._errors[esig] = m
            if m >= self.max_same_error:
                return Verdict(STOP,
                               f"同一个错误已经出现 {m} 次：{(error or '')[:80]}",
                               "停下来核对，用 ask_user 说明卡在哪")

        return Verdict(WARN if n >= 3 else OK, f"这段代码第 {n} 次执行" if n >= 3 else "")

    def observe_reject(self, reason: str = "") -> Verdict:
        """静态校验拒绝。不执行，但连拒三次说明模型没读懂规则。"""
        self._rejects += 1
        if self._rejects >= self.max_reject:
            return Verdict(STOP,
                           f"连续 {self._rejects} 次写出不合规的代码：{(reason or '')[:80]}",
                           "规则没理解，用 ask_user 问主人")
        return Verdict(WARN, f"代码被拒第 {self._rejects} 次：{(reason or '')[:80]}")

    def observe_accept(self) -> None:
        """一次合规就清零连续计数。"""
        self._rejects = 0

    # ------------------------------------------------------------------
    def state(self) -> dict:
        """给面板/日志看的诊断快照。"""
        return {
            "steps": self.steps,
            "unique_codes": len(self._codes),
            "max_repeat": max(self._codes.values()) if self._codes else 0,
            "errors": len(self._errors),
            "rejects": self._rejects,
        }
