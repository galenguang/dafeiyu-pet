# -*- coding: utf-8 -*-
"""记忆抽取：规则轨 + 模型轨。

触发是三级闸门，先便宜后昂贵：

1. ``memory_enabled`` 为假 → 只更新计数，不抽取
2. 本地规则（``rules.py``）→ **永远跑**，零成本零延迟
3. 模型抽取 → 要同时满足：开关开着、距上次 ≥ 最小间隔、没有在飞的任务、
   攒够 N 轮或命中强信号词

模型只提候选，**不能改也不能删已有记忆**（add-only）。这是防幻觉覆盖真实
画像的关键——一个抽错的模型最多多记一条废话，不能把"主人叫小明"改成
"主人叫小强"。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

from .. import llm as _llm
from .. import llm_raw
from . import rules, schema
from .rules import Candidate
from .store import MemoryStore

MAX_PENDING = 24          # 最多攒这么多条对话去抽取
MAX_ITEMS_PER_RUN = 5     # 一次最多接受几条（模型爱贪多）
FAIL_STREAK_LIMIT = 3     # 连续失败几次就本次运行内关掉模型抽取

EXTRACT_PROMPT = """你是记忆整理器。从下面的对话里抽取值得长期记住的信息，只输出 JSON，不要解释、不要 Markdown。

只抽这些：
- 主人的称呼、职业、身份
- 明确的喜好 / 厌恶（要有具体对象，不是"今天好烦"）
- 长期习惯、作息（熬夜、久坐、固定时间做某事）
- 正在做 / 正在学的事
- 明确要求你记住的事

不要抽：一次性的情绪、寒暄、宠物的台词、你的推测、没有具体对象的好恶。

输出格式（没有的键直接省略；每条 text 用第三人称，不超过 20 字，不要引号不要换行）：
{"profile":{"call_me":"","occupation":"","likes":[""],"dislikes":[""],"habits":[""]},
 "facts":[""],
 "episodes":[{"kind":"preference|event|promise|mood","text":""}],
 "style":{"prefer":"","avoid":[""]}}

最多 3 条 facts、2 条 episodes。宁可少抽，不要编。

对话：
%s"""

FORMAT_FIX = ("上一条不是合法 JSON。只输出一个 JSON 对象，第一个字符必须是 { ，"
              "不要解释、不要 Markdown 围栏。")


# --------------------------------------------------------------------------
# 模型输出 → 候选
# --------------------------------------------------------------------------
_BAD_CHARS = set('\n\r{}"“”')


def _clean_item(v, maxlen: int = 20) -> str:
    """输出侧硬约束：不信任模型给的文本。"""
    if not isinstance(v, str):
        return ""
    t = v.replace("\r", " ").replace("\n", " ").strip()
    if len(t) > maxlen:
        return ""
    if any(c in _BAD_CHARS for c in t):
        return ""
    t = schema.strip_first_person(t).strip(" ，,。.、")
    if len(t) < 2:
        return ""
    return t


def json_to_candidates(obj: dict) -> list[Candidate]:
    """把模型吐的 JSON 转成候选。畸形字段一律跳过，不抛异常。"""
    out: list[Candidate] = []
    if not isinstance(obj, dict):
        return out

    prof = obj.get("profile")
    prof = prof if isinstance(prof, dict) else {}

    name = _clean_item(prof.get("call_me"), 8)
    if name:
        out.append(Candidate("call_me", text=name, confidence=0.9))

    job = _clean_item(prof.get("occupation"), 10)
    if job:
        out.append(Candidate("occupation", text=job, confidence=0.7))

    for field, conf in (("likes", 0.75), ("dislikes", 0.75), ("habits", 0.7)):
        vals = prof.get(field)
        if not isinstance(vals, list):
            continue
        for v in vals[:3]:
            t = _clean_item(v, 12)
            if t:
                out.append(Candidate(field, text=t, confidence=conf))

    facts = obj.get("facts")
    if isinstance(facts, list):
        for v in facts[:3]:
            t = _clean_item(v, 40)
            if t:
                out.append(Candidate("facts", text=t, confidence=0.7))

    eps = obj.get("episodes")
    if isinstance(eps, list):
        for e in eps[:2]:
            if isinstance(e, str):
                e = {"kind": "event", "text": e}
            if not isinstance(e, dict):
                continue
            t = _clean_item(e.get("text"), 40)
            if not t:
                continue
            kind = e.get("kind")
            if kind not in schema.EPISODE_KINDS:
                kind = "event"
            out.append(Candidate("episodes", text=t, kind=kind,
                                 confidence=0.65, tags=["聊天"]))

    # 一次最多接受 5 条，按上面排的优先级截断
    return out[:MAX_ITEMS_PER_RUN]


def json_style(obj: dict) -> tuple[str, list[str]] | None:
    st = obj.get("style") if isinstance(obj, dict) else None
    if not isinstance(st, dict):
        return None
    prefer = _clean_item(st.get("prefer"), 16)
    avoid = []
    if isinstance(st.get("avoid"), list):
        avoid = [x for x in (_clean_item(v, 10) for v in st["avoid"][:4]) if x]
    if not prefer and not avoid:
        return None
    return prefer, avoid


# --------------------------------------------------------------------------
# 抽取器
# --------------------------------------------------------------------------
class Extractor:
    def __init__(self, store: MemoryStore,
                 get_llm: Callable[[], dict],
                 get_cfg: Callable[[], dict] | None = None):
        self.store = store
        self._get_llm = get_llm
        self._get_cfg = get_cfg or (lambda: {})
        self._pending: list[dict] = []
        self._last_at = 0.0
        self._busy = False
        self._fail_streak = 0
        self._llm_off = False
        self._lock = threading.Lock()
        self.last_error = ""

    # ---------- 供外部调用 ----------
    def on_user(self, text: str) -> None:
        """用户说了句话：跑规则轨，然后看要不要触发模型轨。"""
        text = (text or "").strip()
        if not text:
            return
        cfg = self._get_cfg()
        if not cfg.get("memory_enabled", True):
            return

        # 规则轨：永远跑
        cands = rules.extract_rules(text)
        if cands:
            n = rules.apply_candidates(self.store, cands, source="chat")
            if n:
                self.store.mark_dirty()

        with self._lock:
            self._pending.append({"role": "user", "content": text})
            del self._pending[:-MAX_PENDING]

        if self._should_extract(text):
            self.spawn()

    def on_reply(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._pending.append({"role": "pet", "content": text})
            del self._pending[:-MAX_PENDING]

    def flush_pending(self) -> int:
        with self._lock:
            n = len([m for m in self._pending if m["role"] == "user"])
            self._pending.clear()
            return n

    @property
    def pending_users(self) -> int:
        with self._lock:
            return len([m for m in self._pending if m["role"] == "user"])

    # ---------- 闸门 ----------
    def _should_extract(self, text: str) -> bool:
        cfg = self._get_cfg()
        if self._llm_off or self._busy:
            return False
        if not cfg.get("memory_llm_extract", True):
            return False
        gap = float(cfg.get("memory_extract_min_gap", 90) or 0)
        if time.monotonic() - self._last_at < gap:
            return False
        every = int(cfg.get("memory_extract_every", 6) or 6)
        if self.pending_users >= max(2, every):
            return True
        return rules.has_strong_signal(text)

    def spawn(self) -> threading.Thread:
        self._busy = True
        self._last_at = time.monotonic()
        t = threading.Thread(target=self._run_guarded, daemon=True,
                             name="memory-extract")
        t.start()
        return t

    @property
    def busy(self) -> bool:
        return self._busy

    def organize_now(self) -> bool:
        """面板上点「整理一下」用：不看闸门，立刻跑一次。

        已经在跑就返回 False，别叠两个线程抢同一份 pending。
        """
        if self._busy:
            return False
        self.spawn()
        return True

    def _run_guarded(self) -> None:
        try:
            self.run_once()
        finally:
            self._busy = False

    # ---------- 主流程（测试可直接同步调） ----------
    def run_once(self) -> int:
        """跑一次模型抽取，返回新增条数。失败返回 0 并静默。"""
        cfg = self._get_cfg()
        if self._llm_off or not cfg.get("memory_llm_extract", True):
            return 0

        with self._lock:
            convo = list(self._pending)
        if not convo:
            return 0

        llm = self._get_llm()
        ok, why = _llm.ready(llm)
        if not ok:
            self.last_error = why
            return 0

        msgs = [{"role": "user", "content": EXTRACT_PROMPT % _render(convo)}]
        self.store.bump_counter("extract_runs")

        obj = None
        try:
            text = llm_raw.complete(llm, msgs, max_tokens=320,
                                    temperature=0.0, timeout=30)
            obj = llm_raw.loads_lenient(text)
            if obj is None:
                # 一次纠错：把原回复贴回去，再要求只输出 JSON
                msgs += [{"role": "assistant", "content": text},
                         {"role": "user", "content": FORMAT_FIX}]
                text2 = llm_raw.complete(llm, msgs, max_tokens=320,
                                         temperature=0.0, timeout=30)
                obj = llm_raw.loads_lenient(text2)
        except Exception as exc:
            self.last_error = str(exc)
            obj = None

        if not isinstance(obj, dict):
            self._on_fail("输出不是 JSON")
            return 0

        cands = json_to_candidates(obj)
        style = json_style(obj)
        n = 0
        if cands:
            n = rules.apply_candidates(self.store, cands, source="chat")
        if style and (style[0] or style[1]):
            prefer, avoid = style
            if prefer:
                self.store.set_style(prefer=prefer, avoid=avoid or None)
                n += 1

        if n:
            self._fail_streak = 0
            with self._lock:
                self._pending.clear()
            self.store.mark_dirty()
            self.store.emit("changed")
        return n

    def _on_fail(self, why: str) -> None:
        self.last_error = why
        self._fail_streak += 1
        self.store.bump_counter("extract_fail")
        if self._fail_streak >= FAIL_STREAK_LIMIT:
            # 连续崩 3 次就本次运行内关掉，别再浪费用户的 token
            self._llm_off = True
            print("[memory] 模型抽取连续失败，本次运行内改用规则抽取")
        if self._get_cfg().get("memory_debug_log"):
            print("[memory] 抽取失败:", why)


def _render(convo: list[dict]) -> str:
    lines = []
    for m in convo[-10:]:
        tag = "[USER]" if m["role"] == "user" else "[PET]"
        lines.append(f"{tag} {m['content'][:120]}")
    return "\n".join(lines)
