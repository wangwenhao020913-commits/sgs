"""内容包的数据模型（只装身份字段）。

结构层用 pydantic v2：``frozen=True`` + ``extra="forbid"``（字段名写错必须报错，
不能静默忽略）。``颜色`` 由花色派生、``属性`` 与行为字段（使用时机 / 目标规则 /
次数限制 / 效果列表）都不在这里 —— 前者军争才引入，后者归 effects 层（票 07）。

牌实例的 ``id`` **不在这里**：数据只给牌堆清单，实例化由 state/engine 在开局时做，
所以数据文件可以有同型同花色同点数的两张牌。

M3 票 01 追加**武将面**（``Kingdom`` / ``SkillType`` / ``HeroSkill`` / ``Hero`` /
``HeroTable``）：数据文件只装"武将是什么"（id / 名称 / 势力 / 性别 / 体力上限 /
技能清单 / 版本 / 启用），技能**行为**仍住 ``skills/`` 的 Python（ADR-009）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DEFAULT_SLOT_LIMIT",
    "Category",
    "CardType",
    "Color",
    "ContentPack",
    "Deck",
    "DeckCard",
    "DeckExpect",
    "Gender",
    "Hero",
    "HeroSkill",
    "HeroTable",
    "Kingdom",
    "SkillType",
    "Suit",
    "color_of_suit",
]

DEFAULT_SLOT_LIMIT = 2
"""同一 (花色,点数) 槽位的**默认**上限；``Deck.slot_limit`` 可逐档覆盖。

标准版含 EX 的 108 张档位在 ♠2 / ♣2 / ♥Q / ♦Q 四个槽位各有三张，故该档显式写 3；
基本 53 张档位沿用默认 2。它是"未声明时取几"，不是全局放宽（票 09 §3）。
"""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Suit(str, Enum):
    """花色；颜色由它派生（heart/diamond → 红，spade/club → 黑）。"""

    HEART = "heart"
    DIAMOND = "diamond"
    SPADE = "spade"
    CLUB = "club"


class Color(str, Enum):
    """牌的**颜色**（票 12 §Q4 的取值表）：由花色派生，是【仁王盾】一类效果的判据。

    无色（``None``）在 M2 只会由**转化牌 / 虚拟牌**产生（票 30）；实体牌恒有颜色。
    真源是花色到颜色的映射（:func:`color_of_suit`），故没有第二份可漂移的数据。
    """

    RED = "red"
    BLACK = "black"


_RED_SUITS = frozenset({Suit.HEART, Suit.DIAMOND})
_BLACK_SUITS = frozenset({Suit.SPADE, Suit.CLUB})


def color_of_suit(suit: Suit | str) -> Color | None:
    """由花色派生颜色；未知花色返回 ``None``（fail-open 到"无色"，不做静默猜测）。

    接受 ``Suit`` 或它的字符串值：``CardRef.suit`` 是字符串（记录面形状），判定后果与
    装备效果都经本函数解析，故两处不会各自重抄 ``"heart"`` 字面量。
    """
    try:
        resolved = Suit(suit)
    except ValueError:
        return None
    if resolved in _RED_SUITS:
        return Color.RED
    if resolved in _BLACK_SUITS:
        return Color.BLACK
    return None  # pragma: no cover - Suit 是闭集，本条只为全函数性保留


class Gender(str, Enum):
    """角色的**性别**（票 05 §7.1 的【雌雄双股剑】判据）。

    性别在官方里是**武将牌**的事实；M2 一个武将都不落，故没有任何数据源会填它，
    字段默认 ``None`` = "未知"。装备效果里的"异性"判据因此只有在测试人为指定时成立
    （M3 加武将时由武将数据填值，接口与形状都不变）。
    """

    MALE = "male"
    FEMALE = "female"


class Kingdom(str, Enum):
    """武将的**势力**（`GLOSSARY.md`「势力」；M3 票 01 新增）。

    标准版四势力取英文值，中文只出现在渲染层：势力作用域（"其他同势力角色"）判定
    一律比枚举成员，不把中文串塞进状态。扩展的神 / 晋等属后续里程碑，**不建预留取值**
    （未出现的势力等到有数据的那个里程碑再加，避免"闭集里躺着用不到的成员"）。
    """

    WEI = "wei"
    SHU = "shu"
    WU = "wu"
    QUN = "qun"


class SkillType(str, Enum):
    """技能的**类型**（`GLOSSARY.md`「技能类型」；`03` §4「技能描述模型」的闭集；M3 票 01 新增）。

    闭集 = ``NORMAL``（普通技）/ ``LORD``（主公技）——标准版 25 名只落这两类。
    ``LIMITED``（限定技）/ ``AWAKENING``（觉醒技）是**预留取值**：字段照建、取值合法，
    但标准版 25 名**零载体**，M3 不落（ADR-008：改判 M5）。
    """

    NORMAL = "NORMAL"
    LORD = "LORD"
    LIMITED = "LIMITED"
    AWAKENING = "AWAKENING"


class HeroSkill(_FrozenModel):
    """武将牌上的一条技能**条目**：引用技能定义，不装行为。

    ``id`` 是**短 id**（如 ``hujia``）；对外的技能 id = ``hero.<武将 id>.<短 id>``
    （票 01 §Data / 票 12），由 :func:`sgs.data.hero_skill_id` 一处派生。

    条目自带的 ``skill_type`` 是数据侧的声明，加载期会与 ``skills.CATALOG`` 里那份
    定义的 ``SkillType`` 对账（**仅对启用的武将**，见 :mod:`sgs.data.loader`）；
    身份门控的规范落点仍读定义上的字段，不读这里。
    """

    id: str
    name: str
    skill_type: SkillType


class Hero(_FrozenModel):
    """一名武将：**是什么**（身份事实），不含任何行为。

    ``version`` 区分同名武将的不同版本（标准版 / 界限突破版；ADR-006：界版数据分开存、
    MVP 不启用），本文件只有 ``"standard"``。``enabled`` 是**启用门控**：只有启用的武将
    可被发放，且其技能引用必须在 ``skills.CATALOG`` 里闭合；未启用的只做结构与唯一性校验
    （"技能还没实现"不该拦住整张表）。两个字段都**必写**——"没声明"与"声明为假"是两件事。
    """

    id: str
    name: str
    kingdom: Kingdom
    gender: Gender
    hp_max: int
    skills: tuple[HeroSkill, ...]
    version: str
    enabled: bool


class HeroTable(_FrozenModel):
    """一份已通过加载期校验的**武将表**（``heroes.<pack_id>.json``）。

    与内容包分开成文件、分开校验：武将面按其自己的 ``schema_version`` 升版，
    与牌堆档位 / 记录流 / 规则版本互不牵动。
    """

    schema_version: int
    pack_id: str
    heroes: tuple[Hero, ...]

    def by_id(self, hero_id: str) -> Hero | None:
        """按 id 取武将；未知 id 返回 ``None``（fail-closed 的调用方自行决定）。"""
        for hero in self.heroes:
            if hero.id == hero_id:
                return hero
        return None


class Category(str, Enum):
    """卡牌大类；M1 数据只用 ``basic``，其余枚举位随 M2 引入锦囊/装备。"""

    BASIC = "basic"
    TRICK = "trick"
    EQUIPMENT = "equipment"


class CardType(_FrozenModel):
    """牌名级定义：这张牌**是什么**。行为字段整体归 effects 层。

    ``attack_range`` / ``delayed`` 是**牌面事实**（票 09 §6）：前者是"距离 ≤ 攻击范围"
    L3 校验的取值来源、只属武器；后者是延时锦囊"置入判定区"的声明面。两者都带默认值，
    故基本 53 张档位一字不改即可通过。

    数据档里【寒冰剑】的 ``attack_range`` 以 **2** 落地。该数值的**来源类型 = 无一手源**
    （本机没有该武器牌面的一手抓取物，取社区通说；票 05 §U-2 / 票 12）：它只标注来源强度，
    引用时不得当成一手事实。
    """

    id: str
    name: str
    category: Category
    subcategory: str | None = None
    attack_range: int | None = None
    delayed: bool = False


class DeckCard(_FrozenModel):
    """牌堆里的一张牌：只写身份引用与花色点数，不重复牌名级属性。"""

    type: str
    suit: Suit
    rank: int


class DeckExpect(_FrozenModel):
    """牌堆的声明式分布期望；加载期语义校验要求它与实况相符。

    ``total`` / ``by_type`` 必写；``by_suit`` / ``by_category`` 可选，缺失即不校验
    —— 基本 53 张档位的花色分布是 13/19/7/14，写不出"每花色 27"，故不写这两项。
    三项都沿用**双向穷尽**语义：实况出现但未声明的键同样报错。
    """

    total: int
    by_type: dict[str, int]
    by_suit: dict[str, int] | None = None
    by_category: dict[str, int] | None = None


class Deck(_FrozenModel):
    """一个牌堆档位；``slot_limit`` 是**声明式分档字段**（默认 2）。

    标准版含 EX 的 108 张在 ♠2 / ♣2 / ♥Q / ♦Q 四个槽位上各有三张，故该档显式写 3；
    这是档位属性而不是全局放宽，否则基本 53 张"每槽至多两张"的机械保护会失效。
    """

    slot_limit: int = DEFAULT_SLOT_LIMIT
    expect: DeckExpect | None = None
    cards: tuple[DeckCard, ...]


class ContentPack(_FrozenModel):
    """一个已通过加载期校验的内容包（含全部牌堆档位）。"""

    schema_version: int
    pack_id: str
    deck_profile: str
    card_types: tuple[CardType, ...]
    decks: dict[str, Deck]

    @property
    def deck(self) -> Deck:
        """本内容包默认选中的牌堆档位（校验保证 ``deck_profile`` 必存在）。"""
        return self.decks[self.deck_profile]
