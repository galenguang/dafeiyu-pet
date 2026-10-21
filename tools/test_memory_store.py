# -*- coding: utf-8 -*-
"""A0 验收：记忆数据层。

用法：python tools/test_memory_store.py
全程离线，套 DataSandbox，不碰 %APPDATA%。
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox  # noqa: E402


def main() -> int:
    c = Checker()
    from app.memory import schema
    from app.memory.store import MemoryStore

    with DataSandbox() as sb:
        # ---------- 1. 首启 ----------
        c.section("首启与默认结构")
        st = MemoryStore()
        p = sb.path("memory.json")
        c("空目录首启生成 memory.json", os.path.exists(p), p)
        d = json.load(open(p, encoding="utf-8"))
        c("schema 版本正确", d.get("schema") == schema.SCHEMA_VERSION, d.get("schema"))
        c("profile 有 call_me", "call_me" in d["profile"])
        c("relationship 有 first_meet", bool(d["relationship"]["first_meet"]))
        c("episodes 是空列表", d["episodes"] == [])
        c("不留 .tmp", not os.path.exists(p + ".tmp"))

        # ---------- 2. 损坏恢复 ----------
        c.section("损坏 JSON 恢复")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ 这不是合法 json ,,, ")
        st2 = MemoryStore()
        c("损坏后仍能构造", st2.data["schema"] == schema.SCHEMA_VERSION)
        corrupt = [f for f in os.listdir(sb.root) if ".corrupt-" in f]
        c("坏文件已改名备份", len(corrupt) == 1, corrupt)
        c("重建后文件可读", isinstance(json.load(open(p, encoding="utf-8")), dict))

        # ---------- 3. 畸形输入修复 ----------
        c.section("畸形输入修复（永不抛异常）")
        cases = [
            ("None", None),
            ("字符串", "oops"),
            ("列表", [1, 2, 3]),
            ("空字典", {}),
            ("profile 是字符串", {"profile": "x"}),
            ("episodes 里混垃圾", {"episodes": [None, 1, "a", {"text": "有效"}, {"no": "text"}]}),
            ("likes 里混垃圾", {"profile": {"likes": [None, {"text": ""}, "咖啡", {"text": "茶"}]}}),
            ("数值越界", {"relationship": {"intimacy": 999, "streak_days": -5}}),
            ("daily 格式错", {"stats": {"daily": {"bad": 1, "2026-10-21": "x"}}}),
        ]
        all_ok = True
        for name, bad in cases:
            try:
                m = schema.normalize_memory(bad)
                assert isinstance(m, dict) and m["schema"] == schema.SCHEMA_VERSION
            except Exception as exc:
                all_ok = False
                print("    崩溃:", name, exc)
        c(f"{len(cases)} 种畸形输入全部安全", all_ok)

        m = schema.normalize_memory({"relationship": {"intimacy": 999, "streak_days": -5}})
        c("intimacy 夹到 0-100", m["relationship"]["intimacy"] == 100)
        c("streak_days 夹到 >=1", m["relationship"]["streak_days"] == 1)
        m = schema.normalize_memory({"episodes": [None, 1, "a", {"text": "有效"}, {"no": "x"}]})
        c("垃圾 episode 被丢掉，只留有效项", len(m["episodes"]) == 1, m["episodes"])
        m = schema.normalize_memory({"profile": {"likes": [None, {"text": ""}, "咖啡", {"text": "茶"}]}})
        c("likes 里字符串和 dict 都能收", [x["text"] for x in m["profile"]["likes"]] == ["咖啡", "茶"],
          m["profile"]["likes"])

        # ---------- 4. 容量淘汰 ----------
        c.section("容量上限与淘汰")
        st3 = MemoryStore()
        st3.reset()

        def junk(i: int) -> str:
            # 造真正互不相似的文本：bigram 重叠极低，不会被去重吃掉
            return "".join(chr(0x4E00 + (i * 37 + k * 11) % 2000) for k in range(10))

        for i in range(260):
            st3.add_episode("event", junk(i), weight=0.1 + (i % 5) * 0.1)
        c("episodes 不超过 200", len(st3.data["episodes"]) <= 200, len(st3.data["episodes"]))
        c("淘汰确实发生了", len(st3.data["episodes"]) == 200, len(st3.data["episodes"]))

        # pinned / promise 永不淘汰
        st3.reset()
        st3.add_episode("promise", "答应主人周末陪她打游戏", pinned=False, weight=0.05)
        st3.add_episode("manual", "主人手动加的重要事", pinned=True, weight=0.05)
        for i in range(240):
            st3.add_episode("event", junk(i), weight=0.9)
        texts = [e["text"] for e in st3.data["episodes"]]
        c("promise 没被淘汰", any("打游戏" in t for t in texts))
        c("pinned 没被淘汰", any("重要事" in t for t in texts))

        # ---------- 5. 去重 ----------
        c.section("去重与合并")
        st3.reset()
        a = st3.add_episode("late_night", "昨晚又熬到一点")
        b = st3.add_episode("late_night", "昨晚又熬到一点")
        c("完全相同不重复入库", len(st3.data["episodes"]) == 1, len(st3.data["episodes"]))
        c("重复时 hits 增加", b and b["hits"] == 2, b and b["hits"])
        st3.reset()
        st3.add_episode("preference", "喜欢冰美式咖啡", weight=0.6)
        st3.add_episode("preference", "喜欢冰美式咖啡啊", weight=0.6)
        c("近似文本也算同一条", len(st3.data["episodes"]) == 1, len(st3.data["episodes"]))

        st3.reset()
        st3.add_episode("late_night", "熬夜了", dedupe_minutes=30)
        st3.add_episode("late_night", "熬夜了", dedupe_minutes=30)
        st3.add_episode("late_night", "熬夜了", dedupe_minutes=30)
        c("dedupe_minutes 窗口内只留一条", len(st3.data["episodes"]) == 1)

        # ---------- 6. facts / likes / 关系 ----------
        c.section("facts、喜好与关系推进")
        st3.reset()
        st3.add_fact("在做一个桌宠项目", confidence=0.6)
        st3.add_fact("在做一个桌宠项目", confidence=0.6)
        c("fact 不重复", len(st3.data["profile"]["facts"]) == 1)
        c("fact 重复时置信度上升", st3.data["profile"]["facts"][0]["confidence"] > 0.6,
          st3.data["profile"]["facts"][0]["confidence"])

        st3.add_weighted("likes", "冰美式", 0.7)
        st3.add_weighted("likes", "冰美式", 0.7)
        st3.add_weighted("likes", "拿铁", 0.5)
        c("likes 去重并排序", [x["text"] for x in st3.data["profile"]["likes"]] == ["冰美式", "拿铁"],
          st3.data["profile"]["likes"])

        before = st3.data["relationship"]["intimacy"]
        st3.note_chat("在吗", "在")
        c("聊天涨亲密度", st3.data["relationship"]["intimacy"] == before + 1)
        c("聊天计数 +1", st3.data["relationship"]["total_chats"] == 1)
        c("daily 有今天记录", schema.today_str() in st3.data["stats"]["daily"])

        # 亲密度封顶
        for _ in range(200):
            st3.note_chat("x", "y")
        c("亲密度封顶 100", st3.data["relationship"]["intimacy"] == 100)

        # ---------- 7. 跨天 ----------
        c.section("跨天推进")
        st4 = MemoryStore()
        st4.reset()
        from datetime import date, timedelta
        st4.data["relationship"]["last_seen_date"] = (date.today() - timedelta(days=1)).isoformat()
        st4.data["relationship"]["streak_days"] = 5
        changed = st4.touch_day()
        c("跨天返回 True", changed is True)
        c("连续天数 +1", st4.data["relationship"]["streak_days"] == 6,
          st4.data["relationship"]["streak_days"])

        st4.data["relationship"]["last_seen_date"] = (date.today() - timedelta(days=5)).isoformat()
        st4.touch_day()
        c("断档后连续天数归 1", st4.data["relationship"]["streak_days"] == 1)

        # ---------- 8. 原子写 ----------
        c.section("原子写与备份")
        st4.flush()
        c("flush 后无 .tmp 残留", not os.path.exists(st4.path + ".tmp"))
        st4.mark_dirty()
        st4.flush()
        c("第二次写产生 .bak", os.path.exists(st4.bak_path))
        c(".bak 是合法 JSON", isinstance(json.load(open(st4.bak_path, encoding="utf-8")), dict))

        # 节流：mark_dirty 后立刻 maybe_save 不该写
        st4.mark_dirty()
        wrote = st4.maybe_save()
        c("节流期内不重复写盘", wrote is False, wrote)

        # ---------- 9. 事件队列 ----------
        c.section("事件队列")
        st4.drain()
        st4.emit("say", "记住啦")
        st4.emit("changed")
        evs = st4.drain()
        c("事件按序取出", [e["kind"] for e in evs] == ["say", "changed"], evs)
        c("drain 后清空", st4.drain() == [])

        # ---------- 10. reset ----------
        c.section("reset 保留初识日期")
        st5 = MemoryStore()
        first = st5.data["relationship"]["first_meet"]
        st5.add_episode("event", "要被清掉的事")
        st5.add_fact("要被清掉的事实")
        st5.reset()
        c("episodes 清空", st5.data["episodes"] == [])
        c("facts 清空", st5.data["profile"]["facts"] == [])
        c("first_meet 保留", st5.data["relationship"]["first_meet"] == first)

        # ---------- 11. 性能 ----------
        c.section("性能")
        big = st5.snapshot()
        for i in range(200):
            big["episodes"].append({"id": f"e_{i:04d}", "ts": schema.now_iso(),
                                    "kind": "event", "text": f"测试事件{i}号内容",
                                    "tags": ["测试"], "weight": 0.5, "decay": "fast",
                                    "hits": 1, "source": "chat", "pinned": False})
        t0 = time.perf_counter()
        for _ in range(1000):
            schema.normalize_memory(big)
        ms = (time.perf_counter() - t0) * 1000
        c(f"200 条记忆 normalize×1000 = {ms:.0f}ms < 1500ms", ms < 1500, f"{ms:.0f}ms")

        t0 = time.perf_counter()
        for _ in range(1000):
            st5.snapshot()
        ms2 = (time.perf_counter() - t0) * 1000
        c(f"snapshot×1000 = {ms2:.0f}ms < 1000ms", ms2 < 1000, f"{ms2:.0f}ms")

        c.section("体积")
        st5.flush()
        size = os.path.getsize(st5.path)
        c(f"memory.json {size / 1024:.1f}KB < 200KB", size < 200 * 1024)

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
