# -*- coding: utf-8 -*-
"""A6 验收：记忆接进主窗口。

用法：python tools/test_memory_integration.py

会真的把 ``PetWindow`` 建起来（窗口会闪一下），因为这一步要验的正是
"装配对不对"：菜单有没有那两项、tick 会不会把后台台词取出来、聊完天
记忆有没有落盘。纯逻辑测试验不到这些。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox, FakeLLM  # noqa: E402


def menu_labels(menu) -> list[str]:
    out = []
    for a in menu.actions():
        out.append(a.text())
        if a.menu() is not None:
            out += menu_labels(a.menu())
    return out


def settle(app, seconds: float = 1.3) -> None:
    """让挂起的 singleShot 定时器（首次问候）先跑完，别干扰后面的断言。"""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_for(fn, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.03)
    return False


def main() -> int:
    c = Checker("A6 记忆接入主窗口")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    with DataSandbox() as sb:
        # ---------------- 1. 装配 ----------------
        c.section("1. 装配")
        from app.pet import PetWindow
        pet = PetWindow()
        app.processEvents()
        settle(app)          # 消化掉构造时挂的 900ms 首次问候

        c("记忆对象建起来了", pet.memory is not None)
        c("记忆面板建起来了", pet.memory_panel is not None)
        c("默认开启记忆", pet.cfg.get("memory_enabled") is True,
          pet.cfg.get("memory_enabled"))
        c("面板挂在同一个 store 上",
          pet.memory_panel.store is pet.memory.store)
        c("行为采样线程在跑", pet.memory.debug_summary().get("watch") is True,
          pet.memory.debug_summary())

        # ---------------- 2. 菜单 ----------------
        c.section("2. 菜单")
        labels = menu_labels(pet._build_menu())
        c("顶层有「她记得什么」", any("她记得什么" in x for x in labels),
          [x for x in labels if "记" in x])
        c("设置有「记住聊天内容」", any("记住聊天内容" in x for x in labels))
        c("设置有「记录使用习惯」", any("记录使用习惯" in x for x in labels))
        c("原有项没被顶掉", any("体检报告" in x for x in labels)
          and any("退出" in x for x in labels))

        # ---------------- 3. 台词上下文 ----------------
        c.section("3. 台词上下文")
        ctx = pet._mctx("click")
        c("_mctx 返回上下文", ctx is not None and ctx.store is pet.memory.store)
        c("_mctx 带上了 cfg", (ctx.cfg or {}).get("memory_enabled") is True)
        c("未知情境也不崩", pet._mctx("nope") is not None)

        old = pet.memory
        pet.memory = None
        c("没有记忆时 _mctx 返回 None", pet._mctx("click") is None)
        pet.memory = old

        # ---------------- 4. 聊天写入记忆 ----------------
        c.section("4. 聊天写入记忆")
        with FakeLLM(["好的呀"]) as fake:
            pet.cfg.update({"llm_base_url": fake.base_url, "llm_api_key": "k",
                            "llm_model": "m1", "llm_timeout": 5})
            pet._on_chat_text("我叫老板，我喜欢喝冰美式，讨厌开会")
            prof = wait_for(
                lambda: bool(pet.memory.store.profile().get("likes")), 3.0)
            p = pet.memory.store.profile()
            c("规则轨当场记住了喜好", prof, p.get("likes"))
            c("记住了称呼", p.get("call_me") == "老板", p.get("call_me"))
            c("记住了厌恶", any(x["text"] == "开会" for x in p.get("dislikes", [])),
              p.get("dislikes"))
            c("亲密度涨了", pet.memory.store.relationship()["intimacy"] >= 1,
              pet.memory.store.relationship()["intimacy"])

            # 记忆块确实作为独立 system 消息发出去了。
            # 注意别只看 last_payload：抽取器也会打同一个假服务，最后一条可能是它的。
            wait_for(lambda: len(fake.calls) > 0, 5.0)
            time.sleep(0.6)
            msgs: list = []
            for call in fake.calls:
                m = (call.get("payload") or {}).get("messages") or []
                if any("主人的资料" in (x.get("content") or "") for x in m):
                    msgs = m
                    break
            c("聊天请求发出去了", bool(msgs),
              [len((x.get("payload") or {}).get("messages") or [])
               for x in fake.calls])
            if msgs:
                c("第一条是 system", msgs[0]["role"] == "system")
                c("记忆块是第二条 system",
                  len(msgs) > 1 and msgs[1]["role"] == "system"
                  and "主人的资料" in msgs[1]["content"],
                  [x["role"] for x in msgs])
                c("最后一条是用户这句话", msgs[-1]["role"] == "user"
                  and "冰美式" in msgs[-1]["content"], msgs[-1]["content"])

        # ---------------- 5. tick 取后台台词 ----------------
        c.section("5. tick 消费记忆事件")
        settle(app, 0.3)
        pet.bubble_text = ""
        pet.memory.store.emit("say", "该起来动动了")
        pet.tick()
        c("后台台词冒出来了", pet.bubble_text == "该起来动动了", pet.bubble_text)

        pet.bubble_text = ""
        pet.memory.store.emit("noise", "不该显示")
        pet.tick()
        c("非 say 事件被忽略", pet.bubble_text == "", pet.bubble_text)

        # ---------------- 6. 问候 ----------------
        c.section("6. 问候")
        pet.cfg["first_run"] = False
        pet.bubble_text = ""
        pet._greet()
        app.processEvents()
        c("_greet 说了话", bool(pet.bubble_text), pet.bubble_text)

        # 记忆关掉时问候走原台词池
        pet.memory.cfg  # noqa: B018  (确认属性存在)
        pet.cfg["memory_enabled"] = False
        pet.bubble_text = ""
        pet._greet()
        app.processEvents()
        from app import lines
        c("记忆关掉时回落到原台词池",
          pet.bubble_text in lines.GREETINGS, pet.bubble_text)
        pet.cfg["memory_enabled"] = True

        # ---------------- 7. 开关 ----------------
        c.section("7. 开关")
        pet.set_memory_enabled(False)
        c("关掉后 cfg 落盘", pet.cfg["memory_enabled"] is False)
        c("关掉后采样线程停了", pet.memory.debug_summary().get("watch") is False,
          pet.memory.debug_summary())
        pet.set_memory_enabled(True)
        c("打开后采样线程回来", pet.memory.debug_summary().get("watch") is True)

        pet.set_memory_watch(False)
        c("关掉习惯记录后线程停", pet.memory.debug_summary().get("watch") is False)
        pet.set_memory_watch(True)
        c("再打开线程回来", pet.memory.debug_summary().get("watch") is True)

        # ---------------- 8. 面板改动 → 落盘 ----------------
        c.section("8. 面板改动会落盘")
        pet.memory_panel.ed_teach.setText("我生日是 3 月 14 号")
        pet.memory_panel._teach()
        app.processEvents()
        c("教的事写进了 store",
          any("3 月 14" in f["text"] for f in pet.memory.store.profile()["facts"]),
          [f["text"] for f in pet.memory.store.profile()["facts"]])
        c("教完立刻存盘", os.path.exists(sb.path("memory.json")),
          sb.ls())

        # ---------------- 9. 退出 ----------------
        c.section("9. 退出")
        pet.quit_app()
        app.processEvents()
        c("退出后采样线程停了", pet.memory._thread is None)
        data = sb.read_json("memory.json") or {}
        c("memory.json 里真有内容",
          bool((data.get("profile") or {}).get("facts")),
          list((data.get("profile") or {}).keys()))
        c("体积合理（<200KB）",
          os.path.getsize(sb.path("memory.json")) < 200_000,
          os.path.getsize(sb.path("memory.json")))

        # ---------------- 10. 没记忆也能活 ----------------
        c.section("10. 没有记忆模块时桌宠照常跑")
        import app.pet as petmod
        keep_m, keep_p = petmod.Memory, petmod.MemoryPanel
        petmod.Memory = None
        petmod.MemoryPanel = None
        pet2 = None
        try:
            pet2 = petmod.PetWindow()
            app.processEvents()
            settle(app)
            ok = pet2.memory is None and pet2.memory_panel is None
            pet2.cfg["first_run"] = False
            pet2._greet()
            app.processEvents()
            c("Memory 缺失时照常启动", ok)
            c("Memory 缺失时 _greet 正常", bool(pet2.bubble_text), pet2.bubble_text)
            c("Memory 缺失时 _mctx 返回 None", pet2._mctx("click") is None)
            c("Memory 缺失时打开面板只是提示",
              (pet2._open_memory() or True) and pet2.memory_panel is None)
            pet2.quit_app()
        finally:
            petmod.Memory, petmod.MemoryPanel = keep_m, keep_p
            if pet2 is not None:
                try:
                    pet2.tray.hide()
                except Exception:
                    pass

    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
