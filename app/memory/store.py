# -*- coding: utf-8 -*-
"""记忆的持久化与变更入口。

三条设计约束：

1. **永不抛异常**。记忆坏了不该让桌宠起不来——读到损坏 JSON 就改名备份、
   重建默认结构，静默继续。
2. **写盘要节流**。每说一句话都落盘会把磁盘打满，所以脏标记 + 30 秒节流，
   退出时 ``flush()`` 强制写。
3. **不 import Qt**。整个 ``app/memory`` 包都不许碰 Qt，这样能在 headless
   环境里跑测试。回主线程只走 ``drain()`` 队列。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections import deque
from datetime import date, datetime

from .. import config
from . import schema
from .schema import LIMITS

SAVE_THROTTLE = 30.0     # 秒
DUPLICATE_JACCARD = 0.72  # 相似到这个程度就算同一条


class MemoryStore:
    def __init__(self, path: str | None = None, now_fn=None):
        self._lock = threading.RLock()
        self._path = path or config.memory_path()
        self._data: dict = schema.default_memory()
        self._dirty = False
        self._last_save = 0.0
        self._events: deque[dict] = deque(maxlen=64)
        # 时钟可注入：测试要模拟"跨了两天"，不能靠真实时间流逝
        self._now_fn = now_fn or (lambda: datetime.now().astimezone())
        self._load()

    # ---- 时钟 ----
    def _now(self) -> datetime:
        return self._now_fn()

    def _now_iso(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def _today(self) -> str:
        return self._now().date().isoformat()

    # ================= 路径 =================
    @property
    def path(self) -> str:
        return self._path

    @property
    def bak_path(self) -> str:
        return self._path + ".bak"

    # ================= 读写 =================
    def _load(self) -> None:
        raw = None
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            raw = None
        except Exception as exc:
            # 损坏：改名备份，重建。不弹窗、不崩溃。
            print("[memory] 读取失败，重建:", exc)
            try:
                bad = f"{self._path}.corrupt-{datetime.now():%Y%m%d%H%M%S}"
                shutil.move(self._path, bad)
                print("[memory] 坏文件已备份到", bad)
            except Exception:
                pass
            raw = None

        with self._lock:
            self._data = schema.normalize_memory(raw)
            if raw is None:
                self._dirty = True
                self.save(force=True)
            else:
                # 加载时顺手推进跨天状态（不改脏，只在跨天时写）
                before = json.dumps(self._data["relationship"], sort_keys=True)
                schema.touch_day(self._data, self._now().date())
                if json.dumps(self._data["relationship"], sort_keys=True) != before:
                    self._dirty = True

    def save(self, force: bool = False) -> bool:
        """原子写。force=False 时受节流限制。返回是否真的写了。"""
        with self._lock:
            if not self._dirty and not force:
                return False
            if not force and (time.monotonic() - self._last_save) < SAVE_THROTTLE:
                return False
            data = self._data
            data["updated_at"] = self._now_iso()
            tmp = self._path + ".tmp"
            try:
                os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
                # 上一版留一份 .bak，方便用户手工找回
                if os.path.exists(self._path):
                    try:
                        shutil.copy2(self._path, self.bak_path)
                    except Exception:
                        pass
                os.replace(tmp, self._path)
            except Exception as exc:
                print("[memory] 保存失败:", exc)
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
                return False
            self._dirty = False
            self._last_save = time.monotonic()
            return True

    def mark_dirty(self) -> None:
        with self._lock:
            self._dirty = True

    def maybe_save(self) -> bool:
        return self.save(force=False)

    def flush(self) -> bool:
        return self.save(force=True)

    # ================= 数据访问 =================
    @property
    def data(self) -> dict:
        with self._lock:
            return self._data

    def snapshot(self) -> dict:
        """深拷贝一份，给 UI / 检索用，避免外部误改。"""
        with self._lock:
            return json.loads(json.dumps(self._data, ensure_ascii=False))

    # ================= 事件队列 =================
    def emit(self, kind: str, text: str = "", **data) -> None:
        with self._lock:
            self._events.append({"kind": kind, "text": text, "data": data})

    def drain(self) -> list[dict]:
        with self._lock:
            out = list(self._events)
            self._events.clear()
            return out

    # ================= 变更 =================
    def add_episode(self, kind: str, text: str, tags=None, weight: float = 0.5,
                    decay: str | None = None, source: str = "chat",
                    pinned: bool = False, dedupe_minutes: int = 0) -> dict | None:
        """加一条情景记忆。返回新增的条目，重复则返回被更新的那条。

        ``dedupe_minutes`` > 0 时，同 kind 在该时间窗内已有条目就只 hits+1，
        用来防"熬夜"这种事一晚上被记八遍。
        """
        text = schema.clip(text, 60)
        if not text:
            return None
        with self._lock:
            eps = self._data["episodes"]
            now = self._now()
            norm_new = schema.normalize_text(text)

            # 合并规则：**文本相近**才合并；dedupe_minutes>0 时还要求落在时间窗内。
            # 为什么不只看时间窗：跨天再熬夜是两件事，不该被并成一条。
            # 为什么不只看文本：同一句"熬夜了"隔三天说一次也该算新事件。
            for e in reversed(eps):
                if e.get("kind") != kind:
                    continue
                try:
                    gap_min = (now - schema.parse_iso(e.get("ts"))).total_seconds() / 60
                except Exception:
                    gap_min = 1e9
                if dedupe_minutes > 0 and gap_min > dedupe_minutes:
                    continue
                same = (schema.normalize_text(e.get("text")) == norm_new
                        or schema.jaccard(e.get("text"), text) >= DUPLICATE_JACCARD)
                if not same:
                    continue
                e["hits"] = int(e.get("hits", 1)) + 1
                e["weight"] = min(1.0, float(e.get("weight", 0.5)) + 0.1)
                e["ts"] = self._now_iso()
                e["date"] = self._today()
                self._dirty = True
                return e

            if decay is None:
                decay = ("never" if pinned or kind == "promise"
                         else "slow" if kind in ("preference", "manual") else "fast")
            item = {
                "id": schema.new_id("e", eps),
                "ts": self._now_iso(),
                "date": self._today(),
                "kind": kind if kind in schema.EPISODE_KINDS else "event",
                "text": text,
                "tags": [schema.clip(t, 8) for t in (tags or []) if schema.clip(t, 8)][:4],
                "weight": max(0.0, min(1.0, float(weight))),
                "decay": decay if decay in schema.DECAYS else "fast",
                "hits": 1,
                "source": source if source in schema.SOURCES else "chat",
                "pinned": bool(pinned),
            }
            eps.append(item)
            schema.evict_episodes(self._data)
            self._data["stats"]["counters"]["episodes_added"] += 1
            self._dirty = True
            return item

    def add_fact(self, text: str, confidence: float = 0.6, source: str = "chat",
                 pinned: bool = False) -> dict | None:
        text = schema.clip(text, 60)
        if not text:
            return None
        with self._lock:
            facts = self._data["profile"]["facts"]
            for f in facts:
                if (schema.normalize_text(f.get("text")) == schema.normalize_text(text)
                        or schema.jaccard(f.get("text"), text) >= DUPLICATE_JACCARD):
                    f["hits"] = int(f.get("hits", 1)) + 1
                    f["confidence"] = min(1.0, float(f.get("confidence", 0.5)) + 0.1)
                    f["last_seen"] = self._now_iso()
                    f["pinned"] = bool(f.get("pinned") or pinned)
                    self._dirty = True
                    return f
            item = {
                "id": schema.new_id("f", facts),
                "text": text,
                "confidence": max(0.0, min(1.0, confidence)),
                "source": source if source in schema.SOURCES else "chat",
                "created_at": self._now_iso(),
                "last_seen": self._now_iso(),
                "hits": 1,
                "pinned": bool(pinned),
            }
            facts.append(item)
            facts.sort(key=lambda x: (-int(x.get("pinned", False)),
                                      -float(x.get("confidence", 0)),
                                      -int(x.get("hits", 0))))
            del facts[LIMITS["facts"]:]
            self._dirty = True
            return item

    def add_weighted(self, field: str, text: str, weight: float = 0.6,
                     source: str = "chat") -> dict | None:
        """field ∈ likes / dislikes / habits。"""
        if field not in ("likes", "dislikes", "habits"):
            return None
        text = schema.clip(text, 24)
        if not text:
            return None
        with self._lock:
            items = self._data["profile"][field]
            for it in items:
                if (schema.normalize_text(it.get("text")) == schema.normalize_text(text)
                        or schema.contains_either(it.get("text"), text)):
                    it["hits"] = int(it.get("hits", 1)) + 1
                    it["weight"] = min(1.0, float(it.get("weight", 0.5)) + 0.1)
                    self._dirty = True
                    return it
            item = {"text": text,
                    "weight": max(0.0, min(1.0, weight)),
                    "hits": 1,
                    "since": self._today(),
                    "source": source if source in schema.SOURCES else "chat"}
            items.append(item)
            items.sort(key=lambda x: (-float(x.get("weight", 0)), -int(x.get("hits", 0))))
            del items[LIMITS[field]:]
            self._dirty = True
            return item

    def note_chat(self, user_text: str = "", pet_text: str = "") -> None:
        """记一次对话：加计数、涨亲密度、按天统计。"""
        with self._lock:
            if user_text:
                self._data["relationship"]["total_chats"] += 1
                self._data["stats"]["counters"]["chats_total"] += 1
                day = self._data["stats"]["daily"].setdefault(
                    self._today(),
                    {"active_min": 0, "sessions": 0, "late_night": False, "chats": 0})
                day["chats"] = int(day.get("chats", 0)) + 1
                schema.bump_intimacy(self._data, 1)
            schema.touch_day(self._data, self._now().date())
            self._dirty = True

    def touch_day(self) -> bool:
        """跨天检查。返回是否发生了跨天。"""
        with self._lock:
            before = self._data["relationship"].get("last_seen_date")
            schema.touch_day(self._data, self._now().date())
            changed = before != self._data["relationship"].get("last_seen_date")
            if changed:
                self._dirty = True
            return changed

    def set_profile(self, **fields) -> None:
        """面板编辑用。只接受白名单字段。"""
        allowed = {"call_me", "occupation", "nickname_for_pet"}
        with self._lock:
            p = self._data["profile"]
            for k, v in fields.items():
                if k in allowed:
                    p[k] = schema.clip(v, 20)
            if "nickname_for_pet" in fields:
                self._data["relationship"]["nickname_for_pet"] = p["nickname_for_pet"]
            self._dirty = True

    def set_style(self, prefer: str = "", avoid=None) -> None:
        with self._lock:
            st = self._data["profile"]["style"]
            st["prefer"] = schema.clip(prefer, 16)
            if avoid is not None:
                st["avoid"] = [schema.clip(x, 10) for x in avoid if schema.clip(x, 10)][:6]
            st["updated_at"] = self._now_iso()
            self._dirty = True

    def remove_episode(self, eid: str) -> bool:
        with self._lock:
            eps = self._data["episodes"]
            for i, e in enumerate(eps):
                if e.get("id") == eid:
                    eps.pop(i)
                    self._dirty = True
                    return True
            return False

    def remove_fact(self, fid: str) -> bool:
        with self._lock:
            facts = self._data["profile"]["facts"]
            for i, f in enumerate(facts):
                if f.get("id") == fid:
                    facts.pop(i)
                    self._dirty = True
                    return True
            return False

    def remove_weighted(self, field: str, text: str) -> bool:
        if field not in ("likes", "dislikes", "habits"):
            return False
        with self._lock:
            items = self._data["profile"][field]
            for i, it in enumerate(items):
                if it.get("text") == text:
                    items.pop(i)
                    self._dirty = True
                    return True
            return False

    def reset(self) -> None:
        """全部忘掉，但保留 first_meet（否则"认识第几天"会归零，很怪）。"""
        with self._lock:
            first = self._data["relationship"].get("first_meet")
            self._data = schema.default_memory()
            if first:
                self._data["relationship"]["first_meet"] = first
                try:
                    self._data["relationship"]["days_together"] = max(
                        1, (date.today() - date.fromisoformat(first)).days + 1)
                except Exception:
                    pass
            self._dirty = True
            self.flush()

    # ================= 便捷读取 =================
    def profile(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data["profile"], ensure_ascii=False))

    def relationship(self) -> dict:
        with self._lock:
            return dict(self._data["relationship"])

    def episodes(self) -> list[dict]:
        with self._lock:
            return json.loads(json.dumps(self._data["episodes"], ensure_ascii=False))

    def counters(self) -> dict:
        with self._lock:
            return dict(self._data["stats"]["counters"])

    def bump_counter(self, name: str, delta: int = 1) -> None:
        with self._lock:
            c = self._data["stats"]["counters"]
            c[name] = int(c.get(name, 0)) + int(delta)
            self._dirty = True

    # ================= "说过了"记录 =================
    # voice.py 的约束 2 靠这两个方法落地：同一记忆点每天最多提一次。
    # 存在 meta["spoken"] = {key: "YYYY-MM-DD"}，normalize 时只保留最近 7 天。
    def mark_spoken(self, key: str) -> None:
        if not key:
            return
        with self._lock:
            spoken = self._data["meta"].setdefault("spoken", {})
            spoken[str(key)] = self._today()
            self._dirty = True

    def spoken_today(self, key: str) -> bool:
        if not key:
            return False
        with self._lock:
            spoken = self._data["meta"].get("spoken") or {}
            return str(spoken.get(str(key), "")) == self._today()

    # ================= 置顶 =================
    def set_pinned(self, kind: str, item_id: str, pinned: bool = True) -> bool:
        """面板上点 📌 用。kind 取 "episode" / "fact"。"""
        with self._lock:
            if kind == "episode":
                pool = self._data["episodes"]
            elif kind == "fact":
                pool = self._data["profile"]["facts"]
            else:
                return False
            for it in pool:
                if it.get("id") == item_id:
                    it["pinned"] = bool(pinned)
                    if kind == "episode" and pinned:
                        it["decay"] = "never"
                    self._dirty = True
                    return True
            return False
