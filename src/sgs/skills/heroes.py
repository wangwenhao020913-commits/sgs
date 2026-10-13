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
关羽【武圣】  红色牌、任意区域      【杀】（普通）        单源、``PLAY`` + ``RESPOND``
赵云【龙胆】  【杀】                【闪】               单源、``RESPOND`` / ``DODGE``
赵云【龙胆】  【闪】                【杀】（普通）        单源、``PLAY`` + ``RESPOND``
大乔【国色】  方块牌                【乐不思蜀】          单源、``PLAY``
华佗【急救】  红色牌                【桃】               单源、**限回合外**
===========  ==================  ==================  ==============================

**五个谓词全部内联在回调里**（票 03 §要做什么 3）：颜色 / 花色 / 牌名（三者在
:func:`_convertible` 的谓词分支里）、区域（``card_ids`` 给出取牌区）、时机（回合外，
:func:`_outside_turn` 读 ``state.turn_seat``）。**不建声明式 DSL**——回调自己声明
"源筛选 + 目标牌名"，组合与合法性走 :mod:`sgs.skills.virtual` 的共享构造器。

牌面的三形态（单源 / 多源 / 纯虚拟）与"属性不继承"见
`03` §5「效果原语（Effect Primitives）清单」的四列表。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sgs.data import Color, SkillType, color_of_suit
from sgs.effects import CandidateScope, CarrierKind, Modifier, ModifierPoint
from sgs.state import GameState, Zone

from .catalog import HeroSkillDecl, ModifierContext, SkillDef
from .virtual import card_ids, single_conversion

__all__ = ["CONVERSION_SKILLS", "conversion_skill_definitions"]

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


def _all_cards(state: GameState, seat: int) -> tuple[int, ...]:
    """**任意区域**：手牌 ∪ 装备区 ∪ 判定区（区序固定，逐种子可复现）。"""
    return (
        card_ids(state, seat, Zone.HAND)
        + card_ids(state, seat, Zone.EQUIP)
        + card_ids(state, seat, Zone.JUDGE)
    )


def _hand(state: GameState, seat: int) -> tuple[int, ...]:
    """手牌（【龙胆】/【国色】的源：牌面写"一张牌"，标准版按**手牌**读）。"""
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
    requirement: str = ""
    """这条转化"需要玩家做什么"的人话（item 4）；`conversion_skill_definitions` 透进
    :attr:`~sgs.skills.catalog.SkillDef.requirement`（龙胆两个方向各一条 ⇒ 用"；"并起来）。"""

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


def _slash(state: GameState, card_id: int) -> bool:
    """牌名谓词：【杀】（【龙胆】的【杀】→【闪】）。"""
    return _convertible(state, card_id, card_type="slash")


def _dodge(state: GameState, card_id: int) -> bool:
    """牌名谓词：【闪】（【龙胆】的【闪】→【杀】）。"""
    return _convertible(state, card_id, card_type="dodge")


CONVERSION_SKILLS: tuple[ConversionSkill, ...] = (
    # 关羽【武圣】：一张红色牌当【杀】使用或打出（规则 976）。
    ConversionSkill(
        hero_id="guanyu",
        short_id="wusheng",
        target_type_id="slash",
        source_zone=_all_cards,
        predicate=_red,
        scopes=(CandidateScope.PLAY, CandidateScope.RESPOND),
        requirement="选择1张红色牌当【杀】使用或打出",
    ),
    # 赵云【龙胆】两条方向共用一个技能 id（规则 982）。
    ConversionSkill(
        hero_id="zhaoyun",
        short_id="longdan",
        target_type_id="dodge",
        source_zone=_hand,
        predicate=_slash,
        scopes=(CandidateScope.DODGE, CandidateScope.RESPOND),
        requirement="选择1张【杀】当【闪】打出",
    ),
    ConversionSkill(
        hero_id="zhaoyun",
        short_id="longdan",
        target_type_id="slash",
        source_zone=_hand,
        predicate=_dodge,
        scopes=(CandidateScope.PLAY, CandidateScope.RESPOND),
        requirement="选择1张【闪】当【杀】使用或打出",
    ),
    # 大乔【国色】：一张方块牌当【乐不思蜀】使用（规则 1024）。
    ConversionSkill(
        hero_id="daqiao",
        short_id="guose",
        target_type_id="indulgence",
        source_zone=_hand,
        predicate=_diamond,
        scopes=(CandidateScope.PLAY,),
        requirement="选择1张方块牌当【乐不思蜀】使用",
    ),
    # 华佗【急救】：回合外一张红色牌当【桃】使用（规则 1041）。
    ConversionSkill(
        hero_id="huatuo",
        short_id="jijiu",
        target_type_id="peach",
        source_zone=_all_cards,
        predicate=_red,
        scopes=(CandidateScope.PLAY,),
        outside_turn_only=True,
        requirement="选择1张红色牌当【桃】使用",
    ),
)
"""H 族四条技能（五条声明：龙胆两个方向各一条）的逐字声明（票 03 §表）。"""


def _modifier(skill: ConversionSkill) -> Modifier:
    """一条转化技能的 ``CANDIDATES`` 修饰器（回调 = 该声明的 :meth:`candidate`）。"""
    return Modifier(ModifierPoint.CANDIDATES, skill.candidate)


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
        grouped.setdefault(skill.skill_id, []).append(skill)
    return tuple(
        SkillDef(
            skill_id=skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=tuple(_modifier(skill) for skill in skills),
            # 龙胆两个方向各一条声明 ⇒ 文案用"；"并起来；虚拟牌名去重后收成元组。
            requirement="；".join(skill.requirement for skill in skills),
            virtual_type_ids=tuple(dict.fromkeys(skill.target_type_id for skill in skills)),
        )
        for skill_id, skills in grouped.items()
    )
