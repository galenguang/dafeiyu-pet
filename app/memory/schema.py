# -*- coding: utf-8 -*-
"""记忆的数据结构、默认值、校验与文本工具。

这里刻意用**普通 dict** 而不是 dataclass：面板要逐个字段改、抽取器要合并、
迁移要补键，dataclass 在这三件事上都是负担。代价是类型不安全，所以配了
``normalize_memory()`` —— 任何畸形输入进来都会被修成合法结构，永不抛异常。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta

SCHEMA_VERSION = 1

# ---- 枚举 ----
EPISODE_KINDS = {
    "late_night", "long_session", "preference", "event",
    "promise", "mood", "health", "manual",
}
DECAYS = {"fast", "slow", "never"}
SOURCES = {"chat", "behavior", "manual"}
MOODS = {"happy", "normal", "concerned", "annoyed"}

# 衰减半衰期（天）
HALF_LIFE = {"fast": 7.0, "slow": 30.0, "never": 1e9}

# ---- 容量上限：超了自动淘汰 ----
LIMITS = {
    "episodes": 200,
    "facts": 60,
    "likes": 30,
    "dislikes": 30,
    "habits": 30,
    "daily_days": 60,
    "spoken_days": 7,
    "trash": 20,
}

# 单条文本上限（字符）
MAX_TEXT = 40


# --------------------------------------------------------------------------
# 时间
# --------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_str(day: date | None = None) -> str:
    return (day or date.today()).isoformat()


def parse_iso(s, fallback: datetime | None = None) -> datetime:
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        return fallback or datetime.now().astimezone()


def days_since(iso_str: str, now: datetime | None = None) -> float:
    then = parse_iso(iso_str)
    ref = now or datetime.now().astimezone()
    try:
        if then.tzinfo is None and ref.tzinfo is not None:
            ref = ref.replace(tzinfo=None)
        delta = ref - then
    except Exception:
        return 0.0
    return max(0.0, delta.total_seconds() / 86400.0)


# --------------------------------------------------------------------------
# 文本工具
# --------------------------------------------------------------------------
_PUNCT = re.compile(r"[\s\u3000,，。.、!！?？;；:：\"'“”‘’()（）\[\]【】<>《》…~～\-—_/\\|]+")
_LEAD_PRONOUN = re.compile(r"^(我|俺|咱|本人)(的|是|在|有|喜欢|爱|讨厌|烦|叫|想)?")


def clip(s, n: int = MAX_TEXT) -> str:
    """压成单行、去首尾空白、限长。所有入库文本都过这里。"""
    if s is None:
        return ""
    t = str(s).replace("\r", " ").replace("\n", " ")
    t = re.sub(r"\s+", " ", t).strip()
    # 去掉包裹引号
    t = t.strip("\"'“”‘’「」『』")
    if len(t) > n:
        t = t[: max(0, n - 1)] + "…"
    return t


def normalize_text(s) -> str:
    """用于比对的归一化：全半角统一 + 去标点 + 转小写。"""
    t = unicodedata.normalize("NFKC", str(s or "")).lower()
    return _PUNCT.sub("", t)


def strip_first_person(s: str) -> str:
    """'我喜欢喝咖啡' → '喜欢喝咖啡'，'我叫小明' → '小明'。

    只摘掉主语代词，**保留谓语**——把"喜欢喝咖啡"削成"喝咖啡"会丢语义。
    """
    t = (s or "").strip()
    # 姓名类：我叫X / 我的名字是X / 我是X
    t = re.sub(r"^我(?:的)?(?:名字)?(?:叫|是)\s*", "", t)
    # 其余只摘代词
    t = re.sub(r"^(?:我|俺|咱|本人)(?=.)", "", t)
    return t.strip()


def bigrams(s: str) -> set[str]:
    """字符二元组。中文短句不分词也能算相似度。"""
    t = normalize_text(s)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def jaccard(a: str, b: str) -> float:
    sa, sb = bigrams(a), bigrams(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def contains_either(a: str, b: str) -> bool:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return False
    return na in nb or nb in na


# --------------------------------------------------------------------------
# id
# --------------------------------------------------------------------------
def new_id(prefix: str, items: list[dict]) -> str:
    """e_0001 这种递增 id。用序号不用随机，方便人工翻 json 排查。"""
    mx = 0
    pat = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
    for it in items or []:
        m = pat.match(str(it.get("id", "")))
        if m:
            mx = max(mx, int(m.group(1)))
    return f"{prefix}_{mx + 1:04d}"


# --------------------------------------------------------------------------
# 默认结构
# --------------------------------------------------------------------------
def default_memory() -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "updated_at": now_iso(),
        "profile": {
            "call_me": "",
            "occupation": "",
            "likes": [],
            "dislikes": [],
            "habits": [],
            "style": {"prefer": "", "avoid": [], "updated_at": None},
            "facts": [],
        },
        "relationship": {
            "first_meet": today_str(),
            "days_together": 1,
            "streak_days": 1,
            "last_seen_date": today_str(),
            "intimacy": 0,
            "mood": "normal",
            "total_chats": 0,
            "nickname_for_pet": "",
        },
        "episodes": [],
        "stats": {
            "late_nights": {"total": 0, "last_30d": 0,
                            "last_date": None, "consecutive": 0},
            "long_sessions": {"total": 0, "last_date": None, "longest_min": 0},
            "daily": {},
            "counters": {"chats_total": 0, "extract_runs": 0,
                         "extract_fail": 0, "episodes_added": 0},
        },
        "meta": {
            "last_compact_at": None,
            "compact_version": 0,
            "spoken": {},
        },
    }


# --------------------------------------------------------------------------
# 校验 / 修复
# --------------------------------------------------------------------------
def _as_str(v, n: int = MAX_TEXT) -> str:
    return clip(v, n) if isinstance(v, (str, int, float)) else ""


def _as_float(v, lo=0.0, hi=1.0, default=0.5) -> float:
    try:
        f = float(v)
    except Exception:
        return default
    return max(lo, min(hi, f))


def _as_int(v, lo=0, hi=10 ** 9, default=0) -> int:
    try:
        i = int(v)
    except Exception:
        return default
    return max(lo, min(hi, i))


def _as_list(v) -> list:
    return v if isinstance(v, list) else []


def _fix_weighted(items, limit: int, *, key: str = "text") -> list[dict]:
    """likes / dislikes / habits 这种 {text, weight, hits, since, source} 列表。"""
    out: list[dict] = []
    seen: set[str] = set()
    for it in _as_list(items):
        if isinstance(it, str):
            it = {"text": it}
        if not isinstance(it, dict):
            continue
        text = _as_str(it.get(key), 24)
        if not text:
            continue
        norm = normalize_text(text)
        if norm in seen:
            continue
        seen.add(norm)
        out.append({
            "text": text,
            "weight": _as_float(it.get("weight"), default=0.6),
            "hits": _as_int(it.get("hits"), default=1),
            "since": _as_str(it.get("since"), 10) or today_str(),
            "source": it.get("source") if it.get("source") in SOURCES else "chat",
        })
    out.sort(key=lambda x: (-x["weight"], -x["hits"]))
    return out[:limit]


def _fix_facts(items) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for it in _as_list(items):
        if not isinstance(it, dict):
            continue
        text = _as_str(it.get("text"), 60)
        if not text:
            continue
        norm = normalize_text(text)
        if norm in seen:
            continue
        seen.add(norm)
        out.append({
            "id": _as_str(it.get("id"), 12) or new_id("f", out),
            "text": text,
            "confidence": _as_float(it.get("confidence"), default=0.6),
            "source": it.get("source") if it.get("source") in SOURCES else "chat",
            "created_at": _as_str(it.get("created_at"), 32) or now_iso(),
            "last_seen": _as_str(it.get("last_seen"), 32) or now_iso(),
            "hits": _as_int(it.get("hits"), default=1),
            "pinned": bool(it.get("pinned")),
        })
    return out[: LIMITS["facts"]]


def _fix_episodes(items) -> list[dict]:
    out: list[dict] = []
    for it in _as_list(items):
        if not isinstance(it, dict):
            continue
        text = _as_str(it.get("text"), 60)
        if not text:
            continue
        kind = it.get("kind") if it.get("kind") in EPISODE_KINDS else "event"
        decay = it.get("decay") if it.get("decay") in DECAYS else (
            "never" if it.get("pinned") else
            "slow" if kind in ("preference", "promise", "manual") else "fast")
        ts = _as_str(it.get("ts"), 32) or now_iso()
        out.append({
            "id": _as_str(it.get("id"), 12) or new_id("e", out),
            "ts": ts,
            "date": _as_str(it.get("date"), 10) or ts[:10],
            "kind": kind,
            "text": text,
            "tags": [clip(t, 8) for t in _as_list(it.get("tags")) if clip(t, 8)][:4],
            "weight": _as_float(it.get("weight"), default=0.5),
            "decay": decay,
            "hits": _as_int(it.get("hits"), default=1),
            "source": it.get("source") if it.get("source") in SOURCES else "chat",
            "pinned": bool(it.get("pinned")),
        })
    return out[: LIMITS["episodes"]]


def _fix_daily(raw) -> dict:
    """只留最近 N 天，值统一成 {active_min, sessions, late_night, chats}。"""
    if not isinstance(raw, dict):
        return {}
    keep = LIMITS["daily_days"]
    items = sorted(raw.items(), key=lambda kv: str(kv[0]), reverse=True)[:keep]
    out: dict[str, dict] = {}
    for k, v in items:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)):
            continue
        v = v if isinstance(v, dict) else {}
        out[str(k)] = {
            "active_min": _as_int(v.get("active_min"), 0, 1440, 0),
            "sessions": _as_int(v.get("sessions"), 0, 500, 0),
            "late_night": bool(v.get("late_night")),
            "chats": _as_int(v.get("chats"), 0, 10000, 0),
        }
    return out


def normalize_memory(raw) -> dict:
    """把任意输入修成合法结构。**永不抛异常**，这是加载健壮性的唯一保障。"""
    mem = default_memory()
    if not isinstance(raw, dict):
        return mem

    # ---- profile ----
    p_raw = raw.get("profile")
    p_raw = p_raw if isinstance(p_raw, dict) else {}
    p = mem["profile"]
    p["call_me"] = _as_str(p_raw.get("call_me"), 12)
    p["occupation"] = _as_str(p_raw.get("occupation"), 20)
    p["nickname_for_pet"] = _as_str(p_raw.get("nickname_for_pet"), 12)
    p["likes"] = _fix_weighted(p_raw.get("likes"), LIMITS["likes"])
    p["dislikes"] = _fix_weighted(p_raw.get("dislikes"), LIMITS["dislikes"])
    p["habits"] = _fix_weighted(p_raw.get("habits"), LIMITS["habits"])
    p["facts"] = _fix_facts(p_raw.get("facts"))
    st = p_raw.get("style")
    st = st if isinstance(st, dict) else {}
    p["style"] = {
        "prefer": _as_str(st.get("prefer"), 16),
        "avoid": [clip(x, 10) for x in _as_list(st.get("avoid")) if clip(x, 10)][:6],
        "updated_at": _as_str(st.get("updated_at"), 32) or None,
    }

    # ---- relationship ----
    r_raw = raw.get("relationship")
    r_raw = r_raw if isinstance(r_raw, dict) else {}
    r = mem["relationship"]
    first = _as_str(r_raw.get("first_meet"), 10)
    r["first_meet"] = first if re.match(r"^\d{4}-\d{2}-\d{2}$", first) else today_str()
    r["last_seen_date"] = (_as_str(r_raw.get("last_seen_date"), 10)
                           if re.match(r"^\d{4}-\d{2}-\d{2}$",
                                       _as_str(r_raw.get("last_seen_date"), 10))
                           else today_str())
    r["nickname_for_pet"] = p["nickname_for_pet"]
    r["intimacy"] = _as_int(r_raw.get("intimacy"), 0, 100, 0)
    r["mood"] = r_raw.get("mood") if r_raw.get("mood") in MOODS else "normal"
    r["total_chats"] = _as_int(r_raw.get("total_chats"), 0, 10 ** 7, 0)
    r["streak_days"] = _as_int(r_raw.get("streak_days"), 1, 10 ** 5, 1)
    r["days_together"] = _as_int(r_raw.get("days_together"), 1, 10 ** 5, 1)

    # ---- episodes ----
    mem["episodes"] = _fix_episodes(raw.get("episodes"))

    # ---- stats ----
    s_raw = raw.get("stats")
    s_raw = s_raw if isinstance(s_raw, dict) else {}
    ln = s_raw.get("late_nights") if isinstance(s_raw.get("late_nights"), dict) else {}
    ls = s_raw.get("long_sessions") if isinstance(s_raw.get("long_sessions"), dict) else {}
    ct = s_raw.get("counters") if isinstance(s_raw.get("counters"), dict) else {}
    mem["stats"] = {
        "late_nights": {
            "total": _as_int(ln.get("total"), 0, 10 ** 6, 0),
            "last_30d": _as_int(ln.get("last_30d"), 0, 10 ** 6, 0),
            "last_date": _as_str(ln.get("last_date"), 10) or None,
            "consecutive": _as_int(ln.get("consecutive"), 0, 10 ** 5, 0),
        },
        "long_sessions": {
            "total": _as_int(ls.get("total"), 0, 10 ** 6, 0),
            "last_date": _as_str(ls.get("last_date"), 10) or None,
            "longest_min": _as_int(ls.get("longest_min"), 0, 1440, 0),
        },
        "daily": _fix_daily(s_raw.get("daily")),
        "counters": {
            "chats_total": _as_int(ct.get("chats_total"), 0, 10 ** 7, 0),
            "extract_runs": _as_int(ct.get("extract_runs"), 0, 10 ** 7, 0),
            "extract_fail": _as_int(ct.get("extract_fail"), 0, 10 ** 7, 0),
            "episodes_added": _as_int(ct.get("episodes_added"), 0, 10 ** 7, 0),
        },
    }

    # ---- meta ----
    m_raw = raw.get("meta")
    m_raw = m_raw if isinstance(m_raw, dict) else {}
    spoken = m_raw.get("spoken")
    spoken = spoken if isinstance(spoken, dict) else {}
    cutoff = (date.today() - timedelta(days=LIMITS["spoken_days"])).isoformat()
    mem["meta"] = {
        "last_compact_at": _as_str(m_raw.get("last_compact_at"), 32) or None,
        "compact_version": _as_int(m_raw.get("compact_version"), 0, 10 ** 6, 0),
        "spoken": {str(k): str(v) for k, v in spoken.items()
                   if str(v) >= cutoff and isinstance(k, str)},
    }

    mem["schema"] = SCHEMA_VERSION
    mem["updated_at"] = _as_str(raw.get("updated_at"), 32) or now_iso()
    return mem


# --------------------------------------------------------------------------
# 关系状态推进（跨天 / 亲密度）
# --------------------------------------------------------------------------
def touch_day(mem: dict, day: date | None = None) -> dict:
    """跨天时更新连续天数、总天数、最后出现日期。原地修改并返回。"""
    d = day or date.today()
    ds = d.isoformat()
    r = mem["relationship"]
    last = r.get("last_seen_date") or ds
    if last == ds:
        return mem
    try:
        gap = (d - date.fromisoformat(last)).days
    except Exception:
        gap = 1
    if gap == 1:
        r["streak_days"] = _as_int(r.get("streak_days"), 1, 10 ** 5, 1) + 1
    elif gap > 1:
        r["streak_days"] = 1
    r["last_seen_date"] = ds
    try:
        r["days_together"] = max(1, (d - date.fromisoformat(r["first_meet"])).days + 1)
    except Exception:
        r["days_together"] = 1
    return mem


def bump_intimacy(mem: dict, delta: int = 1) -> int:
    r = mem["relationship"]
    r["intimacy"] = _as_int(r.get("intimacy"), 0, 100, 0) + int(delta)
    r["intimacy"] = max(0, min(100, r["intimacy"]))
    return r["intimacy"]


def intimacy_label(v: int) -> str:
    if v >= 80:
        return "自己人"
    if v >= 60:
        return "很熟了"
    if v >= 40:
        return "算朋友"
    if v >= 20:
        return "有点眼熟"
    return "还不太熟"


# --------------------------------------------------------------------------
# 淘汰
# --------------------------------------------------------------------------
def evict_episodes(mem: dict) -> int:
    """超出上限时按 (weight × 新鲜度) 淘汰，pinned / promise 永不淘汰。"""
    eps = mem.get("episodes") or []
    limit = LIMITS["episodes"]
    if len(eps) <= limit:
        return 0

    def score(e: dict) -> float:
        if e.get("pinned") or e.get("kind") == "promise":
            return 1e9
        hl = HALF_LIFE.get(e.get("decay", "fast"), 7.0)
        fresh = 0.5 ** (days_since(e.get("ts", "")) / hl)
        return float(e.get("weight", 0.5)) * fresh

    keep = sorted(eps, key=score, reverse=True)[:limit]
    keep.sort(key=lambda e: str(e.get("ts", "")), reverse=True)
    removed = len(eps) - len(keep)
    mem["episodes"] = keep
    return removed
