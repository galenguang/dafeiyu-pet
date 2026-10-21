# -*- coding: utf-8 -*-
"""A5 验收：记忆面板。

用法：python tools/check_memory_ui.py

除了断言，还会把三页渲染到 ``preview/memory_*.png``，方便肉眼看一下版式。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fake_llm import Checker, DataSandbox  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "preview")


def fill(store) -> None:
    store.set_profile(call_me="老板", occupation="后端开发")
    store.set_style(prefer="毒舌但别扎心")
    for w, t in ((0.9, "冰美式"), (0.7, "火锅"), (0.5, "原神")):
        store.add_weighted("likes", t, w)
    for w, t in ((0.8, "开会"), (0.6, "香菜")):
        store.add_weighted("dislikes", t, w)
    for w, t in ((0.6, "熬夜"),):
        store.add_weighted("habits", t, w)
    for kind, text, tags, w in (
        ("late_night", "熬到 2 点还在改 bug", ["熬夜", "代码"], 0.75),
        ("long_session", "连续干了 3 小时", ["久坐"], 0.55),
        ("event", "加班到十点才回家", ["加班"], 0.6),
        ("event", "在赶一个 Java 项目", ["代码", "工作"], 0.65),
        ("promise", "答应周末陪打游戏", ["游戏"], 0.8),
        ("manual", "生日是 3 月 14 号", ["生日"], 0.9),
    ):
        store.add_episode(kind, text, tags=tags, weight=w,
                          pinned=(kind == "manual"))
    store.add_fact("在赶一个 Java 项目", confidence=0.9)
    store.add_fact("低置信度的猜测", confidence=0.3)
    st = store.data
    st["stats"]["late_nights"]["total"] = 17
    st["stats"]["long_sessions"]["longest_min"] = 214
    import datetime as _dt
    today = _dt.date.today()
    for i, v in enumerate((45, 130, 0, 220, 88, 60, 175)):
        d = (today - _dt.timedelta(days=6 - i)).isoformat()
        st["stats"]["daily"][d] = {"active_min": v, "sessions": 2,
                                   "late_night": v > 150, "chats": 5}


def chips_of(cloud):
    from app.ui.memory import Chip
    out = []
    for i in range(cloud._lay.count()):
        w = cloud._lay.itemAt(i).widget()
        if isinstance(w, Chip):
            out.append(w)
    return out


def rows_of(panel):
    from app.ui.memory import MemRow
    out = []
    for i in range(panel.mem_lay.count()):
        w = panel.mem_lay.itemAt(i).widget()
        if isinstance(w, MemRow):
            out.append(w)
    return out


def main() -> int:
    c = Checker("A5 记忆面板")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    os.makedirs(OUT, exist_ok=True)

    with DataSandbox() as sb:
        from app.memory.store import MemoryStore
        from app.ui.memory import Chip, MemRow, MemoryPanel

        store = MemoryStore()
        store.reset()
        fill(store)
        cfg = {"memory_watch_activity": True}
        panel = MemoryPanel(store, cfg)
        panel.show()
        app.processEvents()

        # ---------------- 1. 骨架 ----------------
        c.section("1. 骨架")
        c("尺寸 392×600", (panel.width(), panel.height()) == (392, 600),
          f"{panel.width()}×{panel.height()}")
        c("三页", panel.stack.count() == 3, panel.stack.count())
        c("标签文字正确", [t.text() for t in panel.tabs] == ["画像", "回忆", "习惯"],
          [t.text() for t in panel.tabs])
        c("默认停在画像页", panel.stack.currentIndex() == 0)
        flags = panel.windowFlags()
        c("无边框 + 置顶 + Tool",
          bool(flags & Qt.WindowType.FramelessWindowHint)
          and bool(flags & Qt.WindowType.WindowStaysOnTopHint)
          and bool(flags & Qt.WindowType.Tool), hex(int(flags)))
        c("副标题带天数与条数", "认识" in panel.sub.text() and "记着" in panel.sub.text(),
          panel.sub.text())

        # ---------------- 2. 画像页 ----------------
        c.section("2. 画像页")
        c("称呼回填", panel.ed_name.text() == "老板", panel.ed_name.text())
        c("职业回填", panel.ed_job.text() == "后端开发", panel.ed_job.text())
        c("说话风格回填", panel.cb_style.currentText() == "毒舌但别扎心",
          panel.cb_style.currentText())
        like_chips = chips_of(panel.cloud_like)
        bad_chips = chips_of(panel.cloud_bad)
        c("喜欢 3 个 chip", len(like_chips) == 3, [x.text for x in like_chips])
        c("不喜欢 2 个 chip", len(bad_chips) == 2, [x.text for x in bad_chips])
        c("chip 云有高度（FlowLayout 生效）",
          panel.cloud_like.sizeHint().height() >= 20,
          panel.cloud_like.sizeHint().height())

        # 删一个 chip
        like_chips[0].removed.emit(like_chips[0].text)
        app.processEvents()
        c("点 × 删掉一个喜欢", len(chips_of(panel.cloud_like)) == 2,
          len(chips_of(panel.cloud_like)))
        c("删的是冰美式",
          not any("冰美式" == x["text"] for x in store.profile()["likes"]))

        # 加一个
        panel.ed_add.setText("桂花糕")
        panel.cb_add.setCurrentIndex(0)
        panel._add_weighted()
        app.processEvents()
        c("加一个喜欢", any(x["text"] == "桂花糕" for x in store.profile()["likes"]),
          [x["text"] for x in store.profile()["likes"]])
        c("加完清空输入框", panel.ed_add.text() == "")

        # 改称呼
        panel.ed_name.setText("小老板")
        panel._save_profile()
        app.processEvents()
        c("改称呼入库", store.profile()["call_me"] == "小老板",
          store.profile()["call_me"])
        c("改风格入库", store.profile()["style"]["prefer"] == "毒舌但别扎心")

        panel.stack.setCurrentIndex(0)
        app.processEvents()
        panel.grab().save(os.path.join(OUT, "memory_profile.png"))

        # ---------------- 3. 回忆页 ----------------
        c.section("3. 回忆页")
        panel._switch(1)
        app.processEvents()
        all_rows = rows_of(panel)
        n_ep = len(store.episodes())
        n_fact = len(store.profile()["facts"])
        c("行数 = 情景 + 事实 − 重复", len(all_rows) == n_ep + n_fact - 1,
          f"{len(all_rows)} vs {n_ep}+{n_fact}")
        c("同一句话只出现一次",
          len({_row_text(r) for r in all_rows}) == len(all_rows),
          [_row_text(r) for r in all_rows])
        c("置顶的排在最前", all_rows[0].item_id in
          {e["id"] for e in store.episodes() if e.get("pinned")}
          | {f["id"] for f in store.profile()["facts"] if f.get("pinned")},
          all_rows[0].item_id)
        c("每行都有正文", all(r.txt.text() for r in all_rows[:3]),
          [r.txt.text() for r in all_rows[:3]])

        panel.ed_search.setText("Java")
        panel._refresh_memory()
        app.processEvents()
        hit = rows_of(panel)
        c("搜索能过滤", 0 < len(hit) < len(all_rows),
          f"{len(hit)} / {len(all_rows)}")
        c("搜到的是相关的", all("Java" in _row_text(r) for r in hit),
          [_row_text(r) for r in hit])

        panel.ed_search.setText("量子力学")
        panel._refresh_memory()
        app.processEvents()
        c("搜不到时显示空提示",
          len(rows_of(panel)) == 0 and panel.mem_empty.isVisible(),
          panel.mem_empty.text())

        panel.ed_search.clear()
        panel._refresh_memory()
        app.processEvents()
        panel.grab().save(os.path.join(OUT, "memory_recall.png"))

        # 教一件事
        before = len(rows_of(panel))
        panel.ed_teach.setText("我讨厌周一早上开会")
        panel._teach()
        app.processEvents()
        c("教一件事后多一行", len(rows_of(panel)) == before + 1,
          f"{before} → {len(rows_of(panel))}")
        c("教的事进了 facts",
          any("周一" in f["text"] for f in store.profile()["facts"]),
          [f["text"] for f in store.profile()["facts"]])
        c("教的事默认置顶",
          any(f.get("pinned") for f in store.profile()["facts"] if "周一" in f["text"]))
        c("教完清空输入框", panel.ed_teach.text() == "")

        # 置顶切换 + 删除
        target = rows_of(panel)[-1]
        tid, tkind = target.item_id, target.kind
        was = _pinned(store, tkind, tid)
        panel._toggle_pin(tkind, tid)
        app.processEvents()
        c("置顶可切换", _pinned(store, tkind, tid) != was,
          f"{was} → {_pinned(store, tkind, tid)}")

        n_before = len(rows_of(panel))
        panel._drop_item(tkind, tid)
        app.processEvents()
        c("× 能删掉一条", len(rows_of(panel)) == n_before - 1,
          f"{n_before} → {len(rows_of(panel))}")

        # ---------------- 4. 习惯页 ----------------
        c.section("4. 习惯页")
        panel._switch(2)
        app.processEvents()
        c("熬夜次数", panel.n_late.text() == "17", panel.n_late.text())
        c("最长一次", panel.n_long.text() == "214", panel.n_long.text())
        c("近 7 天合计 12.0 小时", panel.n_week.text() == "12.0", panel.n_week.text())
        c("7 根柱子有数据", len(panel.week.vals) == 7 and sum(panel.week.vals) == 718,
          panel.week.vals)
        c("星期标签 7 个", len(panel.week.labels) == 7, panel.week.labels)
        c("开关反映配置", panel.ck_watch.isChecked() is True)

        panel.ck_watch.setChecked(False)
        app.processEvents()
        c("关掉开关写回 cfg", cfg.get("memory_watch_activity") is False,
          cfg.get("memory_watch_activity"))
        panel.grab().save(os.path.join(OUT, "memory_habit.png"))

        # ---------------- 5. 全部忘掉 ----------------
        c.section("5. 全部忘掉（两步确认）")
        panel._forget_all()
        c("第一次只是上膛", panel.btn_forget.text() != "全部忘掉"
          and len(store.episodes()) > 0, panel.btn_forget.text())
        panel._forget_all()
        app.processEvents()
        c("第二次真清空", len(store.episodes()) == 0
          and not store.profile()["likes"], len(store.episodes()))
        c("按钮文案复原", panel.btn_forget.text() == "全部忘掉",
          panel.btn_forget.text())
        c("清空后回忆页为空", len(rows_of(panel)) == 0)
        c("清空后画像 chip 归零", len(chips_of(panel.cloud_like)) == 0)

        # ---------------- 6. 健壮性 ----------------
        c.section("6. 健壮性")
        panel2 = MemoryPanel(None, None)
        app.processEvents()
        c("store=None 不崩", panel2.sub.text() == "记忆功能已关闭", panel2.sub.text())
        panel2.refresh()
        panel2._teach()
        panel2._forget_all()
        c("store=None 时各操作静默", True)

        empty = MemoryStore()
        empty.reset()
        panel3 = MemoryPanel(empty, {})
        app.processEvents()
        c("空记忆不崩", panel3.sub.text() != "", panel3.sub.text())
        c("空记忆时喜欢区显示占位",
          len(chips_of(panel3.cloud_like)) == 0)
        panel3._switch(1)
        app.processEvents()
        c("空记忆时回忆页提示", panel3.mem_empty.text() != "", panel3.mem_empty.text())

        # 长文本不撑破
        empty.add_episode("event", "很长" * 20, weight=0.5)
        panel3._refresh_memory()
        app.processEvents()
        c("超长文本被 clip 过（不会撑破行）",
          len(empty.episodes()[0]["text"]) <= 60,
          len(empty.episodes()[0]["text"]))

        # ---------------- 7. 截图 ----------------
        c.section("7. 截图")
        for name in ("memory_profile.png", "memory_recall.png", "memory_habit.png"):
            p = os.path.join(OUT, name)
            c(f"{name} 已生成", os.path.exists(p) and os.path.getsize(p) > 3000,
              os.path.getsize(p) if os.path.exists(p) else "缺失")

        panel.hide()
        panel2.hide()
        panel3.hide()
        app.processEvents()
        c("sandbox 里有记忆文件", os.path.exists(sb.path("memory.json")),
          sb.ls())

    return c.summary()


def _row_text(row) -> str:
    return row.txt.text()


def _pinned(store, kind: str, item_id: str) -> bool:
    pool = (store.data["episodes"] if kind == "episode"
            else store.data["profile"]["facts"])
    for it in pool:
        if it.get("id") == item_id:
            return bool(it.get("pinned"))
    return False


if __name__ == "__main__":
    sys.exit(main())
