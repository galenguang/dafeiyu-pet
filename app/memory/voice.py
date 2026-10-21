# -*- coding: utf-8 -*-
"""人格化台词：把"记住了"变成"说话不一样"。

这个模块是记忆系统**唯一直接面向主人**的出口。做得好的时候，主人会觉得
"这条鱼好像真的认识我"；做得不好就是恐怖谷——一个每天提醒你"你今天喝了
咖啡"的东西。

所以三条约束写死在代码里，而不是靠提示词自觉：

1. **占比 ≤ 35%**（``VOICE_RATE``）。大部分时候还是原来的台词。记忆台词一
   多，就从"记得我"变成"监视我"。
2. **同一记忆点每天最多提 1 次**（``store.mark_spoken`` / ``spoken_today``）。
   同一件事一天念叨两遍，关心就变成了催命。
3. **负面记忆只在亲密度 ≥ 60 时才能提**（``NEGATIVE_MIN_INTIMACY``）。还
   不太熟的时候提人家讨厌的东西，是冒犯不是贴心。

还有一个隐含约束：**没有记忆就不说话**。所有 builder 拿不到料就返回空列表，
``pick_persona`` 返回 None，``lines.pick`` 自然回落到原台词池。所以刚装上、
什么都没记的时候，这个模块完全透明——现有 40+ 处台词调用一个字都不变。

**为什么台词模板里只放"裸名词"**：``likes`` 里存的是 "冰美式" 而不是 "喜欢
喝冰美式"（``rules._clean_object`` 会削掉动词和语气词），但模型轨偶尔会漏削。
``_noun()`` 在读取时再削一遍，保证模板 ``"我知道你喜欢{obj}"`` 拼出来永远
是通顺的。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime

from . import schema

# --------------------------------------------------------------------------
# 约束参数（改这里就是改人设，别散落在各处）
# --------------------------------------------------------------------------
VOICE_RATE = 0.35              # 记忆台词的目标占比
NEGATIVE_MIN_INTIMACY = 60     # 低于这个亲密度不提负面记忆
LATE_START = 23 * 60 + 30      # 23:30
LATE_END = 4 * 60              # 04:00
EVENT_MAX_AGE_DAYS = 14.0      # 超过两周的旧事不再翻出来
ANNIV_DAYS = {7, 30, 50, 100, 200, 300, 365, 500, 730, 1000}
MAX_LINE = 30                  # 气泡一行放得下的字数

SITUATIONS = ("greet", "click", "idle", "anniversary")


# --------------------------------------------------------------------------
# 上下文
# --------------------------------------------------------------------------
class Ctx:
    """一次台词请求的上下文。

    字段全是可选的——``lines.pick(..., ctx=voice.context(store, "click"))``
    是最常见用法；``rng`` / ``now`` 只在测试里注入。
    """

    __slots__ = ("store", "situation", "cfg", "rng", "now", "action_id", "cat")

    def __init__(self, store=None, situation: str = "", cfg=None, rng=None,
                 now=None, action_id: str = "", cat: str = ""):
        self.store = store
        self.situation = situation
        self.cfg = cfg
        self.rng = rng
        self.now = now
        self.action_id = action_id
        self.cat = cat


def context(store, situation: str, **kw) -> Ctx:
    return Ctx(store, situation, **kw)


@dataclass
class Line:
    """一条记忆台词。``key`` 是去重键，``negative`` 决定要不要过亲密度门槛。"""
    text: str
    key: str = ""
    negative: bool = False


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
_PREF_PREFIX = re.compile(
    r"^(?:很|超|特别|最|挺|真的|非常|比较|有点)*"
    r"(?:喜欢|爱喝|爱吃|爱玩|爱看|爱|中意|讨厌|烦|受不了|不爱|不喜欢)")
_LEAD_VERB = re.compile(r"^(?:喝|吃|玩|看|听|打|买|用|搞|做|抽|养|穿|读|学)")
_TAIL = re.compile(r"[了啊呀吧呢哦嘛哈呗啦噢哟]+$")


def _noun(text: str) -> str:
    """'喜欢喝冰美式' / '爱喝冰美式了' → '冰美式'。削不动就原样返回。"""
    t = (text or "").strip()
    t = _TAIL.sub("", t)
    t = _PREF_PREFIX.sub("", t)
    t = _LEAD_VERB.sub("", t)
    t = _TAIL.sub("", t).strip()
    return t or (text or "").strip()


def _now(store, override=None) -> datetime:
    if override is not None:
        return override
    fn = getattr(store, "_now", None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            pass
    return datetime.now().astimezone()


def _minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _top(prof: dict, field: str) -> str:
    items = prof.get(field) or []
    if not items:
        return ""
    return _noun(items[0].get("text", ""))


def _first_of_kind(store, kind: str) -> dict | None:
    for e in store.episodes():
        if e.get("kind") == kind:
            return e
    return None


def _recent_event(store, now: datetime) -> dict | None:
    """最近两周内的一条 event / mood。太老的别翻，翻出来像查户口。"""
    for e in store.episodes():
        if e.get("kind") not in ("event", "mood"):
            continue
        if schema.days_since(e.get("ts", ""), now) > EVENT_MAX_AGE_DAYS:
            continue
        text = (e.get("text") or "").strip()
        if 2 <= len(text) <= 24:
            return e
    return None


def _top_fact(store) -> dict | None:
    facts = [f for f in (store.profile().get("facts") or [])
             if float(f.get("confidence", 0)) >= 0.8 and not f.get("pinned")]
    if not facts:
        return None
    text = (facts[0].get("text") or "").strip()
    return facts[0] if 2 <= len(text) <= 20 else None


# --------------------------------------------------------------------------
# 四类情境的候选台词
# --------------------------------------------------------------------------
def _greet(store, ctx, now: datetime) -> list[Line]:
    """启动问候。深夜 / 连续天数 / 答应过的事 / 称呼 / 喜好。"""
    out: list[Line] = []
    rel = store.relationship()
    prof = store.profile()
    stats = store.data.get("stats") or {}
    ln = stats.get("late_nights") or {}

    minute = _minute_of_day(now)
    late = minute >= LATE_START or minute < LATE_END
    consec = int(ln.get("consecutive") or 0)
    if late and consec >= 2:
        out.append(Line("又是这个点。", "late"))
        out.append(Line(f"连着第 {consec} 天这个点开机，我记着呢。", "late"))
    elif late and consec == 1:
        out.append(Line("这个点还没睡？我陪你坐会儿。", "late"))

    streak = int(rel.get("streak_days") or 1)
    if streak >= 3:
        out.append(Line(f"连续 {streak} 天报到了，你比我还准时。", "streak"))

    promise = _first_of_kind(store, "promise")
    if promise:
        out.append(Line(f"说好的「{promise['text']}」，别忘了。",
                        f"promise:{promise.get('id', '')}"))

    name = prof.get("call_me") or ""
    if name and int(rel.get("intimacy") or 0) >= 40:
        out.append(Line(f"{name}，今天也来了。", "call_me"))

    like = _top(prof, "likes")
    if like:
        out.append(Line(f"{like}今天续上了吗？", f"like:{like}"))
    return out


def _click(store, ctx, now: datetime) -> list[Line]:
    """被戳的时候。知道你喜欢什么，也知道你烦什么。"""
    out: list[Line] = []
    prof = store.profile()

    like = _top(prof, "likes")
    if like:
        out.append(Line(f"我知道你喜欢{like}，我记着呢", f"like:{like}"))
        out.append(Line(f"戳吧戳吧，{like}又不能替你干活", f"like:{like}"))

    dislike = _top(prof, "dislikes")
    if dislike:
        out.append(Line(f"又要碰{dislike}了？我先替你烦", f"dislike:{dislike}",
                        negative=True))

    habit = _top(prof, "habits")
    if habit:
        # 记下来的习惯基本是熬夜久坐这类，按敏感信息处理
        out.append(Line(f"{habit}，我数着呢", f"habit:{habit}", negative=True))
    return out


def _idle(store, ctx, now: datetime) -> list[Line]:
    """空闲自说自话。翻一件最近的事，或提一句记着的事实。"""
    out: list[Line] = []
    prof = store.profile()

    like = _top(prof, "likes")
    if like:
        out.append(Line(f"你那个{like}，今天续了吗", f"like:{like}"))

    ep = _recent_event(store, now)
    if ep:
        text = ep["text"]
        out.append(Line(f"上次你说{text}，后来呢", f"ep:{ep.get('id', '')}"))
        out.append(Line(f"还在想你说的「{text}」", f"ep:{ep.get('id', '')}"))

    fact = _top_fact(store)
    if fact:
        out.append(Line(f"{fact['text']}，我记着呢", f"fact:{fact.get('id', '')}"))
    return out


def _anniversary(store, ctx, now: datetime) -> list[Line]:
    """认识第 N 天。只在有纪念意义的数字上开口。"""
    days = int(store.relationship().get("days_together") or 1)
    if days < 7:
        return []
    if not (days in ANNIV_DAYS or (days >= 100 and days % 100 == 0)):
        return []
    return [
        Line(f"第 {days} 天了。你居然还没把我删掉。", f"anniv:{days}"),
        Line(f"{days} 天，我还在。", f"anniv:{days}"),
    ]


def _greeting(store, ctx, now: datetime) -> list[Line]:
    """启动问候的合成池：纪念日绝对优先，没有纪念日才轮得到日常问候。

    合成一个池子而不是分别掷两次骰子——两次掷骰会让开场白有 58% 的概率
    被记忆接管，那就不是"偶尔提一句"，是"每次见面都在查户口"。
    """
    ann = _anniversary(store, ctx, now)
    if ann:
        return ann
    return _greet(store, ctx, now)


_BUILDERS = {
    "greet": _greet,
    "click": _click,
    "idle": _idle,
    "anniversary": _anniversary,
    "greeting": _greeting,
}


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------
def candidates(store, situation: str, now: datetime | None = None) -> list[Line]:
    """某情境下所有能说的记忆台词（**不含**概率闸门和去重）。

    给面板预览和测试用。真实台词走 ``pick_persona``。
    """
    build = _BUILDERS.get(situation)
    if build is None or store is None:
        return []
    try:
        raw = build(store, None, _now(store, now))
    except Exception:
        return []
    return [ln for ln in raw if ln.text and len(ln.text) <= MAX_LINE]


def pick_persona(ctx, last: str | None = None) -> str | None:
    """按三条约束挑一句记忆台词。挑不到就返回 None，让调用方回落到原台词池。

    **永远不抛异常**——台词是装饰，不能因为记忆文件畸形就让桌宠崩掉。
    """
    try:
        return _pick(ctx, last)
    except Exception:
        return None


def _pick(ctx, last: str | None) -> str | None:
    store = getattr(ctx, "store", None)
    if store is None:
        return None

    cfg = getattr(ctx, "cfg", None) or {}
    if cfg.get("memory_enabled") is False or cfg.get("memory_speak") is False:
        return None

    situation = getattr(ctx, "situation", "") or ""
    build = _BUILDERS.get(situation)
    if build is None:
        return None

    rng = getattr(ctx, "rng", None) or random

    # 约束 1：先掷骰子再干活。大多数时候这里就返回了。
    if rng.random() >= VOICE_RATE:
        return None

    now = _now(store, getattr(ctx, "now", None))
    raw = build(store, ctx, now)

    intimacy = int((store.relationship() or {}).get("intimacy") or 0)
    ok: list[Line] = []
    for ln in raw:
        if not ln.text or len(ln.text) > MAX_LINE:
            continue
        if last and ln.text == last:
            continue
        if ln.negative and intimacy < NEGATIVE_MIN_INTIMACY:   # 约束 3
            continue
        if store.spoken_today(ln.key):                         # 约束 2
            continue
        ok.append(ln)
    if not ok:
        return None

    line = rng.choice(ok)
    store.mark_spoken(line.key)
    return line.text


def greeting(store, cfg=None, rng=None, now=None) -> str | None:
    """启动问候专用：纪念日优先于普通问候。

    节日仍然走 ``lines.for_festival``（一年就几次，优先级最高），这里只处理
    "今天正好是认识第 N 天"和"又是这个点"这两种开场。
    """
    ctx = Ctx(store, "greeting", cfg=cfg, rng=rng, now=now)
    return pick_persona(ctx)
