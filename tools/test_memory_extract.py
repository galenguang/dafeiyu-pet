# -*- coding: utf-8 -*-
"""A1 验收：记忆抽取（规则轨 + 模型轨）。

用法：python tools/test_memory_extract.py
全程离线，套 DataSandbox + FakeLLM。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox, FakeLLM  # noqa: E402


def main() -> int:
    c = Checker()
    from app.memory import rules, schema
    from app.memory.extract import Extractor, json_to_candidates
    from app.memory.store import MemoryStore

    # ================= 规则轨 =================
    c.section("规则抽取")
    cases = [
        ("叫我老王吧", "call_me", "老王"),
        ("以后叫我阿杰", "call_me", "阿杰"),
        ("我叫小明", "call_me", "小明"),
        ("我喜欢喝冰美式", "likes", "冰美式"),
        ("我超爱打游戏", "likes", "游戏"),
        ("我讨厌开会", "dislikes", "开会"),
        ("我不喜欢香菜", "dislikes", "香菜"),
        ("我在做一个桌宠项目", "facts", None),
        ("记住:我对芒果过敏", "facts", None),
    ]
    for text, field, want in cases:
        cands = rules.extract_rules(text)
        got = [x for x in cands if x.field == field]
        ok = bool(got)
        detail = f"{text} -> {[(x.field, x.text) for x in cands]}"
        if ok and want:
            ok = got[0].text == want
        c(f"「{text}」→ {field}", ok, detail)

    c.section("规则抽取的误报防护")
    negatives = ["我喜欢你", "我讨厌这个", "你好呀", "今天天气不错", "我在吗"]
    bad = 0
    for t in negatives:
        for cd in rules.extract_rules(t):
            if cd.field in ("likes", "dislikes") and cd.text in rules.STOP_OBJECTS:
                bad += 1
    c(f"{len(negatives)} 句闲聊不产生垃圾喜好", bad == 0, f"{bad} 条误报")

    cands = rules.extract_rules("记住:我对芒果过敏")
    c("「记住」标记为 pinned", bool(cands) and cands[0].pinned is True)
    c("「记住」置信度高", bool(cands) and cands[0].confidence >= 0.9)

    cands = rules.extract_rules("昨天又熬夜到两点了")
    late = [x for x in cands if x.kind == "late_night"]
    c("熬夜被识别为 episode", bool(late), [(x.field, x.kind, x.text) for x in cands])

    c("强信号词命中", rules.has_strong_signal("我喜欢喝咖啡"))
    c("闲聊不触发强信号", not rules.has_strong_signal("哈哈哈"))

    # ================= 模型输出容错 =================
    c.section("模型输出容错（8 种畸形）")
    GOOD = ('{"profile":{"call_me":"老板","likes":["咖啡"]},'
            '"facts":["在赶一个Java项目"]}')

    shapes = {
        "正常 JSON": GOOD,
        "带 ```json 围栏": "```json\n" + GOOD + "\n```",
        "带 ``` 围栏": "```\n" + GOOD + "\n```",
        "前面有废话": "好的，我整理了一下：\n" + GOOD,
        "尾随逗号": '{"profile":{"call_me":"老板",},"facts":["在赶一个Java项目",]}',
        "中文引号": '{"profile":{"call_me":"老板"},"facts":["在赶一个Java项目"]}'.replace('"', '“', 1),
        "空字符串": "",
        "完全不是 JSON": "我不会整理",
    }
    all_ok = True
    for name, text in shapes.items():
        try:
            obj = __import__("app.llm_raw", fromlist=["loads_lenient"]).loads_lenient(text)
            # 不要求全部解析成功，只要求不抛异常
            _ = json_to_candidates(obj) if obj else []
        except Exception as exc:
            all_ok = False
            print("    崩溃:", name, type(exc).__name__, exc)
    c(f"{len(shapes)} 种输出形态全部不崩", all_ok)

    from app.llm_raw import loads_lenient
    c("正常 JSON 能解析", loads_lenient(GOOD) is not None)
    c("围栏能剥离", loads_lenient("```json\n" + GOOD + "\n```") is not None)
    c("前面废话能跳过", loads_lenient("好的：\n" + GOOD) is not None)
    c("尾随逗号能修", loads_lenient('{"a":1,}') == {"a": 1})
    c("非 JSON 返回 None", loads_lenient("我不会") is None)
    c("空串返回 None", loads_lenient("") is None)

    c.section("输出侧硬约束（不信任模型）")
    obj = {"profile": {"likes": ["咖" * 30, "咖啡"]},
           "facts": ["这" * 50, "正常事实内容"],
           "episodes": [{"kind": "不存在的类型", "text": "有效事件"}]}
    cands = json_to_candidates(obj)
    texts = [x.text for x in cands]
    c("超长文本被丢弃", not any(len(t) > 40 for t in texts), texts)
    c("合法文本保留", "咖啡" in texts, texts)
    c("未知 kind 降级为 event",
      all(x.kind in schema.EPISODE_KINDS for x in cands if x.field == "episodes"))
    c("单次最多 5 条", len(cands) <= 5, len(cands))

    c("第一人称被去掉",
      json_to_candidates({"facts": ["我喜欢喝咖啡"]})[0].text == "喜欢喝咖啡",
      json_to_candidates({"facts": ["我喜欢喝咖啡"]})[0].text)

    # ================= 端到端：模型轨 =================
    with DataSandbox() as sb, FakeLLM([
        '{"profile":{"call_me":"老板","likes":["冰美式"],"dislikes":["开会"]},'
        '"facts":["在赶一个Java项目"],'
        '"episodes":[{"kind":"event","text":"今天被拉去开了三个会"}]}'
    ]) as fake:
        st = MemoryStore()
        st.reset()
        ex = Extractor(st, lambda: fake.llm(), lambda: {
            "memory_enabled": True, "memory_llm_extract": True,
            "memory_extract_every": 1, "memory_extract_min_gap": 0,
        })

        c.section("端到端：一次完整抽取")
        ex.on_user("我叫老板，喜欢喝冰美式，最讨厌开会，最近在赶一个Java项目")
        n = ex.run_once()
        c("抽取写入了条目", n > 0, n)
        prof = st.data["profile"]
        c("称呼入库", prof["call_me"] == "老板", prof["call_me"])
        c("喜欢入库", any(x["text"] == "冰美式" for x in prof["likes"]), prof["likes"])
        c("讨厌入库", any(x["text"] == "开会" for x in prof["dislikes"]), prof["dislikes"])
        c("fact 入库", any("Java" in x["text"] for x in prof["facts"]), prof["facts"])
        c("episode 入库", len(st.data["episodes"]) >= 1, st.data["episodes"])
        c("抽取后 pending 清空", ex.pending_users == 0)

        # ---------- 失败静默 ----------
        c.section("失败静默")
        for bad_script in [("", ""), ("完全不是 JSON", "还是不是"), ("{坏", "{坏")]:
            with FakeLLM(list(bad_script)) as f2:
                st2 = MemoryStore()
                st2.reset()
                ex2 = Extractor(st2, lambda: f2.llm(), lambda: {
                    "memory_enabled": True, "memory_llm_extract": True,
                    "memory_extract_every": 1, "memory_extract_min_gap": 0,
                })
                ex2.on_user("随便说点什么内容")
                before = st2.data["stats"]["counters"]["extract_fail"]
                ex2.run_once()
                after = st2.data["stats"]["counters"]["extract_fail"]
                c(f"垃圾输出「{bad_script[0][:8] or '空'}」不崩且计数 +1", after == before + 1,
                  f"{before}->{after}")
                c("失败时不产生记忆条目", st2.data["episodes"] == [])

        # ---------- 连续 3 次失败自动关闭 ----------
        c.section("连续失败自动降级")
        with FakeLLM(["垃圾", "垃圾", "垃圾"]) as f3:
            st3 = MemoryStore()
            st3.reset()
            ex3 = Extractor(st3, lambda: f3.llm(), lambda: {
                "memory_enabled": True, "memory_llm_extract": True,
                "memory_extract_every": 1, "memory_extract_min_gap": 0,
            })
            for i in range(3):
                ex3.on_user(f"第{i}句测试内容")
                ex3.run_once()
            c("连续 3 次失败后模型轨关闭", ex3._llm_off is True)
            c("失败计数累计 3", st3.data["stats"]["counters"]["extract_fail"] >= 3,
              st3.data["stats"]["counters"]["extract_fail"])

            # 关掉后规则轨仍在工作
            before = len(st3.data["profile"]["likes"])
            ex3.on_user("我喜欢喝冰美式")
            c("模型轨关闭后规则轨仍生效",
              len(st3.data["profile"]["likes"]) > before or
              any(x["text"] == "冰美式" for x in st3.data["profile"]["likes"]),
              st3.data["profile"]["likes"])

        # ---------- 无 Key / 无网络 ----------
        c.section("无模型可用时规则轨兜底")
        st4 = MemoryStore()
        st4.reset()
        ex4 = Extractor(st4, lambda: {"base_url": "", "api_key": "", "model": "", "timeout": 5},
                        lambda: {"memory_enabled": True, "memory_llm_extract": True,
                                 "memory_extract_every": 1, "memory_extract_min_gap": 0})
        ex4.on_user("我喜欢喝冰美式，讨厌开会")
        n = ex4.run_once()
        c("没配模型时不崩", n == 0)
        c("规则轨仍然记下了喜好",
          any(x["text"] == "冰美式" for x in st4.data["profile"]["likes"]),
          st4.data["profile"]["likes"])
        c("规则轨仍然记下了厌恶",
          any(x["text"] == "开会" for x in st4.data["profile"]["dislikes"]),
          st4.data["profile"]["dislikes"])

        # ---------- 闸门 ----------
        c.section("触发闸门")
        with FakeLLM(["{}"]) as f5:
            st5 = MemoryStore()
            st5.reset()
            ex5 = Extractor(st5, lambda: f5.llm(), lambda: {
                "memory_enabled": False, "memory_llm_extract": True,
                "memory_extract_every": 1, "memory_extract_min_gap": 0,
            })
            ex5.on_user("我喜欢喝咖啡")
            c("memory_enabled=False 时规则轨也不跑",
              st5.data["profile"]["likes"] == [], st5.data["profile"]["likes"])

            ex5._get_cfg = lambda: {"memory_enabled": True, "memory_llm_extract": False,
                                    "memory_extract_every": 1, "memory_extract_min_gap": 0}
            ex5.on_user("我喜欢喝茶")
            c("memory_llm_extract=False 时不触发模型",
              ex5._should_extract("我喜欢喝茶") is False)
            c("但规则轨照常工作",
              any(x["text"] == "茶" for x in st5.data["profile"]["likes"]),
              st5.data["profile"]["likes"])

        # ---------- style ----------
        c.section("说话风格抽取")
        with FakeLLM(['{"style":{"prefer":"毒舌但别扎心","avoid":["说教","长篇大论"]}}']) as f6:
            st6 = MemoryStore()
            st6.reset()
            ex6 = Extractor(st6, lambda: f6.llm(), lambda: {
                "memory_enabled": True, "memory_llm_extract": True,
                "memory_extract_every": 1, "memory_extract_min_gap": 0,
            })
            ex6.on_user("你说话可以毒一点，但别真扎心，也别老说教")
            ex6.run_once()
            st = st6.data["profile"]["style"]
            c("style.prefer 入库", st["prefer"] == "毒舌但别扎心", st)
            c("style.avoid 入库", "说教" in st["avoid"], st["avoid"])

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
