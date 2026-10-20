# -*- coding: utf-8 -*-
"""动作注册表。

100 段 VP9-alpha 透明动画，素材来自开源项目 dsh-pet（作者 PC2005-cloud）。
每个动作对应 assets/webm/<file>.webm，统一 640x360 / 24fps / 10s。
"""
from __future__ import annotations

from dataclasses import dataclass

FPS = 24
CANVAS_W = 640
CANVAS_H = 360


@dataclass(frozen=True)
class Action:
    id: str
    file: str
    name: str
    cat: str
    kind: str          # idle | act | click | move | drag | squash
    weight: float = 1.0
    loop: bool = False

    @property
    def clip(self) -> str:
        return f"{self.file}.webm"


# 分类：(中文名, 图标)
CATEGORIES: dict[str, tuple[str, str]] = {
    "idle":     ("待机", "🌙"),
    "daily":    ("日常", "🏠"),
    "eat":      ("吃货", "🍚"),
    "festival": ("节日", "🎊"),
    "perform":  ("表演", "🎪"),
    "music":    ("歌舞", "🎵"),
    "fx":       ("特效", "✨"),
    "muse":     ("碎碎念", "💭"),
    "balance":  ("余额", "💰"),
    "move":     ("移动", "👣"),
    "click":    ("点击反应", "👆"),
    "special":  ("特殊", "🧩"),
}

_A: list[Action] = []


def _add(*items: Action) -> None:
    _A.extend(items)


# ---------------- 待机 ----------------
_add(
    Action("idle", "idle", "待机", "idle", "idle", 14, loop=True),
    Action("sleep", "sleep", "打盹", "idle", "idle", 3, loop=True),
)

# ---------------- 日常 ----------------
_add(
    Action("lookAround", "look-around", "东张西望", "daily", "act", 4),
    Action("yawn", "yawn", "打哈欠", "daily", "act", 3),
    Action("stretch", "stretch", "伸懒腰", "daily", "act", 3),
    Action("wakeUp", "wake-up", "睡醒", "daily", "act", 1),
    Action("startled", "startled", "吓一跳", "daily", "act", 0.6),
    Action("coding", "coding", "写代码", "daily", "act", 2),
    Action("petCat", "pet-cat", "撸猫", "daily", "act", 1.5),
    Action("caughtSnacking", "caught-snacking", "偷吃被抓", "daily", "act", 1.2),
    Action("rageQuit", "rage-quit", "气急败坏", "daily", "act", 1),
    Action("mirror", "mirror", "照镜子", "daily", "act", 1),
    Action("deskTap", "desk-tap", "敲桌子", "daily", "act", 1),
    Action("jotNotes", "jot-notes", "记笔记", "daily", "act", 1),
    Action("brushTeeth", "brush-teeth", "刷牙", "daily", "act", 0.8),
    Action("dressUp", "dress-up", "换衣服", "daily", "act", 0.8),
    Action("spinShow", "spin-show", "转圈圈", "daily", "act", 0.8),
    Action("smashOverhead", "smash-overhead", "砸头", "daily", "act", 0.8),
)

# ---------------- 碎碎念 ----------------
_add(
    Action("muse", "muse", "碎碎念", "muse", "act", 2.5),
    Action("museThink", "muse-think", "沉思", "muse", "act", 2),
    Action("museWipe", "muse-wipe", "擦擦脸", "muse", "act", 1.5),
    Action("museScreen", "muse-screen", "盯屏幕", "muse", "act", 1.5),
)

# ---------------- 吃货 ----------------
_add(
    Action("eatToken", "eat-token", "吃 Token", "eat", "act", 1.5),
    Action("hotpot", "hotpot", "涮火锅", "eat", "act", 1.2),
    Action("iceCream", "ice-cream", "吃冰淇淋", "eat", "act", 1.2),
    Action("snack", "snack", "吃零食", "eat", "act", 1.2),
    Action("whatToEat", "what-to-eat", "吃啥好呢", "eat", "act", 1),
    Action("eatBreakfast", "eat-breakfast", "吃早饭", "eat", "act", 0.8),
    Action("eatLunch", "eat-lunch", "吃午饭", "eat", "act", 0.8),
    Action("eatDinner", "eat-dinner", "吃晚饭", "eat", "act", 0.8),
    Action("eatRice", "eat-rice", "干饭", "eat", "act", 0.7),
    Action("eatCrab", "eat-crab", "吃螃蟹", "eat", "act", 0.7),
    Action("eatDumpling", "eat-dumpling", "吃饺子", "eat", "act", 0.7),
    Action("eatLaba", "eat-laba", "喝腊八粥", "eat", "act", 0.6),
    Action("eatRicecake", "eat-ricecake", "吃年糕", "eat", "act", 0.6),
    Action("eatQingtuan", "eat-qingtuan", "吃青团", "eat", "act", 0.6),
    Action("eatTangyuan", "eat-tangyuan", "吃汤圆", "eat", "act", 0.6),
    Action("eatTanghulu", "eat-tanghulu", "吃糖葫芦", "eat", "act", 0.6),
    Action("eatWatermelon", "eat-watermelon", "吃西瓜", "eat", "act", 0.6),
    Action("eatNoodles", "eat-noodles", "嗦面", "eat", "act", 0.6),
    Action("eatChongyangCake", "eat-chongyang-cake", "吃重阳糕", "eat", "act", 0.5),
    Action("eatZongzi", "eat-zongzi", "吃粽子", "eat", "act", 0.6),
)

# ---------------- 节日 ----------------
_add(
    Action("fireworks", "fireworks", "放烟花", "festival", "act", 0.5),
    Action("redEnvelope", "red-envelope", "收红包", "festival", "act", 0.7),
    Action("mooncake", "mooncake", "吃月饼", "festival", "act", 0.6),
    Action("lionDance", "lion-dance", "舞狮", "festival", "act", 0.6),
    Action("writeFu", "write-fu", "写福字", "festival", "act", 0.6),
    Action("buildSnowman", "build-snowman", "堆雪人", "festival", "act", 0.6),
    Action("christmasTree", "christmas-tree", "装圣诞树", "festival", "act", 0.5),
    Action("halloweenPumpkin", "halloween-pumpkin", "万圣南瓜", "festival", "act", 0.5),
    Action("skyLantern", "sky-lantern", "放天灯", "festival", "act", 0.5),
    Action("riverLantern", "river-lantern", "放河灯", "festival", "act", 0.5),
    Action("qixiNeedle", "qixi-needle", "七夕乞巧", "festival", "act", 0.5),
    Action("chongyangFlower", "chongyang-flower", "重阳赏菊", "festival", "act", 0.5),
    Action("summerFan", "summer-fan", "摇蒲扇", "festival", "act", 0.5),
    Action("unwrapGift", "unwrap-gift", "拆礼物", "festival", "act", 0.5),
)

# ---------------- 表演 ----------------
_add(
    Action("cardMagic", "card-magic", "扑克魔术", "perform", "act", 1),
    Action("magicDove", "magic-dove", "变鸽子", "perform", "act", 0.9),
    Action("conjureFlowers", "conjure-flowers", "变花", "perform", "act", 0.9),
    Action("blowBalloon", "blow-balloon", "吹气球", "perform", "act", 0.9),
    Action("juggleBalls", "juggle-balls", "抛接球", "perform", "act", 1),
    Action("spinTop", "spin-top", "抽陀螺", "perform", "act", 0.9),
    Action("swing", "swing", "荡秋千", "perform", "act", 0.9),
    Action("flyKite", "fly-kite", "放风筝", "perform", "act", 0.9),
    Action("shuttlecock", "shuttlecock", "踢毽子", "perform", "act", 0.9),
    Action("waterGun", "water-gun", "玩水枪", "perform", "act", 0.9),
    Action("gomoku", "gomoku", "下五子棋", "perform", "act", 0.9),
    Action("rubikCube", "rubik-cube", "玩魔方", "perform", "act", 0.9),
    Action("rockingHorse", "rocking-horse", "摇木马", "perform", "act", 0.8),
    Action("toyCar", "toy-car", "玩小车", "perform", "act", 0.8),
)

# ---------------- 歌舞 ----------------
_add(
    Action("maidDance", "maid-dance", "女仆舞", "music", "act", 1.2),
    Action("dance", "dance", "宅舞", "music", "act", 1.2),
    Action("humSong", "hum-song", "哼歌", "music", "act", 1.2),
    Action("swayDance", "sway-dance", "摇摆舞", "music", "act", 1),
    Action("maidCurtsy", "maid-curtsy", "女仆行礼", "music", "act", 0.9),
    Action("playFlute", "play-flute", "吹笛子", "music", "act", 0.9),
    Action("violin", "violin", "拉小提琴", "music", "act", 0.9),
)

# ---------------- 特效 ----------------
_add(
    Action("whaleAppear", "whale-appear", "蓝鲸现世", "fx", "act", 0.6),
    Action("whaleBubbles", "whale-bubbles", "鲸吐泡泡", "fx", "act", 1),
    Action("whaleTail", "whale-tail", "鲸尾拍地", "fx", "act", 0.8),
    Action("littleGhost", "little-ghost", "小幽灵", "fx", "act", 0.8),
    Action("animalHalo", "animal-halo", "兽耳光环", "fx", "act", 0.6),
    Action("butterflyHalo", "butterfly-halo", "蝶舞光环", "fx", "act", 0.6),
    Action("leafPile", "leaf-pile", "落叶堆", "fx", "act", 0.6),
)

# ---------------- 余额 ----------------
_add(
    Action("balanceEmpty", "balance-empty", "余额空空", "balance", "act", 0.4),
    Action("balanceBroke", "balance-broke", "余额告急", "balance", "act", 0.4),
    Action("balanceJingle", "balance-jingle", "金币叮当", "balance", "act", 0.4),
    Action("balanceOverflow", "balance-overflow", "余额爆表", "balance", "act", 0.4),
    Action("balanceNormal", "balance-normal", "余额正常", "balance", "act", 0.4),
    Action("balanceCount", "balance-count", "数钱", "balance", "act", 0.4),
)

# ---------------- 点击反应 ----------------
_add(
    Action("clickWave", "click-wave", "挥手", "click", "click", 1),
    Action("clickJoy", "click-joy", "开心", "click", "click", 1),
    Action("clickAngry", "click-angry", "生气", "click", "click", 1),
    Action("clickShy", "click-shy", "害羞", "click", "click", 1),
    Action("clickGiggle", "click-giggle", "偷笑", "click", "click", 1),
)

# ---------------- 移动 / 特殊 ----------------
_add(
    Action("run", "run", "跑", "move", "move", 1, loop=True),
    Action("crabWalk", "crab-walk", "螃蟹步", "move", "move", 1, loop=True),
    Action("floatStep", "float-step", "飘步", "move", "move", 1, loop=True),
    Action("drag", "drag", "被拖走", "special", "drag", 1, loop=True),
    Action("squash", "squash", "落地挤压", "special", "squash", 1),
)

ACTIONS: dict[str, Action] = {a.id: a for a in _A}

# 每个动作一个图标，界面里用；没写的退回分类图标
ICONS: dict[str, str] = {
    "idle": "🌙", "sleep": "😴", "lookAround": "👀", "yawn": "🥱", "stretch": "🙆",
    "wakeUp": "🌅", "startled": "😱",
    "muse": "💭", "museThink": "🤔", "museWipe": "🧻", "museScreen": "🖥",
    "eatToken": "🪙", "hotpot": "🍲", "iceCream": "🍦", "snack": "🍿", "whatToEat": "🍽",
    "eatBreakfast": "🍳", "eatLunch": "🍱", "eatDinner": "🍛", "eatRice": "🍚",
    "eatCrab": "🦀", "eatDumpling": "🥟", "eatLaba": "🥣", "eatRicecake": "🍡",
    "eatQingtuan": "🟢", "eatTangyuan": "⚪", "eatTanghulu": "🍢", "eatWatermelon": "🍉",
    "eatNoodles": "🍜", "eatChongyangCake": "🥮", "eatZongzi": "🍙",
    "fireworks": "🎆", "redEnvelope": "🧧", "mooncake": "🥮", "lionDance": "🦁",
    "writeFu": "🖌", "buildSnowman": "⛄", "christmasTree": "🎄",
    "halloweenPumpkin": "🎃", "skyLantern": "🏮", "riverLantern": "🕯",
    "qixiNeedle": "🪡", "chongyangFlower": "🌼", "summerFan": "🪭", "unwrapGift": "🎁",
    "cardMagic": "🃏", "magicDove": "🕊", "conjureFlowers": "💐", "blowBalloon": "🎈",
    "juggleBalls": "🤹", "spinTop": "🌀", "swing": "🛝", "flyKite": "🪁",
    "shuttlecock": "🏸", "waterGun": "🔫", "gomoku": "⚫", "rubikCube": "🧩",
    "rockingHorse": "🐴", "toyCar": "🚗",
    "maidDance": "💃", "dance": "🕺", "humSong": "🎤", "swayDance": "🎶",
    "maidCurtsy": "🙇", "playFlute": "🎼", "violin": "🎻",
    "whaleAppear": "🐋", "whaleBubbles": "🫧", "whaleTail": "🌊",
    "littleGhost": "👻", "animalHalo": "🐾", "butterflyHalo": "🦋", "leafPile": "🍂",
    "balanceEmpty": "🪙", "balanceBroke": "💸", "balanceJingle": "🔔",
    "balanceOverflow": "💰", "balanceNormal": "📊", "balanceCount": "🧮",
    "clickWave": "👋", "clickJoy": "😄", "clickAngry": "😠", "clickShy": "😳",
    "clickGiggle": "🤭",
    "run": "🏃", "crabWalk": "🦀", "floatStep": "👣", "drag": "🎣", "squash": "💥",
    "coding": "💻", "petCat": "🐱", "caughtSnacking": "🍪", "rageQuit": "💢",
    "mirror": "🪞", "deskTap": "👆", "jotNotes": "📝", "brushTeeth": "🪥",
    "dressUp": "👗", "spinShow": "💫", "smashOverhead": "🔨",
}


def icon_of(action: Action) -> str:
    return ICONS.get(action.id) or CATEGORIES.get(action.cat, ("", "✨"))[1]


def by_cat(cat: str) -> list[Action]:
    return [a for a in _A if a.cat == cat]


def idle_pool() -> list[Action]:
    """自动表演时可用的一次性动作（带权重）。"""
    return [a for a in _A if a.kind == "act"]


# ---------------- 用户点名的 28 个互动功能 ----------------
# kind: action=直接播放 / pool=分类随机 / special=特殊逻辑
@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    icon: str
    kind: str
    target: str = ""


FEATURES: list[Feature] = [
    Feature("random_perform", "随机表演", "🎪", "pool", "perform"),
    Feature("random_fx", "随机特效", "✨", "pool", "fx"),
    Feature("festival", "节日限定", "🎊", "special", "festival"),
    Feature("random_music", "随机歌舞", "🎵", "pool", "music"),
    Feature("random_eat", "随便吃点", "🍚", "pool", "eat"),
    Feature("sleep", "打盹", "😴", "action", "sleep"),
    Feature("stretch", "伸懒腰", "🙆", "action", "stretch"),
    Feature("startled", "吓一跳", "😱", "action", "startled"),
    Feature("hotpot", "涮火锅", "🍲", "action", "hotpot"),
    Feature("random_daily", "随机日常", "🏠", "pool", "daily"),
    Feature("balance", "看看余额", "💰", "special", "balance"),
    Feature("mooncake_token", "Token吃月饼", "🥮", "special", "mooncake_token"),
    Feature("maid_dance", "女仆舞", "💃", "action", "maidDance"),
    Feature("hum_song", "哼歌", "🎤", "action", "humSong"),
    Feature("ice_cream", "吃冰淇淋", "🍦", "action", "iceCream"),
    Feature("snack", "吃零食", "🍿", "action", "snack"),
    Feature("fireworks", "放烟花", "🎆", "action", "fireworks"),
    Feature("red_envelope", "收红包", "🧧", "action", "redEnvelope"),
    Feature("dance", "宅舞", "🕺", "action", "dance"),
    Feature("whale_appear", "蓝鲸现世", "🐋", "action", "whaleAppear"),
    Feature("pet_cat", "撸猫", "🐱", "action", "petCat"),
    Feature("whale_bubbles", "鲸吐泡泡", "🫧", "action", "whaleBubbles"),
    Feature("whale_tail", "鲸尾拍地", "🌊", "action", "whaleTail"),
    Feature("little_ghost", "小幽灵", "👻", "action", "littleGhost"),
    Feature("coding", "写代码", "💻", "action", "coding"),
    Feature("caught_snacking", "偷吃被抓", "🍪", "action", "caughtSnacking"),
    Feature("rage_quit", "气急败坏", "💢", "action", "rageQuit"),
    Feature("crab_walk", "螃蟹步", "🦀", "action", "crabWalk"),
]
