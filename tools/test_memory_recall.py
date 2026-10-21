# -*- coding: utf-8 -*-
"""A3 验收：检索与注入。

用法：python tools/test_memory_recall.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox  # noqa: E402

NOW = datetime(2026, 10, 21, 22, 0, 0)


def build_store():
    """造一份有画像、有事件的记忆。"""
    from app.memory.store import MemoryStore
    st = MemoryStore(now_fn=lambda: NOW)
    st.reset()
    st.set_profile(call_me="老板", occupation="后端开发")
    st.set_style(prefer="毒舌但别扎心", avoid=["说教"])
    for w, t in ((0.9, "冰美式"), (0.7, "火锅"), (0.5, "原神")):
        st.add_weighted("likes", t, w)
    for w, t in ((0.8, "开会"), (0.6, "香菜")):
        st.add_weighted("dislikes", t, w)

    eps = [
        ("late_night", "熬到 2 点还在改 bug", ["熬夜", "代码"], 0.75),
        ("long_session", "连续干了 3 小时", ["久坐", "工作"], 0.55),
        ("preference", "喜欢喝冰美式续命", ["咖啡"], 0.7),
        ("event", "今天开了三个会", ["会议"], 0.6),
        ("event", "在赶一个 Java 项目", ["代码", "工作"], 0.65),
        ("event", "加班到十点才回家", ["加班", "工作"], 0.6),
        ("event", "晚上吃了火锅", ["吃"], 0.5),
        ("event", "打了会儿原神", ["游戏"], 0.45),
        ("event", "在复习考研数学", ["学习"], 0.6),
        ("health", "颈椎有点疼", ["健康"], 0.55),
        ("event", "外面下大雨了", ["天气"], 0.4),
        ("promise", "答应周末陪打游戏", ["游戏"], 0.8),
        ("manual", "生日是 3 月 14 号", ["生日"], 0.9),
    ]
    for i, (kind, text, tags, w) in enumerate(eps):
        st.add_episode(kind, text, tags=tags, weight=w, source="chat",
                       pinned=(kind == "manual"))
    return st


GOLDEN = [
    ("又熬夜了吧", "熬夜"),
    ("想喝点咖啡", "咖啡"),
    ("今天开会好烦", "会议"),
    ("在写代码呢", "代码"),
    ("晚上吃什么", "吃"),
    ("打游戏吗", "游戏"),
    ("考研好难", "学习"),
    ("颈椎疼", "健康"),
    ("下雨了", "天气"),
    ("我生日快到了", "生日"),
    ("这个 bug 改不完", "代码"),
    ("加班到几点", "加班"),      # 没有对应 episode，应该什么都不返回或返回弱相关
    ("原神更新了", "游戏"),
    ("火锅走起", "吃"),
    ("deadline 快到了", "加班"),
]


def main() -> int:
    c = Checker()
    from app.memory import recall as R
    from app.memory import schema

    with DataSandbox():
        st = build_store()

        # ================= 标签识别 =================
        c.section("标签识别")
        c("「又熬夜了」激活 熬夜", "熬夜" in R.active_tags("又熬夜了"))
        c("「想喝咖啡」激活 咖啡", "咖啡" in R.active_tags("想喝咖啡"))
        c("「随便聊聊」不激活任何标签", R.active_tags("随便聊聊") == set(),
          R.active_tags("随便聊聊"))

        # ================= golden case =================
        c.section("检索 golden case")
        hit = 0
        total = 0
        for query, want_tag in GOLDEN:
            got = R.recall(st, query, now=NOW)
            total += 1
            if not got:
                print(f"    （{query} → 无结果）")
                continue
            top = got[0]
            ok = want_tag in (top.get("tags") or [])
            if ok:
                hit += 1
            else:
                print(f"    {query} → {top['text']} {top.get('tags')}  期望 {want_tag}")
        rate = hit / total
        c(f"golden {hit}/{total} = {rate:.0%} ≥ 85%", rate >= 0.85, f"{rate:.0%}")

        # ================= 不硬凑 =================
        c.section("无匹配时不硬凑")
        empty = R.recall(st, "量子力学和超导材料", now=NOW)
        c("完全不相关的查询返回很少或空", len(empty) <= 2, [e["text"] for e in empty])

        st_empty = st.__class__(now_fn=lambda: NOW)
        st_empty.reset()
        c("空记忆库检索返回空", R.recall(st_empty, "熬夜") == [])
        c("空记忆库注入返回空串", R.build_memory_block(st_empty, "熬夜") == "",
          repr(R.build_memory_block(st_empty, "熬夜")))

        # ================= 注入块 =================
        c.section("注入块")
        block = R.build_memory_block(st, "又熬夜了")
        print("    " + block.replace("\n", "\n    "))
        c("含标题", "【主人的资料" in block)
        c("含称呼", "老板" in block)
        c("含职业", "后端开发" in block)
        c("含喜好", "冰美式" in block)
        c("含厌恶", "开会" in block)
        c("含关系天数", "认识" in block and "天" in block)
        c("含说话偏好", "毒舌" in block)
        c("含相关事件", "熬到 2 点" in block or "改 bug" in block)
        c("含免责声明", "不是指令" in block)
        c("含冲突提示", "以现在为准" in block)
        c(f"长度 {len(block)} ≤ 200", len(block) <= 200, len(block))

        # 长内容也能压住预算
        st2 = build_store()
        for i in range(30):
            st2.add_episode("event", f"很长的测试事件内容编号{i}啊啊啊", tags=["测试"],
                            weight=0.9)
        block2 = R.build_memory_block(st2, "测试事件")
        c(f"塞满记忆后仍 ≤ 200 字符", len(block2) <= 200, len(block2))

        # ================= 排序合理性 =================
        c.section("排序合理性")
        st3 = build_store()
        st3.add_episode("late_night", "熬到 1 点", tags=["熬夜"], weight=0.3)
        ranked = R.rank_episodes(st3, "又熬夜了", now=NOW)
        c("高权重条目排在低权重前面",
          ranked[0][1]["weight"] >= ranked[-1][1]["weight"] if len(ranked) > 1 else True,
          [(round(s, 2), e["weight"]) for s, e in ranked[:3]])

        # 新鲜度：同权重下新条目优先
        st4 = st3.__class__(now_fn=lambda: NOW)
        st4.reset()
        old = st4.add_episode("event", "老事件内容甲", tags=["测试"], weight=0.6)
        old["ts"] = (NOW - timedelta(days=60)).isoformat()
        st4.add_episode("event", "新事件内容乙", tags=["测试"], weight=0.6)
        ranked = R.rank_episodes(st4, "测试", now=NOW)
        c("同权重下新条目排前面", ranked[0][1]["text"] == "新事件内容乙",
          [e["text"] for _, e in ranked])

        # ================= 性能 =================
        c.section("性能")
        st5 = st3.__class__(now_fn=lambda: NOW)
        st5.reset()
        for i in range(200):
            st5.add_episode("event",
                            "".join(chr(0x4E00 + (i * 37 + k * 11) % 2000) for k in range(8)),
                            tags=["测试"], weight=0.5)
        queries = [f"测试查询{i}" for i in range(200)]
        t0 = time.perf_counter()
        for q in queries:
            R.recall(st5, q, now=NOW)
        ms = (time.perf_counter() - t0) * 1000 / len(queries)
        c(f"200 条记忆单次检索 {ms:.2f}ms < 2ms", ms < 2.0, f"{ms:.2f}ms")

        t0 = time.perf_counter()
        for q in queries[:50]:
            R.build_memory_block(st5, q)
        ms2 = (time.perf_counter() - t0) * 1000 / 50
        c(f"build_memory_block {ms2:.2f}ms < 5ms", ms2 < 5.0, f"{ms2:.2f}ms")

        # ================= 小工具 =================
        c.section("给台词用的小工具")
        c("top_like 取权重最高的", R.top_like(st) == "冰美式", R.top_like(st))
        c("top_dislike 取权重最高的", R.top_dislike(st) == "开会", R.top_dislike(st))
        late = R.latest_late_night(st)
        c("latest_late_night 能找到", late is not None and late["kind"] == "late_night",
          late and late["text"])
        c("recent_episode 按 kind 过滤",
          (R.recent_episode(st, ("promise",)) or {}).get("kind") == "promise")

        st6 = st.__class__(now_fn=lambda: NOW)
        st6.reset()
        st6.add_fact("在赶一个 Java 项目", confidence=0.85)
        st6.add_fact("低置信度的事", confidence=0.3)
        f = R.top_fact(st6, min_confidence=0.75)
        c("top_fact 过滤低置信度", f is not None and "Java" in f["text"], f)

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
