# -*- coding: utf-8 -*-
"""记忆门面：``pet.py`` 只跟这一个类打交道。

里面套着四样东西，但对外只暴露少数几个方法：

- ``store``      数据（json 落盘、去重、淘汰）
- ``extractor``  抽取（规则轨永远跑，模型轨按闸门跑）
- ``watcher``    行为观察（60 秒一次 daemon 线程）
- ``voice``      人格化台词（由 ``ctx()`` / ``greeting()`` 转出去）

**为什么要有这一层**：``pet.py`` 已经 900 行了，不能让它知道"亲密度怎么涨""阈
值多少""线程怎么停"。所以这里把所有跨模块的胶水都收进来，主窗口那边净增
十来行就够。

**这个模块不许 import Qt**——它跑在 daemon 线程里，也跑在 headless 测试里。
"""
from __future__ import annotations

from . import recall, voice
from .behavior import BehaviorThread, BehaviorWatcher
from .extract import Extractor
from .store import MemoryStore


class Memory:
    def __init__(self, get_llm=None, get_cfg=None, store: MemoryStore | None = None,
                 watch: bool = True):
        self._get_llm = get_llm or (lambda: {})
        self._get_cfg = get_cfg or (lambda: {})
        self.store = store or MemoryStore()
        self.extractor = Extractor(self.store, self._get_llm, self._get_cfg)
        self._watcher: BehaviorWatcher | None = None
        self._thread: BehaviorThread | None = None
        self._watch = watch
        self.store.touch_day()
        self.sync_config()

    # ================= 配置 =================
    def cfg(self) -> dict:
        try:
            return self._get_cfg() or {}
        except Exception:
            return {}

    def enabled(self) -> bool:
        return bool(self.cfg().get("memory_enabled", True))

    def sync_config(self) -> None:
        """按配置决定要不要跑行为采样线程。菜单里开关一改就调这个。"""
        if not self._watch:
            return
        want = self.enabled() and bool(self.cfg().get("memory_watch_activity", True))
        if want and self._thread is None:
            self._watcher = BehaviorWatcher(self.store, self._get_cfg)
            self._thread = BehaviorThread(self._watcher)
            self._thread.start()
        elif not want and self._thread is not None:
            self._thread.stop()
            self._thread = None
            self._watcher = None

    # ================= 对话 =================
    def note_user(self, text: str) -> None:
        """主人说了一句。规则轨在这里同步跑，模型轨可能起线程。"""
        if not self.enabled():
            return
        self.store.note_chat(user_text=text)
        self.extractor.on_user(text)
        self.store.maybe_save()

    def note_reply(self, text: str) -> None:
        if not self.enabled():
            return
        self.extractor.on_reply(text)

    def teach(self, text: str) -> bool:
        """手动教一件事。直接写 facts 并置顶，永不淘汰。"""
        text = (text or "").strip()
        if not text:
            return False
        return bool(self.store.add_fact(text, confidence=1.0,
                                        source="manual", pinned=True))

    def organize(self) -> bool:
        """面板「整理一下」：立刻跑一次模型抽取。"""
        if not self.enabled():
            return False
        return self.extractor.organize_now()

    # ================= 台词 =================
    def ctx(self, situation: str, **kw) -> voice.Ctx:
        """给 ``lines.pick(..., ctx=mem.ctx("click"))`` 用。"""
        return voice.context(self.store, situation, cfg=self.cfg(), **kw)

    def greeting(self) -> str | None:
        """启动问候。返回 None 就让调用方走原来的台词池。"""
        if not self.enabled():
            return None
        return voice.greeting(self.store, cfg=self.cfg())

    # ================= 给对话 / Agent 用 =================
    def context_block(self, query: str = "") -> str:
        """塞进 system prompt 的那一小段。没有记忆时返回空串。"""
        if not self.enabled():
            return ""
        try:
            return recall.build_memory_block(self.store, query)
        except Exception:
            return ""

    # ================= 主循环 =================
    def drain_events(self) -> list[dict]:
        """取走后台线程排队的台词/通知，主窗口在 tick() 里消费。"""
        return self.store.drain()

    def tick(self) -> list[dict]:
        """跨天检查 + 取事件。返回 ``[{"kind": "say", "text": ...}]``。

        主循环 20ms 一次，跨天检查没必要跟着跑——大约 6 秒一次就够了。
        """
        self._n = getattr(self, "_n", 0) + 1
        if self._n % 300 == 1:
            self.store.touch_day()
        return self.store.drain()

    def flush(self) -> None:
        try:
            self.store.flush()
        except Exception:
            pass

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread = None
            self._watcher = None
        self.flush()

    # ================= 调试 =================
    def debug_summary(self) -> dict:
        rel = self.store.relationship()
        prof = self.store.profile()
        return {
            "enabled": self.enabled(),
            "days": rel.get("days_together"),
            "intimacy": rel.get("intimacy"),
            "episodes": len(self.store.episodes()),
            "facts": len(prof.get("facts") or []),
            "likes": [x["text"] for x in (prof.get("likes") or [])],
            "dislikes": [x["text"] for x in (prof.get("dislikes") or [])],
            "watch": self._thread is not None,
            "extract_busy": self.extractor.busy,
        }
