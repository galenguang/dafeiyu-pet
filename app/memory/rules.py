# -*- coding: utf-8 -*-
"""本地规则抽取：零成本、零延迟、不需要网络。

它的定位不是"代替模型"，而是**兜底**：模型输出格式崩了、接口挂了、用户
没配 Key，只要主人说了"我喜欢冰美式"，这条也得记住。所以这里的正则宁可
少抽（漏了后面还有模型补），也不能抽错——把"我喜欢你"记成"喜欢：你"
比不记更糟。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 结果结构
# --------------------------------------------------------------------------
@dataclass
class Candidate:
    field: str                 # call_me | occupation | likes | dislikes | facts | episodes
    text: str = ""
    kind: str = ""             # episodes 专用
    confidence: float = 0.6
    pinned: bool = False
    tags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 停用词：这些东西不能当"喜好对象"
# --------------------------------------------------------------------------
STOP_OBJECTS = {
    "你", "我", "他", "她", "它", "您", "你们", "我们", "他们",
    "这个", "那个", "这样", "那样", "这么", "那么", "什么", "啥",
    "了", "的", "吧", "啊", "呀", "呢", "哦", "嘛", "哈", "嗯",
    "一个人", "东西", "事情", "感觉", "样子", "时候",
    "工作", "上班", "上学",  # 太泛，记了也没用
}

# 喜好对象前面的动词，去掉更像"东西"
_LEAD_VERB = re.compile(r"^(喝|吃|玩|看|听|打|买|用|搞|做|抽|养|穿|读|学)")

# 句尾语气词
_TAIL_PARTICLE = re.compile(r"[了啊呀吧呢哦嘛哈呗啦噢哟]+$")

# 分句
_SENT_SPLIT = re.compile(r"[。！？!?\n;；]+")
_CLAUSE_SPLIT = re.compile(r"[，,、]+")


def _clean_object(s: str, maxlen: int = 12) -> str:
    """把"喝冰美式了"整理成"冰美式"。不合格返回空串。"""
    t = (s or "").strip()
    t = t.strip("\"'“”‘’「」『』 ")
    t = _TAIL_PARTICLE.sub("", t)
    t = _LEAD_VERB.sub("", t)
    t = _TAIL_PARTICLE.sub("", t)
    t = t.strip()
    if not t or len(t) > maxlen:
        return ""
    if t in STOP_OBJECTS:
        return ""
    # 单字只收汉字：'茶' 可以，'a' 不行
    if len(t) == 1 and not re.match(r"[\u4e00-\u9fff]", t):
        return ""
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", t):
        return ""
    return t


# --------------------------------------------------------------------------
# 正则表
# --------------------------------------------------------------------------
# 称呼：叫我X / 我叫X / 以后叫我X / 你可以叫我X
_R_CALL_ME = re.compile(
    r"(?:以后|你可以|你就|请)?(?:叫我|喊我|称呼我)\s*([^\s，,。！？!?]{1,8})")
_R_MY_NAME = re.compile(r"^我(?:的名字)?叫\s*([^\s，,。！？!?]{1,8})")

# 喜欢 / 讨厌
_R_LIKE = re.compile(r"我(?:很|超|特别|最|挺|真的)?(?:喜欢|爱|中意)\s*([^，,。！？!?\n]{1,14})")
_R_DISLIKE = re.compile(r"我(?:很|超|特别|最|挺|真的)?(?:不喜欢|讨厌|烦|受不了)\s*([^，,。！？!?\n]{1,14})")
# 分句开头省略主语的情况："我喜欢咖啡，讨厌开会" 后半句没有"我"
_R_LIKE_BARE = re.compile(
    r"^(?:很|超|特别|最|挺|真的)?(?:喜欢|爱|中意)\s*([^，,。！？!?\n]{1,14})")
_R_DISLIKE_BARE = re.compile(
    r"^(?:很|超|特别|最|挺|真的)?(?:不喜欢|讨厌|烦|受不了)\s*([^，,。！？!?\n]{1,14})")

# 职业：我是做X的 / 我是X工程师 / 我做X的
_R_JOB = re.compile(
    r"我(?:是|在做|干)\s*([^，,。！？!?\n]{2,10}?)(?:的)?(?:工作|工程师|开发|设计|运营|产品经理)?")

# 正在做的事
_R_DOING = re.compile(
    r"我(?:在|正在|最近在|这两天在)\s*(?:做|写|学|搞|弄|研究|看|准备|赶)\s*([^，,。！？!?\n]{2,16})")

# 显式要求记住
_R_REMEMBER = re.compile(r"(?:记住|记一下|别忘了|别忘|帮我记)[:：,，]?\s*([^。！？!?\n]{2,30})")

# 熬夜
_R_LATE = re.compile(r"(?:昨天|昨晚|今天|今夜|刚才)?[^。！？!?\n]{0,8}(?:熬夜|通宵|没睡|失眠)")

# 强信号词：命中就值得立刻跑一次模型抽取
STRONG_SIGNALS = (
    "我叫", "我是", "叫我", "我的名字", "我喜欢", "我爱", "我讨厌",
    "我不喜欢", "我最近", "我在做", "我在学", "我每天", "我一般",
    "我习惯", "我生日", "记住", "别忘", "以后",
)


def has_strong_signal(text: str) -> bool:
    return any(s in (text or "") for s in STRONG_SIGNALS)


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def extract_rules(text: str) -> list[Candidate]:
    """从一句用户发言里抽候选。按句分，每句最多给几类结果。"""
    out: list[Candidate] = []
    if not text or len(text) > 500:
        return out

    seen: set[tuple[str, str]] = set()

    def add(cand: Candidate) -> None:
        key = (cand.field, cand.text or cand.kind)
        if not key[1] or key in seen:
            return
        seen.add(key)
        out.append(cand)

    # 整句先处理"记住"（优先级最高，能跨逗号）
    for m in _R_REMEMBER.finditer(text):
        body = m.group(1).strip(" ，,。！？!?")
        body = body[:30]
        if len(body) >= 2:
            add(Candidate("facts", text=body, confidence=0.95, pinned=True,
                          tags=["手动"]))

    for sent in _SENT_SPLIT.split(text):
        sent = sent.strip()
        if not sent:
            continue

        # 称呼
        m = _R_CALL_ME.search(sent) or _R_MY_NAME.search(sent)
        if m:
            name = _clean_object(m.group(1), 8)
            if name:
                add(Candidate("call_me", text=name, confidence=0.9))

        # 讨厌要在喜欢之前判，否则"我不喜欢X"会被"喜欢"正则吃掉
        for clause in _CLAUSE_SPLIT.split(sent):
            clause = clause.strip()
            if not clause:
                continue

            m = _R_DISLIKE.search(clause) or _R_DISLIKE_BARE.search(clause)
            if m:
                obj = _clean_object(m.group(1))
                if obj:
                    add(Candidate("dislikes", text=obj, confidence=0.7))
                    continue

            m = _R_LIKE.search(clause) or _R_LIKE_BARE.search(clause)
            if m:
                obj = _clean_object(m.group(1))
                if obj:
                    add(Candidate("likes", text=obj, confidence=0.7))
                    continue

            m = _R_DOING.search(clause)
            if m:
                obj = _clean_object(m.group(1), 16)
                if obj:
                    add(Candidate("facts", text=f"在做{obj}", confidence=0.6))

        # 职业（单独判，避免和"我在做"抢）
        m = _R_JOB.search(sent)
        if m:
            job = _clean_object(m.group(1), 10)
            if job and not _R_DOING.search(sent):
                add(Candidate("occupation", text=job, confidence=0.5))

        # 熬夜
        if _R_LATE.search(sent) and re.search(r"(熬夜|通宵|没睡|失眠)", sent):
            add(Candidate("episodes", kind="late_night",
                          text=_episode_text(sent), confidence=0.6,
                          tags=["熬夜"]))

    return out


def _episode_text(sent: str) -> str:
    """把"昨天又熬夜到两点了"压成"熬夜到两点"。"""
    t = re.sub(r"^(昨天|昨晚|今天|今夜|刚才)", "", sent.strip())
    t = re.sub(r"^(又|还是|居然|竟然)", "", t)
    t = re.sub(r"^(我)?(在|又)?", "", t)
    t = t.strip(" ，,。！？!?")
    return (t or "熬夜了")[:24]


# --------------------------------------------------------------------------
# 应用候选到 store
# --------------------------------------------------------------------------
def apply_candidates(store, cands: list[Candidate], source: str = "chat") -> int:
    """把候选写进 store，返回实际新增/更新的条数。"""
    n = 0
    for cd in cands:
        if cd.field == "call_me":
            if cd.text and not store.data["profile"].get("call_me"):
                store.set_profile(call_me=cd.text)
                n += 1
        elif cd.field == "occupation":
            if cd.text and not store.data["profile"].get("occupation"):
                store.set_profile(occupation=cd.text)
                n += 1
        elif cd.field in ("likes", "dislikes", "habits"):
            if cd.text and store.add_weighted(cd.field, cd.text, cd.confidence, source):
                n += 1
        elif cd.field == "facts":
            if cd.text and store.add_fact(cd.text, cd.confidence, source, cd.pinned):
                n += 1
        elif cd.field == "episodes":
            if cd.text and store.add_episode(
                    cd.kind or "event", cd.text, tags=cd.tags,
                    weight=cd.confidence, source=source,
                    pinned=cd.pinned, dedupe_minutes=30):
                n += 1
    return n
