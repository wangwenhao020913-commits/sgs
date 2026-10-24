r"""武将技能的**定义**（票 03 起；全表登记归票 12）。

本模块与 :mod:`sgs.skills.catalog` 同构：一条 :class:`~sgs.skills.catalog.SkillDef`
= 一个 ``hero.<武将 id>.<短 id>`` 技能（id 的派生与数据侧共用
``sgs.data.hero_skill_id``，不在两处各拼一次前缀）。定义与回调住本模块，
:data:`sgs.skills.CATALOG` 仍是**唯一入口**（票 12 §要做什么 1）。

**票 03 只落 H 族（转化牌泛化）的四条技能**；其余 25 条由票 04–11 逐族落地、票 12 照表
登记。四条技能是：

===========  ==================  ==================  ==============================
技能         源筛选               目标牌名             形态
===========  ==================  ==================  ==============================
关羽【武圣】  红色牌、手牌∪装备     【杀】（普通）        单源、``PLAY`` + ``RESPOND``
赵云【龙胆】  【杀】、手牌∪装备     【闪】               单源、``RESPOND`` / ``DODGE``
赵云【龙胆】  【闪】、手牌∪装备     【杀】（普通）        单源、``PLAY`` + ``RESPOND``
大乔【国色】  方块牌、手牌∪装备     【乐不思蜀】          单源、``PLAY``
华佗【急救】  红色牌、手牌∪装备     【桃】               单源、**限回合外**
甄姬【倾国】  黑色**手牌**          【闪】               单源、``DODGE`` + ``RESPOND``
甘宁【奇袭】  黑色牌、手牌∪装备     【过河拆桥】          单源、``PLAY``
界关羽【武圣】 红色牌、手牌∪装备     【杀】（普通）        单源、``PLAY`` + ``RESPOND``
                                                      ＋②方块【杀】无距离限制
                                                      （同一技能上的 ``TARGET_POOL``）
===========  ==================  ==================  ==============================

**区域默认**（W4-3 / `GLOSSARY.md`）：五条牌面都只写"一张牌"，没有一条写"区域内的牌"
或"场上的牌" ⇒ 源区一律是 :func:`_hand_equip`（手牌 ∪ 装备）。**不设统一的转化源区**，
更不默认包含判定区；将来某条转化技的牌面真写了别的区域，改它自己那一行的 ``source_zone``。

**谓词全部内联在回调里**（票 03 §要做什么 3）：颜色 / 花色 / 牌名（三者在
:func:`_convertible` 的谓词分支里）、区域（``card_ids`` 给出取牌区）、时机（回合外，
:func:`_outside_turn` 读 ``state.turn_seat``）。**不建声明式 DSL**——回调自己声明
"源筛选 + 目标牌名"，组合与合法性走 :mod:`sgs.skills.virtual` 的共享构造器。

牌面的三形态（单源 / 多源 / 纯虚拟）与"属性不继承"见
`03` §5「效果原语（Effect Primitives）清单」的四列表。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sgs.data import Color, SkillType, Suit, color_of_suit, hero_skill_id
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    Modifier,
    ModifierPoint,
    behavior_for,
    is_slash,
    locate,
    target_sets_match,
)
from sgs.state import (
    GameState,
    PlayCard,
    QueryKind,
    Step,
    StepPool,
    UseCard,
    Zone,
)

from .catalog import (
    HeroSkillDecl,
    ModifierContext,
    SkillDef,
    SkillSourceKind,
    resolve_instances,
)
from .flow import seat_choice
from .virtual import card_ids, single_conversion

__all__ = [
    "CONVERSION_SKILLS",
    "conversion_offers",
    "conversion_offers_by_source",
    "conversion_preactivations",
    "conversion_skill_definitions",
    "is_transformed_action",
    "owned_conversion_skill_ids",
    "resolve_conversion_confirmation",
    "transform_candidates",
]

ZoneFilter = Callable[[GameState, int], tuple[int, ...]]
"""取牌区谓词：``(state, seat) -> 该座位在这个区域里的牌 id``（区域是闭集的一个谓词）。"""

Predicate = Callable[[GameState, int], bool]
"""来源谓词：``(state, card_id) -> bool``（颜色 / 花色 / 牌名的公共形状）。"""


# ---------------------------------------------------------------------------
# 五个谓词的公共形状（全部内联在各自技能的 CANDIDATES 回调里）
# ---------------------------------------------------------------------------


def _convertible(
    state: GameState,
    card_id: int,
    *,
    color: Color | None = None,
    suit: str | None = None,
    card_type: str | None = None,
) -> bool:
    """这张牌满不满足该技能的**牌面谓词**（颜色 / 花色 / 牌名三选一）。

    颜色读 ``CardRef.color``（花色派生的唯一真源），不重抄花色映射；花色比值、牌名读
    ``CardRef.type``。三个判据互斥使用——每条技能的源筛选只声明其中一个（票 03 §表）。
    """
    card = state.card(card_id)
    if color is not None:
        resolved = card.color if card.color is not None else color_of_suit(card.suit)
        return resolved is color
    if suit is not None:
        return card.suit == suit
    if card_type is not None:
        return card.type == card_type
    return False


def _outside_turn(state: GameState, seat: int) -> bool:
    """**时机谓词**（回合外）：``state.turn_seat`` 不是自己（票 03 §验收 6）。

    ``turn_seat is None``（没有回合在跑的人为局面）算**回合外**——与"回合内"的反面
    同姿态；真正跑起来的对局里 ``turn_seat`` 恒有值。
    """
    return state.turn_seat != seat


def _hand_equip(state: GameState, seat: int) -> tuple[int, ...]:
    """**区域默认**：手牌 ∪ 装备（牌面没写区域时按这一档读，`GLOSSARY.md`「区域默认」）。

    判定区**不默认包含**（W4-3）：转化技的牌面都只写"一张牌 / 一张红色牌 / 一张方块牌 /
    一张【杀】/【闪】"，没有一条写"区域内的牌"或"场上的牌"，故一律落到手牌 + 装备。
    区序固定（先手牌后装备），逐种子可复现。

    **例外由牌面自己声明**：M6 票 02 的【倾国】写的是"一张黑色**手牌**"，故它声明
    :func:`_hand`（只有手牌），不套用本默认——区域默认是"牌面没写区域"时的那一档，
    不是统一强制。
    """
    return card_ids(state, seat, Zone.HAND) + card_ids(state, seat, Zone.EQUIP)


def _hand(state: GameState, seat: int) -> tuple[int, ...]:
    """**手牌**（牌面明写"手牌"时的区域谓词；【倾国】是第一个消费者，§九 L962）。"""
    return card_ids(state, seat, Zone.HAND)


# ---------------------------------------------------------------------------
# 一个转化技能 = 源筛选 + 目标牌名 + 形态
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConversionSkill(HeroSkillDecl):
    """H 族一条技能的**声明**：源筛选谓词 + 目标牌名 + 可用语境（票 03 §表一行）。"""

    target_type_id: str
    source_zone: ZoneFilter
    predicate: Predicate
    scopes: tuple[CandidateScope, ...]
    outside_turn_only: bool = False
    modifiers: tuple[Modifier, ...] = ()
    """这条转化技能**额外**的修饰器（M6 票 04 的加法；缺省空 = 既有六条一字不改）。

    转化候选本身只落 ``CANDIDATES``；但界关羽【武圣】②"你使用**方块**【杀】无距离限制"是
    同一张武将牌上的一条**连续型**效果，必须与转化入口共用同一个 ``SkillDef``（一个技能
    一条定义），故在这里给声明面一个修饰器口子，由
    :func:`conversion_skill_definitions` 合并进定义（合并 = 一份真源，不是两份拷贝）。
    """
    requirement: str = ""
    """这条转化"需要玩家做什么"的人话（item 4）；`conversion_skill_definitions` 透进
    :attr:`~sgs.skills.catalog.SkillDef.requirement`（龙胆两个方向各一条 ⇒ 用"；"并起来）。"""

    shared_with: tuple[str, ...] = ()
    """**同一份牌面文本**也由这些武将 id 持有的那条转化技（M6 票 07 的加法；缺省空）。

    界甘宁【奇袭】（§十九 L2129）与标准甘宁【奇袭】（§九 L1009）**逐字相同**，
    故这里写 ``shared_with=("jie_ganning",)``——一条声明派生两个全限定技能 id，
    而不是把同一行声明复制两份：源筛选 / 产物 / 时机只有一份真源，界版只是另一个
    武将 id（与【克己】【马术】的"另一个武将 id"同一姿态）。
    """

    @property
    def skill_ids(self) -> tuple[str, ...]:
        """本声明的全部全限定技能 id：自己 + :attr:`shared_with`（按声明序）。"""
        return tuple(
            hero_skill_id(hero_id, self.short_id)
            for hero_id in (self.hero_id, *self.shared_with)
        )

    def select(self, state: GameState, seat: int) -> tuple[int, ...]:
        """本技能在当前局面下**筛好的**来源牌（区域谓词 + 牌面谓词 + 时机谓词）。"""
        if self.outside_turn_only and not _outside_turn(state, seat):
            return ()
        return tuple(
            card_id
            for card_id in self.source_zone(state, seat)
            if self.predicate(state, card_id)
        )

    def candidate(self, state: GameState, ctx: ModifierContext, current: object) -> object:
        """本技能的 ``CANDIDATES`` 回调：筛源 → 共享构造器追加候选。"""
        return (
            *current,  # type: ignore[misc]
            *single_conversion(
                state,
                ctx,
                target_type_id=self.target_type_id,
                cards=self.select(state, ctx.subject),
                scopes=self.scopes,
            ),
        )


RED = Color.RED


def _red(state: GameState, card_id: int) -> bool:
    """颜色谓词：红色牌（【武圣】/【急救】）。"""
    return _convertible(state, card_id, color=RED)


def _diamond(state: GameState, card_id: int) -> bool:
    """花色谓词：方块牌（【国色】）。"""
    return _convertible(state, card_id, suit="diamond")


def _black(state: GameState, card_id: int) -> bool:
    """颜色谓词：黑色牌（【倾国】§九 L962 / 【奇袭】§九 L1009）。"""
    return _convertible(state, card_id, color=Color.BLACK)


def _slash(state: GameState, card_id: int) -> bool:
    """牌名谓词：【杀】（【龙胆】的【杀】→【闪】）。

    M5-05b-fix：按【杀】**家族**判据（:func:`~sgs.effects.is_slash`）——火【杀】/ 雷【杀】
    也能被龙胆转成【闪】。另一条方向 ``_dodge``（【闪】→【杀】的源）仍逐字相等。
    """
    return is_slash(behavior_for(state.card(card_id)))


def _dodge(state: GameState, card_id: int) -> bool:
    """牌名谓词：【闪】（【龙胆】的【闪】→【杀】）。"""
    return _convertible(state, card_id, card_type="dodge")


def _product_in_pack(state: GameState, type_id: str) -> bool:
    """这种牌名在**当前牌堆**里存在实体牌吗（界【龙胆】的 酒↔桃 方向用）。

    牌堆档位是内容事实：标准 108 档没有【酒】（它随军争 160 档进入）。把一张【桃】转成
    一张**牌堆里根本不存在的牌**时，虚拟牌的名字只能回落到 type id（界面上会出现
    "wine"），且"使用一张牌堆外的牌"也不是这套数据的语义。故这两个方向只在产物牌名
    有实体牌时产出候选；杀 / 闪 / 桃三个既有方向不受影响（标准包里都有实体牌）。
    """
    return any(card.type == type_id for card in state.cards)


def _wine(state: GameState, card_id: int) -> bool:
    """牌名谓词：【酒】（【龙胆】的【酒】→【桃】，§十九 L2083–L2084）。"""
    return _convertible(state, card_id, card_type="wine") and _product_in_pack(state, "peach")


def _peach_to_wine(state: GameState, card_id: int) -> bool:
    """牌名谓词：【桃】（【龙胆】的【桃】→【酒】；产物牌名不在本档牌堆时不产出）。"""
    return _convertible(state, card_id, card_type="peach") and _product_in_pack(state, "wine")


def _diamond_slash_target_pool(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """界关羽【武圣】②：你使用**方块【杀】无距离限制**（§十九 L2068–L2070）。

    落点是 ``TARGET_POOL``（"无距离限制" = 目标池豁免"距离 ≤ 攻击范围"这一条判据）：
    把**全部其他存活座位**并进池子。用"并集"而不是直接返回全集，是为了不与链上**其它**
    目标池修饰器打架——链序 ``(priority_class, skill_id, owner)`` 里后跑的回调（如
    【空城】的剔除）仍能删掉不合法的目标；本回调只**补上**被攻击范围挡掉的那些。

    牌面判据读 ``ctx.card`` 的**花色 + 族**：方块是 `CardRef.suit` 的牌面事实（转化牌
    单源继承花色，故"方块牌当【杀】"同样命中）；族判据走 :func:`~sgs.effects.is_slash`
    而不是牌型相等（火 / 雷【杀】同族）。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    if ctx.card.suit != Suit.DIAMOND.value:
        return current
    everyone = tuple(
        player.seat for player in state.players if player.alive and player.seat != ctx.subject
    )
    return tuple(dict.fromkeys((*current, *everyone)))


CONVERSION_SKILLS: tuple[ConversionSkill, ...] = (
    # 关羽【武圣】：一张红色牌当【杀】使用或打出（规则 976）。
    ConversionSkill(
        hero_id="guanyu",
        short_id="wusheng",
        target_type_id="slash",
        source_zone=_hand_equip,
        predicate=_red,
        scopes=(CandidateScope.PLAY, CandidateScope.RESPOND),
        requirement="选择1张红色牌当【杀】使用或打出",
    ),
    # 界关羽【武圣】（M6 票 04，§十九 L2068–L2070）：①与标准版逐字相同（红牌当【杀】）；
    # ②"你使用**方块**【杀】无距离限制"是同一技能的连续型效果，挂在 TARGET_POOL 上。
    ConversionSkill(
        hero_id="jie_guanyu",
        short_id="wusheng",
        target_type_id="slash",
        source_zone=_hand_equip,
        predicate=_red,
        scopes=(CandidateScope.PLAY, CandidateScope.RESPOND),
        modifiers=(Modifier(ModifierPoint.TARGET_POOL, _diamond_slash_target_pool),),
        requirement="选择1张红色牌当【杀】使用或打出；方块【杀】无距离限制",
    ),
    # 赵云【龙胆】两条方向共用一个技能 id（规则 982）。
    # M6 票 16：界赵云【龙胆】的 杀↔闪 两向与标准版**逐字相同**（§十九 L2082–L2084），
    # 故经 ``shared_with`` 派生 ``hero.jie_zhaoyun.longdan``，不复制这两行；另加的
    # 酒↔桃 两向是**另一份声明**（产物牌名不同），见下面两条。
    ConversionSkill(
        hero_id="zhaoyun",
        short_id="longdan",
        target_type_id="dodge",
        source_zone=_hand_equip,
        predicate=_slash,
        scopes=(CandidateScope.DODGE, CandidateScope.RESPOND),
        requirement="选择1张【杀】当【闪】打出",
        shared_with=("jie_zhaoyun",),
    ),
    ConversionSkill(
        hero_id="zhaoyun",
        short_id="longdan",
        target_type_id="slash",
        source_zone=_hand_equip,
        predicate=_dodge,
        scopes=(CandidateScope.PLAY, CandidateScope.RESPOND),
        requirement="选择1张【闪】当【杀】使用或打出",
        shared_with=("jie_zhaoyun",),
    ),
    # 界赵云【龙胆】的 酒↔桃 两向（§十九 L2083–L2084）。【酒】当【桃】：出牌阶段可当
    # 【桃】对自己使用（受伤时），濒死求桃窗口由 ``_transformed_peaches`` 按产物牌名取
    # （该窗口不读 ``scopes``）。【桃】当【酒】：出牌阶段使用（每回合限一次由【酒】的
    # 行为表收住，目标只能是自己）。
    ConversionSkill(
        hero_id="jie_zhaoyun",
        short_id="longdan",
        target_type_id="peach",
        source_zone=_hand_equip,
        predicate=_wine,
        scopes=(CandidateScope.PLAY,),
        requirement="选择1张【酒】当【桃】使用",
    ),
    ConversionSkill(
        hero_id="jie_zhaoyun",
        short_id="longdan",
        target_type_id="wine",
        source_zone=_hand_equip,
        predicate=_peach_to_wine,
        scopes=(CandidateScope.PLAY,),
        requirement="选择1张【桃】当【酒】使用",
    ),
    # 大乔【国色】：一张方块牌当【乐不思蜀】使用（规则 1024）。
    ConversionSkill(
        hero_id="daqiao",
        short_id="guose",
        target_type_id="indulgence",
        source_zone=_hand_equip,
        predicate=_diamond,
        scopes=(CandidateScope.PLAY,),
        requirement="选择1张方块牌当【乐不思蜀】使用",
    ),
    # 华佗【急救】：回合外一张红色牌当【桃】使用（规则 1041）。
    # M6 票 18：界华佗【急救】与标准版**逐字相同**（§十九 L2183）⇒ 同一条声明经
    # ``shared_with`` 派生 ``hero.jie_huatuo.jijiu``，不复制第二行。
    ConversionSkill(
        hero_id="huatuo",
        short_id="jijiu",
        target_type_id="peach",
        source_zone=_hand_equip,
        predicate=_red,
        scopes=(CandidateScope.PLAY,),
        outside_turn_only=True,
        requirement="选择1张红色牌当【桃】使用",
        shared_with=("jie_huatuo",),
    ),
    # 甄姬【倾国】（M6 票 02）：一张**黑色手牌**当【闪】使用或打出（§九 L962）。
    # 区域 = 只有手牌：牌面明写"手牌"，故不套 `_hand_equip` 的区域默认。
    # M6 票 09：界甄姬【倾国】与标准版**逐字相同**（§十九 L2048–L2049）⇒ 同一条声明经
    # ``shared_with`` 派生 ``hero.jie_zhenji.qingguo``，不复制第二行。
    ConversionSkill(
        hero_id="zhenji",
        short_id="qingguo",
        target_type_id="dodge",
        source_zone=_hand,
        predicate=_black,
        scopes=(CandidateScope.DODGE, CandidateScope.RESPOND),
        requirement="选择1张黑色手牌当【闪】使用或打出",
        shared_with=("jie_zhenji",),
    ),
    # 甘宁【奇袭】（M6 票 03）：一张黑色牌当【过河拆桥】使用（§九 L1009）。
    # 区域默认 = 手牌 ∪ 装备：牌面没写区域，按 `_hand_equip` 那一档读。
    # M6 票 07：界甘宁【奇袭】牌面逐字相同（§十九 L2129）⇒ 同一条声明经 ``shared_with``
    # 派生 ``hero.jie_ganning.qixi``，不复制第二行。
    ConversionSkill(
        hero_id="ganning",
        short_id="qixi",
        target_type_id="dismantlement",
        source_zone=_hand_equip,
        predicate=_black,
        scopes=(CandidateScope.PLAY,),
        requirement="选择1张黑色牌当【过河拆桥】使用",
        shared_with=("jie_ganning",),
    ),
)
"""H 族五条技能（六条声明：龙胆两个方向各一条；界甘宁【奇袭】经 ``shared_with`` 共用
甘宁那一行）的逐字声明（票 03 §表 + M6 票 02【倾国】/ 票 03【奇袭】/ 票 04 界关羽【武圣】/
票 07 界甘宁【奇袭】）。"""


def _modifier(skill: ConversionSkill) -> Modifier:
    """一条转化技能的 ``CANDIDATES`` 修饰器（回调 = 该声明的 :meth:`candidate`）。"""
    return Modifier(ModifierPoint.CANDIDATES, skill.candidate)


def conversion_offers(
    state: GameState, seat: int, target_type_id: str
) -> tuple[tuple[int, str], ...]:
    """此刻**能把哪张牌当** ``target_type_id``：``((来源实体牌 id, 技能 id), …)``（票 14）。

    这是"源筛选"的唯一真源：逐条按 :data:`CONVERSION_SKILLS` 的 ``target_type_id`` /
    ``source_zone`` / 牌面谓词 / 时机谓词核对（:meth:`ConversionSkill.select` 一次给全），
    故"手牌 ∪ 装备"（W4-3 的区域默认）只是表里的参数——
    "提供一张 B"的候选面与接入层的技能归属**读同一份**，不各写一份谓词。

    **先过"这个座位真有这条技能"**（:func:`~sgs.skills.catalog.resolve_instances`）：源谓词
    只回答"这张牌当不当得成 B"，不回答"你会不会这条技能"——少了这一道，红牌在任何人手里
    都会被算成【武圣】（身份门控 / 武将牌的判断在实例解析那一处，不在这里重写）。

    **只覆盖单来源转化**：多来源的【丈八蛇矛】是装备的 ``CANDIDATES`` 回调、不在本表里
    （它的"先选两张"是另一维，票 14 不做）。方向也由 ``target_type_id`` 逐条区分：
    需要【杀】时【龙胆】只给【闪】、需要【闪】时只给【杀】（票 14 顺手修的现有 bug）。
    """
    owned = {
        instance.skill_id
        for instance in resolve_instances(state)
        if instance.owner == seat
    }
    return tuple(
        (card_id, owned_id)
        for skill in CONVERSION_SKILLS
        if skill.target_type_id == target_type_id
        for owned_id in skill.skill_ids
        if owned_id in owned
        for card_id in skill.select(state, seat)
    )


def conversion_offers_by_source(
    state: GameState, seat: int, target_type_id: str
) -> dict[int, tuple[str, ...]]:
    """:func:`conversion_offers` 的**按来源实体牌分组**形状：``{来源 id: (技能 id, …)}``。

    两处读取方共用同一条分组（W4-3 收口）：

    - :func:`sgs_web.projection._offers_by_source`（界面：来源牌 → 技能按钮 / 文案）；
    - :func:`conversion_preactivations`（接入层：来源牌 → 该技能的预发动清单）。

    分组只写一次，两处都调这里——否则"来源 → 技能"这层含义会有第二份实现。
    """
    grouped: dict[int, list[str]] = {}
    for source, skill_id in conversion_offers(state, seat, target_type_id):
        grouped.setdefault(int(source), []).append(skill_id)
    return {source: tuple(skill_ids) for source, skill_ids in grouped.items()}


def _candidate_skill_ids(state: GameState, owner: int, card: Any) -> tuple[str, ...]:
    """一张**虚拟牌候选**归属哪些已拥有的转化技（清单渲染的技能归属判据）。

    - **单来源**：读 :func:`conversion_offers_by_source`——区域 / 颜色 / 花色 / 牌名 /
      时机谓词全在那份声明表里，故"这张【闪】能不能当【杀】"不会在这里有第二份答案。
      **装备转化技**（M5-05b 的朱雀羽扇）不在 H 族声明表里，另按"载体 = 装备区实体牌 +
      产物牌名 + 单来源"归属（见下方内联注释）；
    - **多来源**（【丈八蛇矛】）：单源声明表不覆盖它，改按定义上的
      :attr:`~sgs.skills.catalog.SkillDef.virtual_source_count` + ``virtual_type_ids``
      归属——两张来源的虚拟【杀】只有张数是 2 的那条技能能转出来。
    """
    sources = tuple(int(source) for source in card.virtual_sources)
    if len(sources) == 1:
        offered = conversion_offers_by_source(state, owner, card.type).get(sources[0], ())
        # 装备转化技（M5-05b 的朱雀羽扇）不在 H 族声明表里：来源谓词内联在
        # ``CANDIDATES`` 回调里，声明上只有产物牌名，故按"产物牌名 + 单来源"归属
        # ——候选存在本身已证明来源筛过（清单与确认都从这份内部候选池派生）。
        declared = tuple(
            instance.skill_id
            for instance in resolve_instances(state)
            if instance.owner == owner
            and instance.source.kind is SkillSourceKind.EQUIPMENT_CARD
            and card.type in instance.definition.virtual_type_ids
            and instance.definition.virtual_source_count == 1
        )
        return tuple(dict.fromkeys((*offered, *declared)))
    return tuple(
        instance.skill_id
        for instance in resolve_instances(state)
        if instance.owner == owner
        and card.type in instance.definition.virtual_type_ids
        and instance.definition.virtual_source_count == len(sources)
    )


_SOURCE_ZONE_ORDER: tuple[Zone, ...] = (Zone.HAND, Zone.EQUIP, Zone.JUDGE)
"""清单 ``Step.zones`` 的规范化次序（与 :func:`_hand_equip` 的区序一致，判定区垫底）。"""


def is_transformed_action(action: Any) -> bool:
    """这个候选是不是**转化牌**（``virtual_sources`` 非空的使用 / 打出）。

    W4-fix-2 的候选收口判据（用户裁定 A）：转化技的候选从 ``legal_actions`` 收回到
    :func:`transform_candidates` 那份**内部**池；判据读**来源集合**而不是"虚拟牌"
    ——纯虚拟牌（【八卦阵】的视为【闪】，没有来源）不属于任何转化技，照常留在候选里。

    W4-fix-6（D.4）：这是**唯一**的转化牌判据；:func:`transform_candidates` 也读它
    （原先两处判据不一致：那里收 ``is_virtual``、这里收 ``virtual_sources`` 非空，
    于是"纯虚拟牌照常留在候选里"的分支不可达）。
    """
    card = getattr(action, "card", None)
    return card is not None and card.is_virtual and bool(card.virtual_sources)


def transform_candidates(
    kind: QueryKind,
    actions: Iterable[Any],
) -> tuple[Any, ...]:
    """这次询问里**属于转化技入口**的候选（W4-3 的清单 / W4-fix-2 的确认都读这一份）。

    - 出牌阶段 / 【闪】窗口 / 求桃 / 响应窗口：``UseCard`` 与 ``PlayCard`` 都收
      （构造子由候选生成侧决定：``mode=PLAY`` 通常给 ``PlayCard``，但【万箭齐发】的
      "需要【闪】"走 ``CandidateScope.DODGE``，候选因此是 ``UseCard``）；
    - 其余询问：没有转化技入口（返回空元组）。

    W4-fix-6 的 A 项曾按**响应语境**排除借刀杀人的"被迫使用"（那时没有"使用牌"的第二段
    预发动，清单表达不出"目标由窗口固定"）。W5-02b 起那扇窗口的清单就是
    ``conversion_preactivations(..., fixed_target=True)`` 的**来源步**，确认解析成
    ``UseCard(虚拟【杀】, (Bn,))``，故例外已删除——五条转化技在**所有**响应窗口同形。

    这份池是**内部**的：调用方（引擎）只把其中的转化牌（:func:`is_transformed_action`）
    从 ``legal_actions`` 收回，清单与确认校验仍从这一份读——"清单 = 候选"因此没有第二份谓词。
    """
    if kind not in (
        QueryKind.PLAY_PHASE,
        QueryKind.DODGE,
        QueryKind.PEACH,
        QueryKind.RESPOND,
    ):
        return ()
    return tuple(
        action
        for action in actions
        if isinstance(action, (UseCard, PlayCard)) and is_transformed_action(action)
    )


def owned_conversion_skill_ids(state: GameState, seat: int) -> frozenset[str]:
    """``seat`` 此刻拥有的全部**转化技 id**（声明上的 ``virtual_type_ids``，唯一真源）。

    单源（武圣 / 龙胆 / 国色 / 急救）与多源（丈八蛇矛）都落在这条判据上：一条技能的
    ``SkillDef`` 声明了自己的虚拟牌名，它就把"这张牌当那张牌"算作自己的入口。用途只有
    一个——让确认路径认出"这次提交是转化技确认"，从而走 :func:`resolve_conversion_confirmation`
    而不是普通复合动作校验；**不回答"这组来源此刻合不合法"**（那是候选池的事）。
    """
    return frozenset(
        instance.skill_id
        for instance in resolve_instances(state)
        if instance.owner == seat and instance.definition.virtual_type_ids
    )


def resolve_conversion_confirmation(
    state: GameState,
    owner: int,
    skill_id: str,
    picks: Sequence[int],
    choices: Sequence[str],
    candidates: Iterable[Any],
    *,
    steps: Sequence[Step],
) -> Any | str:
    """把一次"按清单确认"解析成**候选池里的那条动作**；``str`` = 首个失败原因。

    W4-fix-2 的唯一确认入口（引擎的 ``submit`` 与 AI 覆盖判据同读它）：

    - **不建第二份合法性**：解析就是在这份**内部候选**（:func:`transform_candidates`，
      清单也是它派生的）里按 ``(技能归属, 来源集合, 目标集合)`` 找那一条；找不到即失败。
      来源集合与目标集合的顺序无关（来源在 ``virtual_sources`` 里本就已排序）；
    - **是否需要目标步由清单回答**（``steps`` 里有没有 ``min > 0`` 的 ``seat`` 步）：
      有目标步 ⇒ 按**完整目标集**比较（先比原始选择个数——重复选择因此被拒——再比
      去重后的集合，见 :func:`sgs.effects.target_sets_match`）；没有目标步 ⇒ 目标已被清单
      判为唯一（或本就没有目标），不比较。同一条判据也是清单的形状；
    - **W7-01b**：多目标组合同样走这里——``ActivateSkill(虚拟牌来源, 1..max 个目标)``
      解析成候选池里那条 ``UseCard(虚拟牌, targets=…)``；清单只表达"最多可选几个"，
      不在池里的组合由上面的比较拒绝。
    - 失败原因分三档：来源组不在池里 / 目标不在池里 / 清单外的选项文本。

    返回的动作自带本次询问的 ``answers_seq``，调用方把它当普通使用 / 打出动作执行。
    """
    wanted_sources = tuple(sorted(int(card_id) for card_id in picks))
    needs_target = any(step.name == "seat" and step.min > 0 for step in steps)
    wanted_targets: list[int] = []
    for choice in choices:
        text = str(choice)
        if not text.startswith("seat:"):
            return f"清单外的选项：{text}"
        try:
            wanted_targets.append(int(text[len("seat:") :]))
        except ValueError:
            return f"清单外的选项：{text}"
    if wanted_targets and not needs_target:
        return "这次转化没有目标步：来源之外的答案不被接受"
    source_matched = False
    for action in candidates:
        card = getattr(action, "card", None)
        if card is None or not card.virtual_sources:
            continue
        if skill_id not in _candidate_skill_ids(state, owner, card):
            continue
        if tuple(sorted(int(source) for source in card.virtual_sources)) != wanted_sources:
            continue
        source_matched = True
        if needs_target and not target_sets_match(
            wanted_targets, getattr(action, "targets", ())
        ):
            continue
        return action
    if not source_matched:
        return "来源牌不合法：这组牌此刻不能这样转化"
    return "目标不合法：这组来源没有那条目标"


def conversion_preactivations(
    state: GameState,
    owner: int,
    actions: Iterable[Any],
    *,
    fixed_target: bool = False,
) -> dict[str, tuple[Step, ...]]:
    """把一次询问的**虚拟牌候选**按转化技渲染成**预发动清单**（W4-3）。

    转化技的最终动作仍是普通的 ``UseCard(虚拟牌, 目标)``（本来就一次动作）；预发动只是
    接入层给它一个"点技能 → 选源牌 → 选目标 → 确认"的本地入口。故清单不描述"复合动作"，
    而是描述**这次询问里已经存在的那些候选**：

    - **来源步**（``name="card"``）：候选的 ``virtual_sources`` 就是来源组；每组的张数
      （单源 1 / 丈八 2）⇒ ``min = max = 组大小``。``zones`` = 这些来源**此刻**所在的区
      （判定区永不出现——区域默认不包含它）；
    - **目标步**（``name="seat"``，W7-01b）：``min = 1``、``max`` = 本次候选组合的最长
      目标数；``pool`` 按前序来源逐行给该来源组**全部组合的全部目标**（``seat:N``）。
      目标完全唯一（所有候选都是同一个单目标、或根本就没有目标）时不下发——求桃 /
      【万箭】 / 无方天的借刀等既有形状因此不变；出现多目标候选（W7 的方天式额外目标）
      才下发，座位步在界面上是多选。

    **清单与候选同源**：张数、目标池、次数链（``check_count``）、区域与时机谓词都没有
    在这里重算一次——它们已经体现在"候选存在与否"里。空 dict = 这次询问没有可预发动的
    转化技。渲染**只读状态**：不铺选择面、不抽随机数、不搬牌。

    ``actions`` 由调用方给：它是 :func:`transform_candidates` 那份**内部**候选池，
    故这扇窗口是否收转化候选（借刀杀人的被迫使用属 W5）由那一处判据回答，这里不重判。
    ``fixed_target=True``：本次语境的**目标由窗口承载**（求桃的濒死者、【万箭】/【南蛮】
    这类响应窗口的被问者、借刀的被迫使用）——候选目标**完全唯一**时清单只下来源步
    （`spec.md` §2 的急救行 / W4-fix-6 的【万箭】行）；多目标候选仍下发目标步（借刀窗口
    的 Bn 是该步的首项、必选）。
    """
    groups: dict[str, dict[tuple[int, ...], list[tuple[int, ...]]]] = {}
    source_zones: dict[int, Zone] = {}
    for action in actions:
        card = getattr(action, "card", None)
        if card is None or not card.is_virtual:
            continue
        sources = tuple(int(source) for source in card.virtual_sources)
        if not sources:
            continue  # 纯虚拟牌（八卦阵的视为【闪】）没有转化技归属
        targets = tuple(int(target) for target in (getattr(action, "targets", None) or ()))
        skill_ids = _candidate_skill_ids(state, owner, card)
        if not skill_ids:
            continue
        for skill_id in skill_ids:
            rows = groups.setdefault(skill_id, {}).setdefault(sources, [])
            if targets not in rows:
                rows.append(targets)
        for source in sources:
            source_zones.setdefault(source, locate(state, source).zone)

    out: dict[str, tuple[Step, ...]] = {}
    for skill_id in sorted(groups):
        steps = _manifest_steps(
            groups[skill_id], source_zones, fixed_target=fixed_target
        )
        if steps is not None:
            out[skill_id] = steps
    return out


def _manifest_steps(
    per_source: dict[tuple[int, ...], list[tuple[int, ...]]],
    source_zones: dict[int, Zone],
    *,
    fixed_target: bool = False,
) -> tuple[Step, ...] | None:
    """一个技能的"来源组 → 目标组合"表 → 清单步骤；不可表达的形状返回 ``None``。

    - **来源步**：``min = max`` = 组大小（单源 1 / 丈八 2）。同一个技能出现不同张数的
      来源组是**不可表达**的形状（不猜），返回 ``None``；
    - **目标步**（W7-01b / W7-03）：``min = 1``、``max = 该技能候选组合的最长目标数``；
      ``pool`` 仍按来源组（``when`` = 来源答案）分列，但每行 options = 该来源组**全部组合
      的全部目标**（并集、按候选顺序去重），不再只列"每个组合的首目标"——否则额外目标在
      清单里永远点不到。不同来源组的上限不同时步上 ``max`` 取最大、逐行池各自限定；越界组合
      由确认的成员判定拒绝（清单只表达"最多可选几个"，不是第二份合法性）；每行还带出
      该来源组的**逐来源上限** ``StepPool.max``——``options`` 只说"哪些目标能点"，不说
      "最多点几个"，界面要收住逐来源上限只能读这一格（W7-03 的 D4）；行相同才并成一行；
    - ``fixed_target=True``（目标由窗口承载：求桃的濒死者 / 【万箭】·【南蛮】的响应 /
      借刀的被迫使用）**只在目标完全唯一时**省掉目标步——所有候选都是同一个单目标
      （或无目标）⇒ 没有可选的第二步。出现第二个目标、或任一组合的目标数 > 1（W7 的
      方天式多目标）⇒ 仍下发目标步（座位步因此可多选；借刀窗口的候选恒含 Bn，
      ``(Bn,)`` 是候选排序的自然首条，故目标步首项 = Bn）。
    """
    sizes = {len(sources) for sources in per_source}
    if len(sizes) != 1:
        return None
    count = sizes.pop()
    zones_used = {
        source_zones[source] for sources in per_source for source in sources
    }
    options = tuple(dict.fromkeys(str(source) for sources in per_source for source in sources))
    steps = [
        Step(
            name="card",
            zones=tuple(zone for zone in _SOURCE_ZONE_ORDER if zone in zones_used),
            min=count,
            max=count,
            pool=(StepPool(when=(), options=options),),
            order=0,
        )
    ]
    combos = [targets for rows in per_source.values() for targets in rows]
    if not combos or all(not targets for targets in combos):
        return tuple(steps)
    if fixed_target and _targets_are_truly_unique(combos):
        return tuple(steps)
    rows = [
        (
            tuple(str(source) for source in sources),
            tuple(
                dict.fromkeys(
                    seat_choice(target)
                    for targets in targets_list
                    for target in targets
                )
            ),
            max(len(targets) for targets in targets_list),
        )
        for sources, targets_list in per_source.items()
    ]
    # 行相同才并成一行：``when`` 只回答"哪组来源"，而**逐来源上限**（``StepPool.max``）
    # 可能不同（方天只放宽"最后一张手牌"的转化源）——上游相同也必须分开，界面才收得住。
    if len({(row_options, row_max) for _when, row_options, row_max in rows}) == 1:
        rows = [((), rows[0][1], rows[0][2])]
    steps.append(
        Step(
            name="seat",
            min=1,
            max=max(row_max for _when, _options, row_max in rows),
            pool=tuple(
                StepPool(when=when, options=row_options, max=row_max)
                for when, row_options, row_max in rows
            ),
            order=1,
        )
    )
    return tuple(steps)


def _targets_are_truly_unique(combos: Iterable[tuple[int, ...]]) -> bool:
    """这些候选组合是否都是**同一个单目标**（``fixed_target`` 语境省掉目标步的判据）。

    空组合由调用方先处理；这里回答"目标步有没有信息量"：全是同一个单目标 ⇒ 没有。
    """
    combos = tuple(combos)
    if any(len(targets) > 1 for targets in combos):
        return False
    return len(set(combos)) == 1


def conversion_skill_definitions() -> tuple[SkillDef, ...]:
    """H 族四条技能的 :class:`~sgs.skills.catalog.SkillDef`（**去重后**每条技能一个）。

    :data:`CONVERSION_SKILLS` 里 `longdan` 出现两次（两个方向），而技能定义只有一个
    （id 唯一）：两条声明的修饰器**合并进同一条定义**，故"一个技能只产一种构造子"与
    "一个 id 一条定义"两件事同时成立。

    非锁定（技能面写了"你可以"）：发动是询问出来的；``skill_type = NORMAL``（都不是
    主公技）；``carrier = HERO`` ⇒ 优先级档由载体派生（武将档先于装备档）。
    """
    grouped: dict[str, list[ConversionSkill]] = {}
    for skill in CONVERSION_SKILLS:
        for skill_id in skill.skill_ids:
            grouped.setdefault(skill_id, []).append(skill)
    return tuple(
        SkillDef(
            skill_id=skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=tuple(
                dict.fromkeys(
                    modifier
                    for skill in skills
                    for modifier in (_modifier(skill), *skill.modifiers)
                )
            ),
            # 龙胆两个方向各一条声明 ⇒ 文案用"；"并起来；虚拟牌名去重后收成元组。
            requirement="；".join(skill.requirement for skill in skills),
            virtual_type_ids=tuple(dict.fromkeys(skill.target_type_id for skill in skills)),
        )
        for skill_id, skills in grouped.items()
    )
