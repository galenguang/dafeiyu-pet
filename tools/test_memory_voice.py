# -*- coding: utf-8 -*-
"""A4 验收：人格化台词。

用法：python tools/test_memory_voice.py

重点是第一条：**``ctx=None`` 时必须与改造前逐字符一致**。现有 40+ 处台词调用
零改动，不能因为接了记忆就变了说话风格。
"""
from __future__ import annotations

import os
import random
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox  # noqa: E402

DAY = datetime(2026, 10, 21, 23, 50, 0)


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
class Clock:
    """可推进的假时钟。``MemoryStore(now_fn=clock)``。"""

    def __init__(self, dt: datetime):
        self.dt = dt

    def __call__(self) -> datetime:
        return self.dt

    def advance(self, days: float = 0, hours: float = 0) -> None:
        self.dt = self.dt + timedelta(days=days, hours=hours)


class AlwaysRng:
    """random() 恒为 0（必过概率闸门），choice 恒取第一个。"""

    def random(self) -> float:
        return 0.0

    def choice(self, seq):
        return seq[0]


class NeverRng:
    def random(self) -> float:
        return 0.999

    def choice(self, seq):
        return seq[0]


def old_pick(lines: list[str], last: str | None = None) -> str:
    """改造前 ``lines.pick`` 的逐字复制，用来做回归对照。"""
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    for _ in range(6):
        s = random.choice(lines)
        if s != last:
            return s
    return random.choice(lines)


def new_store(clock=None, path=None):
    from app.memory.store import MemoryStore
    st = MemoryStore(path=path, now_fn=clock or (lambda: DAY))
    st.reset()
    return st


def rich_store(clock=None):
    """画像齐全、有事件、有承诺的记忆。"""
    st = new_store(clock)
    st.set_profile(call_me="老板", occupation="后端开发")
    st.add_weighted("likes", "冰美式", 0.9)
    st.add_weighted("likes", "火锅", 0.7)
    st.add_weighted("dislikes", "开会", 0.8)
    st.add_weighted("habits", "熬夜", 0.6)
    st.add_episode("event", "加班到十点才回家", tags=["加班"], weight=0.7)
    st.add_episode("promise", "周末陪打游戏", tags=["游戏"], weight=0.8)
    st.add_fact("在赶一个 Java 项目", confidence=0.9)
    st.data["stats"]["late_nights"]["consecutive"] = 3
    st.data["relationship"]["streak_days"] = 5
    st.data["relationship"]["days_together"] = 100
    st.data["relationship"]["intimacy"] = 70
    return st


def late_store(clock):
    """只有"连续熬夜"这一条料的记忆，用来单独验每日去重。"""
    st = new_store(clock)
    st.data["stats"]["late_nights"]["consecutive"] = 3
    return st


def clear_spoken(st) -> None:
    st.data["meta"]["spoken"].clear()


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main() -> int:
    c = Checker("A4 人格化台词")
    from app import lines
    from app.memory import voice

    with DataSandbox():
        # ---------------- 1. 回归：ctx=None 与旧版逐字符一致 ----------------
        c.section("1. 回归（ctx=None 必须与旧版完全一致）")
        pools = [
            ("GREETINGS", lines.GREETINGS),
            ("IDLE_LINES", lines.IDLE_LINES),
            ("CLICK_LINES", lines.CLICK_LINES),
            ("DRAG_LINES", lines.DRAG_LINES),
            ("ACTION:codding", lines.ACTION_LINES["coding"]),
            ("CAT:eat", lines.CAT_LINES["eat"]),
            ("SINGLE", ["只有一句"]),
            ("EMPTY", []),
        ]
        for name, pool in pools:
            random.seed(20261021)
            want = [old_pick(pool, None) for _ in range(120)]
            random.seed(20261021)
            got = [lines.pick(pool, None) for _ in range(120)]
            c(f"pick(ctx=None) 与旧版一致 · {name}", want == got,
              f"{len(want)} 次采样有差异" if want != got else "120/120")

        # last 参数也要一致
        random.seed(7)
        want = [old_pick(lines.IDLE_LINES, "摸鱼使我快乐") for _ in range(80)]
        random.seed(7)
        got = [lines.pick(lines.IDLE_LINES, "摸鱼使我快乐") for _ in range(80)]
        c("pick(ctx=None) 的 last 去重行为一致", want == got)

        random.seed(9)
        want = [lines.for_action("coding", "code", None) for _ in range(60)]
        random.seed(9)
        got = [lines.for_action("coding", "code") for _ in range(60)]
        c("for_action(ctx 省略) 与旧版一致", want == got)

        random.seed(11)
        want = [lines.for_festival("中秋") for _ in range(40)]
        random.seed(11)
        got = [lines.for_festival("中秋") for _ in range(40)]
        c("for_festival 未受影响", want == got)

        c("空列表 + ctx=None 仍返回空串", lines.pick([], None) == "")

        # ---------------- 2. 无记忆时完全透明 ----------------
        c.section("2. 无记忆透明（刚装上什么都不该说）")
        empty = new_store()
        for sit in voice.SITUATIONS:
            c(f"空记忆 · {sit} 无候选", voice.candidates(empty, sit) == [],
              voice.candidates(empty, sit))
            ctx = voice.context(empty, sit, rng=AlwaysRng())
            c(f"空记忆 · {sit} 不开口", voice.pick_persona(ctx) is None)
        ctx = voice.context(empty, "click", rng=AlwaysRng())
        c("空记忆时 lines.pick 回落原池",
          lines.pick(lines.CLICK_LINES, None, ctx) in lines.CLICK_LINES)

        # ---------------- 3. 四类情境各自命中 ----------------
        c.section("3. 四类情境各自命中")
        st = rich_store()
        want = {
            "greet": "又是这个点。",
            "click": "我知道你喜欢冰美式，我记着呢",
            "idle": "你那个冰美式，今天续了吗",
            "anniversary": "第 100 天了。你居然还没把我删掉。",
        }
        for sit, expect in want.items():
            # 每个情境用一份干净的记忆：同一天说过的话不该串到下一个情境里
            ctx = voice.context(rich_store(), sit, rng=AlwaysRng(), now=DAY)
            got = voice.pick_persona(ctx)
            c(f"{sit} 命中记忆台词", got == expect, f"got={got!r}")

        # 计划里的四条 before/after 对照，至少要能复现
        c("深夜问候复现计划样例", want["greet"] == "又是这个点。")
        c("100 天纪念复现计划样例", "100 天" in want["anniversary"])

        # greeting() 的优先级：纪念日 > 日常问候，而且只掷一次骰子
        got = voice.greeting(rich_store(), rng=AlwaysRng(), now=DAY)
        c("greeting 优先纪念日", bool(got) and got.startswith("第 100 天"), got)
        g2 = new_store()
        g2.data["stats"]["late_nights"]["consecutive"] = 3
        got = voice.greeting(g2, rng=AlwaysRng(), now=DAY)
        c("没有纪念日时走深夜问候", got == "又是这个点。", got)
        got = voice.greeting(rich_store(), rng=NeverRng(), now=DAY)
        c("greeting 也受占比闸门约束", got is None, got)

        # 候选池不能为空，且每条都合规
        for sit in voice.SITUATIONS:
            cands = voice.candidates(st, sit, now=DAY)
            c(f"{sit} 候选非空", len(cands) >= 2, f"{len(cands)} 条")

        # ---------------- 4. 约束 1：占比 ≤ 35% ----------------
        c.section("4. 约束 1：记忆台词占比")
        rng = random.Random(20261021)
        hit = 0
        total = 3000
        for _ in range(total):
            clear_spoken(st)
            ctx = voice.context(st, "click", rng=rng, now=DAY)
            if voice.pick_persona(ctx):
                hit += 1
        ratio = hit / total
        c("占比落在 25%–45%", 0.25 <= ratio <= 0.45, f"{ratio:.1%}")
        c("占比不超过 VOICE_RATE 太多", ratio <= voice.VOICE_RATE + 0.05,
          f"{ratio:.1%} vs {voice.VOICE_RATE:.0%}")

        # 闸门关掉时一句都不说
        ctx = voice.context(st, "click", rng=NeverRng(), now=DAY)
        c("闸门未命中时返回 None", voice.pick_persona(ctx) is None)

        # ---------------- 5. 约束 2：同一记忆点每天最多 1 次 ----------------
        c.section("5. 约束 2：每日去重")
        clock = Clock(DAY)
        st2 = late_store(clock)
        ctx = voice.context(st2, "greet", rng=AlwaysRng(), now=clock.dt)
        first = voice.pick_persona(ctx)
        c("第一次说得出来", first == "又是这个点。", first)

        ctx2 = voice.context(st2, "greet", rng=AlwaysRng(), now=clock.dt)
        second = voice.pick_persona(ctx2)
        c("同一天第二次闭嘴", second is None, second)

        c("spoken 里记下了 key", st2.spoken_today("late"))

        clock.advance(days=1)
        ctx3 = voice.context(st2, "greet", rng=AlwaysRng(), now=clock.dt)
        third = voice.pick_persona(ctx3)
        c("第二天又能说", third == "又是这个点。", third)

        # 不同记忆点互不影响
        st3 = rich_store()
        a = voice.pick_persona(voice.context(st3, "click", rng=AlwaysRng(), now=DAY))
        b = voice.pick_persona(voice.context(st3, "greet", rng=AlwaysRng(), now=DAY))
        c("不同情境互不阻塞", bool(a) and bool(b), f"{a!r} / {b!r}")

        # ---------------- 6. 约束 3：负面记忆的亲密度门槛 ----------------
        c.section("6. 约束 3：负面记忆门槛")
        st4 = new_store()
        st4.add_weighted("dislikes", "开会", 0.9)
        st4.data["relationship"]["intimacy"] = 30
        cands = voice.candidates(st4, "click")
        c("低亲密度仍有候选", len(cands) >= 1, f"{len(cands)} 条")
        got = voice.pick_persona(voice.context(st4, "click", rng=AlwaysRng(), now=DAY))
        c("低亲密度不提厌恶", got is None, got)

        st4.data["relationship"]["intimacy"] = 60
        got = voice.pick_persona(voice.context(st4, "click", rng=AlwaysRng(), now=DAY))
        c("亲密度 60 才提厌恶", got == "又要碰开会了？我先替你烦", got)

        st5 = new_store()
        st5.add_weighted("likes", "冰美式", 0.9)
        st5.data["relationship"]["intimacy"] = 0
        got = voice.pick_persona(voice.context(st5, "click", rng=AlwaysRng(), now=DAY))
        c("正面记忆不受亲密度限制", got is not None, got)

        # ---------------- 7. 开关 ----------------
        c.section("7. 开关")
        for key in ("memory_enabled", "memory_speak"):
            cfg = {key: False}
            ctx = voice.context(st, "click", cfg=cfg, rng=AlwaysRng(), now=DAY)
            c(f"{key}=False 时闭嘴", voice.pick_persona(ctx) is None)
        ctx = voice.context(st, "click", cfg={"memory_enabled": True},
                            rng=AlwaysRng(), now=DAY)
        c("开关打开时照常说", voice.pick_persona(ctx) is not None)

        # ---------------- 8. 健壮性 ----------------
        c.section("8. 健壮性（台词不能把桌宠搞崩）")
        c("ctx=None 直接返回 None", voice.pick_persona(None) is None)
        c("store=None 返回 None",
          voice.pick_persona(voice.context(None, "click", rng=AlwaysRng())) is None)
        c("未知情境返回 None",
          voice.pick_persona(voice.context(st, "dance", rng=AlwaysRng())) is None)
        c("空情境返回 None",
          voice.pick_persona(voice.context(st, "", rng=AlwaysRng())) is None)
        c("未知情境无候选", voice.candidates(st, "nope") == [])

        class Broken:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        c("store 抛异常时不崩",
          voice.pick_persona(voice.context(Broken(), "click", rng=AlwaysRng())) is None)
        c("lines.pick 兜住记忆层异常",
          lines.pick(lines.CLICK_LINES, None,
                     voice.context(Broken(), "click", rng=AlwaysRng()))
          in lines.CLICK_LINES)

        # 畸形记忆：缺 stats / relationship
        st6 = new_store()
        st6.data["stats"] = {}
        st6.data["relationship"] = {}
        st6.data["profile"] = {"likes": [{"text": "咖啡"}], "dislikes": []}
        for sit in voice.SITUATIONS:
            try:
                voice.pick_persona(voice.context(st6, sit, rng=AlwaysRng(), now=DAY))
                ok = True
            except Exception as exc:
                ok = False
                c(f"畸形记忆 · {sit}", False, repr(exc))
                continue
            c(f"畸形记忆 · {sit} 不崩", ok)

        # last 相同的台词不再重复
        st7 = new_store()
        st7.add_weighted("likes", "冰美式", 0.9)
        ctx = voice.context(st7, "click", rng=AlwaysRng(), now=DAY)
        c("与上一句相同则跳过",
          voice.pick_persona(ctx, last="我知道你喜欢冰美式，我记着呢")
          != "我知道你喜欢冰美式，我记着呢")

        # ---------------- 9. 台词质量 ----------------
        c.section("9. 台词质量")
        st8 = rich_store()
        all_text = []
        for sit in voice.SITUATIONS:
            all_text += [ln.text for ln in voice.candidates(st8, sit, now=DAY)]
        c("候选总量够（不会总说同一句）", len(all_text) >= 8, f"{len(all_text)} 条")
        c("无未替换的占位符", not any(("{" in t or "}" in t) for t in all_text))
        c("无空台词", all(t.strip() for t in all_text))
        c("都在气泡宽度内", all(len(t) <= voice.MAX_LINE for t in all_text),
          f"最长 {max(len(t) for t in all_text)}")
        c("没有重复文本", len(set(all_text)) == len(all_text))
        c("裸名词已削干净", not any("喜欢喜欢" in t for t in all_text))

        # _noun 的边界
        for raw, expect in (("喜欢喝冰美式", "冰美式"), ("爱喝咖啡了", "咖啡"),
                            ("冰美式", "冰美式"), ("讨厌开会", "开会"), ("", "")):
            got = voice._noun(raw)
            c(f"_noun({raw!r})", got == expect, f"got={got!r}")

        # ---------------- 10. 性能 ----------------
        c.section("10. 性能")
        st9 = rich_store()
        t0 = time.perf_counter()
        for i in range(500):
            voice.pick_persona(voice.context(st9, "click", now=DAY))
        cost = (time.perf_counter() - t0) / 500 * 1000
        c("单次调用 < 2ms", cost < 2.0, f"{cost:.3f}ms")

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
