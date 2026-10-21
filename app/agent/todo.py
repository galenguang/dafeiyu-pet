# -*- coding: utf-8 -*-
"""Todo 账本：让模型自己维护"我打算做几件事、做到哪了"。

**为什么账本归模型管，而不是由代码推断。** 代码能看出 ``file_write`` 返回了
ok，看不出主人要的其实是"把这一百个文件按月份分好类"。什么算"办成"，只有
模型知道。所以账本的口径交给模型，代码只负责**卡住它**。

卡住什么：``changed_unverified`` 这个状态。写了文件、改了设置、启动了程序，
但还没核验，就只能是 ``changed_unverified``，不能是 ``done``。只要账本里还有
未核验项，``finish`` 就会被驳回一次——这是把系统提示里第 2 条硬规则
（"回执 ok ≠ 办成"）从"劝"变成"拦"。

**不卡什么**：不限制条目数量级、不校验内容合理性。账本是模型的自述，不是
事实来源；它的价值在于"模型自己写下的东西，它自己不好赖账"。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

PENDING = "pending"
DOING = "doing"
DONE = "done"
CHANGED = "changed_unverified"      # 改了东西但没核验——最危险的状态
SKIPPED = "skipped"
FAILED = "failed"

STATUSES = (PENDING, DOING, DONE, CHANGED, SKIPPED, FAILED)
KINDS = ("plan", "act", "verify")

MAX_ITEMS = 30
MAX_CONTENT = 120

_ICON = {PENDING: "○", DOING: "◐", DONE: "●",
         CHANGED: "◆", SKIPPED: "—", FAILED: "✗"}


@dataclass
class TodoItem:
    id: str
    kind: str = "act"
    content: str = ""
    status: str = PENDING
    note: str = ""
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "content": self.content,
                "status": self.status, "note": self.note}

    def line(self) -> str:
        icon = _ICON.get(self.status, "?")
        s = f"{icon} {self.id}. {self.content}"
        if self.note:
            s += f"（{self.note}）"
        return s


class TodoLedger:
    """线程安全。``revision`` 每次真改动 +1，主循环靠它判断要不要推 UI。"""

    def __init__(self, max_items: int = MAX_ITEMS):
        self._lock = threading.Lock()
        self._items: dict[str, TodoItem] = {}
        self._order: list[str] = []
        self.max_items = max(1, int(max_items))
        self.revision = 0

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def update(self, id: str = "", kind: str = "act", content: str = "",
               status: str = PENDING, note: str = "") -> dict:
        """模型调用的入口。``id`` 为空时自动分配下一个号。

        这里**不抛异常**——账本写错不该让整个任务崩掉，返回里带上 ``error``
        让模型自己看见就行。
        """
        tid = str(id or "").strip()
        st = str(status or PENDING).strip().lower()
        kd = str(kind or "act").strip().lower()
        txt = str(content or "").strip()[:MAX_CONTENT]
        nt = str(note or "").strip()[:MAX_CONTENT]

        err = ""
        if st not in STATUSES:
            err = f"未知状态 {st}，可用：{'/'.join(STATUSES)}"
            st = PENDING
        if kd not in KINDS:
            kd = "act"

        with self._lock:
            if not tid:
                tid = str(len(self._order) + 1)
            it = self._items.get(tid)
            if it is None:
                if len(self._order) >= self.max_items:
                    return {"error": f"账本最多 {self.max_items} 条，先收尾再加",
                            "total": len(self._order)}
                it = TodoItem(id=tid)
                self._items[tid] = it
                self._order.append(tid)
            if txt:
                it.content = txt
            if not it.content:
                it.content = f"第 {tid} 步"
            it.kind = kd
            it.status = st
            it.note = nt
            it.ts = time.time()
            self.revision += 1
            return self._snapshot_locked(it, err)

    def add(self, content: str, kind: str = "act") -> str:
        """代码侧便捷入口（不暴露给模型）。"""
        r = self.update("", kind=kind, content=content)
        return str(r.get("id") or "")

    def reset(self) -> None:
        with self._lock:
            self._items.clear()
            self._order.clear()
            self.revision += 1

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def items(self) -> list[dict]:
        """给模型和 UI 看的快照。"""
        with self._lock:
            return [self._items[i].as_dict() for i in self._order]

    def text(self) -> str:
        with self._lock:
            if not self._order:
                return "（账本还是空的）"
            return "\n".join(self._items[i].line() for i in self._order)

    def _snapshot_locked(self, item: TodoItem, err: str) -> dict:
        total = len(self._order)
        done = sum(1 for i in self._order if self._items[i].status == DONE)
        unver = sum(1 for i in self._order if self._items[i].status == CHANGED)
        out = {"id": item.id, "status": item.status,
               "total": total, "done": done, "unverified": unver}
        if err:
            out["error"] = err
        return out

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    def unverified(self) -> list[dict]:
        with self._lock:
            return [self._items[i].as_dict() for i in self._order
                    if self._items[i].status == CHANGED]

    def unfinished(self) -> list[dict]:
        with self._lock:
            return [self._items[i].as_dict() for i in self._order
                    if self._items[i].status in (PENDING, DOING)]

    def stats(self) -> dict:
        with self._lock:
            c = {s: 0 for s in STATUSES}
            for i in self._order:
                c[self._items[i].status] = c.get(self._items[i].status, 0) + 1
            c["total"] = len(self._order)
            return c

    def summary(self) -> str:
        """一句话进度，给气泡台词用。"""
        s = self.stats()
        if not s["total"]:
            return ""
        tail = f"，{s[CHANGED]} 项待核验" if s[CHANGED] else ""
        return f"{s[DONE]}/{s['total']} 步{tail}"
