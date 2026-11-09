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
from itertools import combinations
from typing import Any

from sgs.data import (
    Category,
    Color,
    SkillType,
    Suit,
    card_category,
    color_of_suit,
    hero_skill_id,
)
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    Modifier,
    ModifierPoint,
    CARD_BEHAVIORS,
    TargetCount,
    TargetRule,
    behavior_for,
    forbidden_as_slash,
    is_slash,
    locate,
    target_sets_match,
)
from sgs.state import (
    ActivateSkill,
    GameState,
    PlayCard,
    QueryKind,
    Reforge,
    SlotKind,
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
from .virtual import card_ids, single_conversion, virtual_candidates

__all__ = [
    "BAZHEN",
    "CONVERSION_SKILLS",
    "conversion_offers",
    "conversion_offers_by_source",
    "conversion_preactivations",
    "conversion_skill_definitions",
    "is_forced_conversion",
    "is_transformed_action",
    "near_death_conversion_offers",
    "owned_conversion_skill_ids",
    "resolve_conversion_confirmation",
    "transform_candidates",
    "wolong_zhugeliang_skill_definitions",
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
    """**手牌**（牌面明写"手牌"时的区域谓词；【倾国】是第一个消费者，§九 L978）。"""
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
    locked: bool = False
    """是否**锁定技**（票 05 的加法；缺省 ``False`` = 既有转化技一字不改）。

    转化技的"发动"本来就不问（它是候选入口），但锁定与否影响「所有非锁定技失效」的谓词
    （【义绝】/【铁骑】）与形态表；神关羽【武神】是首个锁定转化技。

    与 :attr:`forced` **正交**：``locked`` 是"锁定技"这一**规则类型**，``forced`` 是
    "只能 / 可以"这一**效果类型**——别拿一个推另一个。
    """

    forced: bool = False
    """是否**强制转化**（牌面写「**只能**将…当…使用 / 打出」；当前只有神关羽【武神】）。

    "可以"型（缺省 ``False``：武圣 / 龙胆 / 国色 / 急救 / 丈八…）保留源牌的原用途，
    转化只在**预发动**里发生；"只能"型（``True``）的源牌默认就是产物——界面据此把手牌
    画成产物、点它直接进转化清单（``pending.transform_products[*].forced``）。

    **不是** :attr:`locked` 的派生值：锁定技是另一条规则类型（失效 / 形态表读它），
    这里回答的只是"源牌还能不能按原用途用"。原用途的**合法性**仍由该技能自己的
    ``FORBIDDEN`` 修饰器承担（武神同时声明了它与 ``forced=True``），本字段只管默认形态。
    """
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

    界甘宁【奇袭】（§十九 L2145）与标准甘宁【奇袭】（§九 L1025）**逐字相同**，
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
    """颜色谓词：黑色牌（【倾国】§九 L978 / 【奇袭】§九 L1025）。"""
    return _convertible(state, card_id, color=Color.BLACK)


def _spade(state: GameState, card_id: int) -> bool:
    """花色谓词：黑桃牌（林包·移动版董卓【酒池】；`Suit.SPADE == "spade"`）。"""
    return _convertible(state, card_id, suit=Suit.SPADE.value)


def _spade_wine(state: GameState, card_id: int) -> bool:
    """花色谓词 + 产物闸门：黑桃手牌，且本档牌堆里有【酒】实体牌。

    林包·移动版董卓【酒池】「你可以将一张黑桃手牌当【酒】使用」：

    - **黑桃**读 :func:`_spade`（花色是 ``CardRef.suit`` 的既有事实，账本 L775）；
    - **手牌**由声明的 ``source_zone=_hand`` 表达（牌面明写「手牌」⇒ 不套 ``_hand_equip``
      的区域默认，先例 = 【倾国】）；
    - **产物闸门**（账本 L938，用户 2026-11-06 已签）：默认 ``standard_108`` 牌堆里
      一张【酒】都没有（``"wine": 5`` 只在军争 ``standard_ex_160`` 档）⇒ 该档整条不亮，
      否则虚拟牌的牌面名只能回落到 ``wine``（界面上出现英文）。先例 = 界【龙胆】的酒桃
      两向 / 徐晃移动版【断粮】。
    """
    return _spade(state, card_id) and _product_in_pack(state, "wine")


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
    """牌名谓词：【酒】（【龙胆】的【酒】→【桃】，§十九 L2099–L2100）。"""
    return _convertible(state, card_id, card_type="wine") and _product_in_pack(state, "peach")


def _peach_to_wine(state: GameState, card_id: int) -> bool:
    """牌名谓词：【桃】（【龙胆】的【桃】→【酒】；产物牌名不在本档牌堆时不产出）。"""
    return _convertible(state, card_id, card_type="peach") and _product_in_pack(state, "wine")


def _red_fire_attack(state: GameState, card_id: int) -> bool:
    """颜色谓词 + 产物闸门：红色手牌，且本档牌堆里有【火攻】实体牌。

    牌面「一张红色手牌当【火攻】**使用**」——产物牌名不在本档牌堆时（默认
    ``standard_108`` 一张都没有，只有军争 160 档有 3 张）不给该方向：照
    :func:`_product_in_pack` 的既有口径（界【龙胆】酒 / 桃、庞统移动版【连环】的 R5），
    否则会造出一张牌面名回落到 ``fire_attack`` 的虚拟锦囊（用户 2026-11-06 随
    huo-wolong-yanliang 票 01 §7 R3 再签）。
    """
    return _red(state, card_id) and _product_in_pack(state, "fire_attack")


def _black_non_trick(state: GameState, card_id: int) -> bool:
    """颜色 + 大类谓词 + 产物闸门：黑色、**大类不是锦囊**，且本档牌堆里有【兵粮寸断】。

    林包·移动版徐晃【断粮】①「你可以将一张**黑色非锦囊牌**当【兵粮寸断】使用」：

    - **黑色**读 :func:`_black`（颜色派生自花色，唯一真源 ``CardRef.color``）；
    - **非锦囊**读数据文件的牌名大类（:func:`sgs.data.card_category`，与【奇才】/【集智】
      同一个读取方）：三类牌是 基本 / 锦囊 / 装备 ⇒「非锦囊」= 基本 ∪ 装备。**延时锦囊
      一并排除**——延时类不是第四类，它是锦囊大类里 ``final_zone is JUDGE`` 的那几支
      （``CardBehavior.is_delayed``），故这里不需要第二份名单；
    - **产物闸门**（用户 2026-11-08 裁的 Q2 = A，账本 L938）：``standard_108`` 档牌堆里
      一张【兵粮寸断】都没有（它只在军争 ``standard_ex_160`` 的 2 张里）⇒ 该档整条不亮；
      否则虚拟牌的牌面名只能回落到 ``supply_shortage``（:func:`_card_name` 的实体牌回退）。
      先例 = 【火计】/ 界【龙胆】的酒桃两向 / 庞统移动版【连环】的 R5。

    颜色判据先行短路：红牌不必再去查牌名大类。
    """
    if not _black(state, card_id):
        return False
    if card_category(state.pack_id, state.card(card_id).type) is Category.TRICK:
        return False
    return _product_in_pack(state, "supply_shortage")


DUANLIANG_DISTANCE = 2
"""【断粮】② 放宽后的合法距离上限（用户 2026-11-08 裁：距离 2 可用、距离 3 仍不合法）。"""


def _duanliang_distance_limit(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """林包·移动版徐晃【断粮】②：``DISTANCE_LIMIT`` 的**额定距离 1 → 2**（连续型）。

    牌面「你对**距离为2**的其他角色使用【兵粮寸断】无距离限制」——用户 2026-11-08 裁定
    合法目标 = **距离 ≤ 2**（距离 ≥ 3 仍不合法），故回调返回 ``max(current, 2)`` 而
    **不是** ``None``（与【奇才】/【决堰】"完全豁免"那一档不同：那两个返回 ``None``）。

    四道判据（与 :func:`~sgs.skills.limits._qicai_distance_limit` 同姿态）：

    1. ``ctx.owner != ctx.subject`` ⇒ 不是**自己**使用 ⇒ 原样返回（M1 主体侧）；
    2. ``ctx.card is None`` ⇒ 没有渠道牌 ⇒ 原样返回（fail-closed）；
    3. 渠道牌名不是 ``supply_shortage`` ⇒ 原样返回（② 只管【兵粮寸断】）；
    4. ``current is None`` ⇒ 链上已有"完全无距离限制"的效果（【奇才】/【决堰】）⇒
       **保持 ``None``**，不许收窄成 2（M3）。

    渠道牌按**产物牌名**判，故真实【兵粮寸断】与转化出的【兵粮寸断】走同一条链。
    连续型效果每次 ``check_targets`` 现算，不落座位状态、不发 delta
    （``edge-cases.md`` 2.5 的 M2）。
    """
    del state
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if ctx.card.type != "supply_shortage":
        return current
    if current is None:
        return None
    return max(current, DUANLIANG_DISTANCE)


def _diamond_slash_target_pool(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """界关羽【武圣】②：你使用**方块【杀】无距离限制**（§十九 L2084–L2086）。

    落点是 ``TARGET_POOL``（"无距离限制" = 目标池豁免"距离 ≤ 攻击范围"这一条判据）：
    把**全部其他存活座位**并进池子。用"并集"而不是直接返回全集，是为了不与链上**其它**
    目标池修饰器打架——**修饰器管道序** ``(priority_class, skill_id, owner)``（= 解析序，不是
    票 05 的派发链序）里后跑的回调（如
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


def _heart(state: GameState, card_id: int) -> bool:
    """牌名谓词：**红桃**（神关羽【武神】①的源筛选；花色不是颜色）。"""
    return state.card(card_id).suit == Suit.HEART.value


def _heart_slash_target_pool(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """神关羽【武神】②：你使用**红桃【杀】**无距离限制（与界武圣②同形）。

    牌面判据 = ``ctx.card`` 的**花色 + 族**；单源转化继承来源花色，故"红桃手牌当【杀】"
    也命中；族判据走 :func:`~sgs.effects.is_slash`（火 / 雷【杀】同族）。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    if ctx.card.suit != Suit.HEART.value:
        return current
    everyone = tuple(
        player.seat for player in state.players if player.alive and player.seat != ctx.subject
    )
    return tuple(dict.fromkeys((*current, *everyone)))


def _wushen_forbidden(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """神关羽【武神】①：**红桃手牌**不能按原用途使用 / 打出（票 05 的 FORBIDDEN）。

    只禁"这张牌此刻在手牌区、花色红桃、由持有者自己使用 / 打出"；转化【杀】入口不在
    ``check_forbidden`` 的读取面上（它由 CANDIDATES 产出），故同牌仍能当【杀】。重铸 /
    弃置 / 技能成本 / 他人把牌置入其装备区都不经过这个挂点（与【义绝】的读法同姿态）。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if ctx.card.suit != Suit.HEART.value:
        return current
    if ctx.card.card_id not in state.player(ctx.subject).hand:
        return current
    return True


LUANJI = hero_skill_id("yuanshao_mobile", "luanji")
"""袁绍移动版【乱击】的全限定技能 id（huo-mobile-batch 票 03）。"""


def _luanji_candidate(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """袁绍移动版【乱击】：出牌阶段把**两张花色相同的手牌**当【万箭齐发】使用。

    多源转化（与【丈八蛇矛】同一台构造器）：按花色把手牌分组、组内取 C(n,2) 个来源组，
    再交给 :func:`~sgs.skills.virtual.virtual_candidates`——"同花色"这条谓词内联在这里，
    与 H 族"每个技能一个 ``CANDIDATES`` 回调"同一条纪律（候选构造只有一处实现）。

    目标由产品定（【万箭齐发】= 全体其他存活角色），本回调不声明任何目标；```ctx.card``
    非空的那一趟逐张牌循环不产出（整段在第二趟 ``ctx.card=None`` 里补）。
    """
    if ctx.card is not None or ctx.owner != ctx.subject:
        return current
    if ctx.scope is not CandidateScope.PLAY:
        return current
    by_suit: dict[str | None, list[int]] = {}
    for card_id in state.player(ctx.subject).hand:
        by_suit.setdefault(state.card(card_id).suit, []).append(card_id)
    groups = tuple(
        combo for cards in by_suit.values() for combo in combinations(cards, 2)
    )
    if not groups:
        return current
    return (
        *current,
        *virtual_candidates(
            state,
            ctx,
            target_type_id="arrow_barrage",
            sources=groups,
            scopes=(CandidateScope.PLAY,),
        ),
    )


def _club_iron_chain(state: GameState, card_id: int) -> bool:
    """花色谓词 + 产物闸门：**梅花手牌**，且本档牌堆里有【铁索连环】实体牌。

    牌面「一张♣️手牌当【铁索连环】**使用**」——产物牌名不在本档牌堆时（默认
    ``standard_108`` 一张都没有，只有军争 160 档有 6 张）不给"使用"方向：照界【龙胆】
    酒 / 桃的既有口径（:func:`_product_in_pack`），否则会造出一张牌面名回落到
    ``iron_chain`` 的虚拟牌，且"使用一张牌堆外的牌"不是这套数据的语义。
    **重铸方向不受这条闸门影响**（它不产物虚拟牌，见 :func:`_lianhuan_reforge`）。
    """
    return _convertible(state, card_id, suit="club") and _product_in_pack(
        state, "iron_chain"
    )


def _lianhuan_reforge(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """庞统移动版【连环】的**重铸**分支：出牌阶段给每张♣手牌补一条 ``Reforge`` 候选。

    牌面「…当【铁索连环】**使用或重铸**」是**两条互斥的合法动作**（与【铁索连环】自己
    在出牌阶段的候选排列同形），故不加 ``SELECT_OPTION``：使用 = 转化候选（``UseCard``
    虚拟牌），重铸 = 本回调追加的 ``Reforge``。

    - 「当【铁索连环】重铸」= 重铸**那张来源手牌**：虚拟牌从不落区域（``card_id < 0``
      的牌 ``GameState.card`` fail-closed），而重铸的结算本来就是"这张实体牌进弃牌堆 +
      摸一张"，两种读法可观测后果相同（票 02 R2）；
    - 与原生重铸**去重**：牌堆里的♣【铁索连环】本身已经由 ``behavior_for(card).reforgeable``
      产出同一条 ``Reforge``，这里跳过它，不让同一个动作出现两次（票 02 R4）；
    - 只在出牌阶段（``CandidateScope.PLAY``）：账本「重铸 = 独立 ``Reforge`` Action」行的
      "仅出牌阶段"是本技能的第一条"技能授予重铸"载体。
    """
    if ctx.card is not None or ctx.owner != ctx.subject:
        return current
    if ctx.scope is not CandidateScope.PLAY:
        return current
    extra: list[Any] = []
    for card_id in state.player(ctx.subject).hand:
        card = state.card(card_id)
        if card.suit != Suit.CLUB.value:
            continue
        if behavior_for(card).reforgeable:
            continue
        extra.append(Reforge(card=card, answers_seq=ctx.answers_seq))
    return (*current, *extra)


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
    # 界关羽【武圣】（M6 票 04，§十九 L2084–L2086）：①与标准版逐字相同（红牌当【杀】）；
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
    # 神关羽【武神】（票 04 / 票 05；移动版牌面）：锁定转化 + 红桃手牌牌级禁令 + 红桃【杀】
    # 无距离限制。产物按 Q&A 是**普通【杀】**（红桃火杀 / 雷杀也被转成普通杀）。
    ConversionSkill(
        hero_id="shen_guanyu_mobile",
        short_id="wushen",
        target_type_id="slash",
        source_zone=_hand,
        predicate=_heart,
        scopes=(CandidateScope.PLAY, CandidateScope.RESPOND),
        locked=True,
        forced=True,
        modifiers=(
            Modifier(ModifierPoint.TARGET_POOL, _heart_slash_target_pool),
            Modifier(ModifierPoint.FORBIDDEN, _wushen_forbidden),
        ),
        requirement="选择1张红桃手牌当【杀】使用或打出；使用红桃【杀】无距离限制",
    ),
    # 赵云【龙胆】两条方向共用一个技能 id（规则 982）。
    # M6 票 16：界赵云【龙胆】的 杀↔闪 两向与标准版**逐字相同**（§十九 L2098–L2100），
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
    # 界赵云【龙胆】的 酒↔桃 两向（§十九 L2099–L2100）。【酒】当【桃】：出牌阶段可当
    # 【桃】对自己使用（受伤时），濒死求桃窗口由 ``near_death_conversion_offers`` 取
    # （产物【桃】= 濒死窗口的正牌）。【桃】当【酒】：出牌阶段使用（每回合限一次由【酒】的
    # 行为表收住，目标只能是自己）。
    #
    # 02x 路线 A（用户 2026-11-08 签）：``_transformed_peaches`` 泛化后**按
    # ``CandidateScope.PEACH`` 收窄**每一条转化，故"酒→桃"这一向必须显式补上 PEACH
    # （它今天就在濒死窗口产出候选，行为不变）；"桃→酒"那一向**不补**——产物是
    # ``near_death_self_only`` 的【酒】，补上会让它在濒死窗口多亮一条（把一张【桃】当
    # 【酒】用 = 废掉一张【桃】）。
    ConversionSkill(
        hero_id="jie_zhaoyun",
        short_id="longdan",
        target_type_id="peach",
        source_zone=_hand_equip,
        predicate=_wine,
        scopes=(CandidateScope.PLAY, CandidateScope.PEACH),
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
    # 林包·移动版徐晃【断粮】（lin-batch 票 03；牌面权威 = 用户 `技能.txt`，口径 = **移动版**）：
    #
    #     ①你可以将一张黑色非锦囊牌当【兵粮寸断】使用；
    #     ②你对距离为2的其他角色使用【兵粮寸断】无距离限制。
    #
    # 与【国色】同一条 ``ConversionSkill`` 形状（同一张单源转化 → **延时锦囊**），只换三格：
    # 产物 ``supply_shortage`` / 源谓词 ``_black_non_trick``（黑色 ∧ 非锦囊 + 产物闸门）/
    # requirement。**没有【截辎】**：那一条只出现在 OL 现行 / OL 官网 / 一将成名，用户
    # 2026-11-08 裁"移动版、没有截辎"（票面 §0.1 / §0.2）。
    #
    # ② 是**同一张武将牌上的连续型效果**，故与界关羽【武圣】② 同姿态挂在同一条声明的
    # ``modifiers`` 上——挂点选 ``DISTANCE_LIMIT``（额定距离，返回 ``max(current, 2)``）
    # 而不是 ``TARGET_POOL``：兵粮寸断走的是 ``TargetRule.max_distance``，并集写法会绕过
    # 坐骑修正、还要在回调里重算一遍 ``distance()``（同一事实写两份）。
    ConversionSkill(
        hero_id="xuhuang_mobile",
        short_id="duanliang",
        target_type_id="supply_shortage",
        source_zone=_hand_equip,
        predicate=_black_non_trick,
        scopes=(CandidateScope.PLAY,),
        modifiers=(
            Modifier(ModifierPoint.DISTANCE_LIMIT, _duanliang_distance_limit),
        ),
        requirement=(
            "选择1张黑色非锦囊牌当【兵粮寸断】使用；"
            "你对距离为2的其他角色使用【兵粮寸断】无距离限制"
        ),
    ),
    # 华佗【急救】：回合外一张红色牌当【桃】使用（规则 1041）。
    # M6 票 18：界华佗【急救】与标准版**逐字相同**（§十九 L2199）⇒ 同一条声明经
    # ``shared_with`` 派生 ``hero.jie_huatuo.jijiu``，不复制第二行。
    # 02x 路线 A：濒死求桃窗口的转化源改按 ``CandidateScope.PEACH`` 收窄 ⇒ 这里显式补上
    # （急救的濒死候选面行为不变；``PLAY`` 分支照旧，``outside_turn_only`` 仍由
    # ``ConversionSkill.select`` 自校）。
    ConversionSkill(
        hero_id="huatuo",
        short_id="jijiu",
        target_type_id="peach",
        source_zone=_hand_equip,
        predicate=_red,
        scopes=(CandidateScope.PLAY, CandidateScope.PEACH),
        outside_turn_only=True,
        requirement="选择1张红色牌当【桃】使用",
        shared_with=("jie_huatuo",),
    ),
    # 甄姬【倾国】（M6 票 02）：一张**黑色手牌**当【闪】使用或打出（§九 L978）。
    # 区域 = 只有手牌：牌面明写"手牌"，故不套 `_hand_equip` 的区域默认。
    # M6 票 09：界甄姬【倾国】与标准版**逐字相同**（§十九 L2064–L2065）⇒ 同一条声明经
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
    # 甘宁【奇袭】（M6 票 03）：一张黑色牌当【过河拆桥】使用（§九 L1025）。
    # 区域默认 = 手牌 ∪ 装备：牌面没写区域，按 `_hand_equip` 那一档读。
    # M6 票 07：界甘宁【奇袭】牌面逐字相同（§十九 L2145）⇒ 同一条声明经 ``shared_with``
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
    # 火包·移动版庞统【连环】（huo-mobile-batch 票 02）：一张♣手牌当【铁索连环】**使用**
    # 或**重铸**。使用 = 常规单源转化（走与真牌同一条使用链：牌名闸门 / 无懈 / 目标池 /
    # 帷幕 / 奋威 / 集智……）；重铸 = 同一 ``SkillDef`` 上另挂一条 ``CANDIDATES`` 回调
    # 追加 ``Reforge``（CANDIDATES 早已追加过非使用类动作的先例：八卦阵的 ``ActivateSkill``）。
    ConversionSkill(
        hero_id="pangtong_mobile",
        short_id="lianhuan",
        target_type_id="iron_chain",
        source_zone=_hand,
        predicate=_club_iron_chain,
        scopes=(CandidateScope.PLAY,),
        modifiers=(Modifier(ModifierPoint.CANDIDATES, _lianhuan_reforge),),
        requirement="选择1张♣手牌当【铁索连环】使用；或重铸这张手牌",
    ),
    # 火包·OL 卧龙诸葛【火计】（huo-wolong-yanliang 票 01 §2）：出牌阶段，一张红色**手牌**
    # 当【火攻】**使用**。牌面明写「手牌」⇒ 只有手牌（不套 `_hand_equip` 的区域默认，账本
    # 「区域默认读法（W4-3）」）；产物走真【火攻】同一条使用链（目标须有手牌、可被无懈、
    # 亮 1 张 + 可选弃同花色造成 1 点火焰伤害）。
    #
    # **产物牌名不在本档牌堆时不给该方向**（`hero.pangtong_mobile.lianhuan` 的 R5，
    # 用户 2026-11-02 已签）：默认 ``standard_108`` 一张【火攻】都没有 ⇒ 本技能在该档
    # 整条不亮；``standard_ex_160`` 正常可用（用户 2026-11-06 随票 01 §7 R3 再签）。
    ConversionSkill(
        hero_id="wolong_zhugeliang",
        short_id="huoji",
        target_type_id="fire_attack",
        source_zone=_hand,
        predicate=_red_fire_attack,
        scopes=(CandidateScope.PLAY,),
        requirement="选择1张红色手牌当【火攻】使用",
    ),
    # 火包·OL 卧龙诸葛【看破】（huo-wolong-yanliang 票 03 读法 A）：「一张黑色手牌当
    # 【无懈可击】使用」。**没有出牌阶段那一档**：【无懈可击】是响应牌
    # （``rated_targets=0`` + ``use_timings`` 只含生效前），出牌阶段没有合法目标 ⇒ 不产
    # 候选（不发空询问）。唯一语境 = **无懈的收集式窗口**（``CandidateScope.NULLIFY``），
    # 产物是无目标使用、在窗口里只记一条**声明**，收齐后由赢家分支统一使用。
    ConversionSkill(
        hero_id="wolong_zhugeliang",
        short_id="kanpo",
        target_type_id="nullify",
        source_zone=_hand,
        predicate=_black,
        scopes=(CandidateScope.NULLIFY,),
        requirement="选择1张黑色手牌当【无懈可击】使用",
    ),
    # 林包·移动版董卓【酒池】（lin-batch2 票 02；牌面权威 = 用户 `技能.txt`，口径 = **移动版**）：
    #
    #     你可以将一张黑桃手牌当【酒】使用。
    #
    # 形状 = 单源转化（`shapes.md` §2 第 56 行），样板 = 界赵云【龙胆】的「【桃】当【酒】」
    # 方向（同一产物、同一条 ``PLAY`` 分支）+ 徐晃移动版【断粮】的**产物闸门**。三处与样板
    # 不同的都由牌面决定：
    #
    # - **源区 = 手牌**（牌面明写「手牌」⇒ 不套 ``_hand_equip`` 的区域默认，先例【倾国】）；
    # - **源谓词 = 花色**（黑桃）而不是牌名；
    # - **``scopes`` 多一档 ``PEACH``**：濒死自救那一路（02x 路线 A）——泛化后的
    #   ``near_death_conversion_offers`` 按 ``CandidateScope.PEACH`` 收窄每条转化，故这一档
    #   是"允许在求桃窗口出现"的声明（``PLAY`` 分支一字不变；``virtual_card_use`` 没有
    #   ``PEACH`` 分支 ⇒ 不会在出牌阶段的候选里多出一条）。
    ConversionSkill(
        hero_id="dongzhuo_mobile",
        short_id="jiuchi",
        target_type_id="wine",
        source_zone=_hand,
        predicate=_spade_wine,
        scopes=(CandidateScope.PLAY, CandidateScope.PEACH),
        requirement="你可以将一张黑桃手牌当【酒】使用",
    ),
)
"""H 族八条技能（九条声明：龙胆两个方向各一条；界甘宁【奇袭】经 ``shared_with`` 共用
甘宁那一行；火包 OL 卧龙诸葛【火计】【看破】两条；林包·移动版董卓【酒池】一条）的逐字声明
（票 03 §表 + M6 票 02【倾国】/ 票 03【奇袭】/ 票 04 界关羽【武圣】/ 票 07 界甘宁【奇袭】/
huo-wolong-yanliang 票 01 §2·§3 / lin-batch2 票 02 董卓【酒池】）。"""


_LUANJI_DEF = SkillDef(
    skill_id=LUANJI,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    modifiers=(Modifier(ModifierPoint.CANDIDATES, _luanji_candidate),),
    virtual_type_ids=("arrow_barrage",),
    virtual_source_count=2,
    requirement="选择两张花色相同的手牌当【万箭齐发】使用",
)
"""袁绍移动版【乱击】的独立定义（多源转化：两张同花色手牌 → 虚拟【万箭齐发】）。"""


BAZHEN = hero_skill_id("wolong_zhugeliang", "bazhen")
"""火包·OL 卧龙诸葛【八阵】的全限定技能 id。"""


def _bazhen_candidate(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """八阵：装备区里**没有实体防具牌**时，把自己追加进"需要【闪】"的候选。

    形状与 :func:`~sgs.skills.catalog._eight_diagrams_candidate` **逐条同构**（huo-wolong-
    yanliang 票 01 §1 的"读法 Q"）：同一条 ``CANDIDATES`` 回调、同一条引擎通用判定路——

    - ``ctx.scope is DODGE`` ∧ ``ctx.owner == ctx.subject``：只往**需要者自己**的【闪】窗口里
      追加（与真【八卦阵】的两条自校判据相同：跨座位注入是合法用法，本技能不用它）；
    - 条件读**实体**装备区槽位，**不读** ``is_equipped``（用户 2026-11-06 随票 01 §7 R2 签）：
      后者是"实体装备 ∪ 虚拟授予"，而八阵若将来落虚拟装备位就会**自指成真**、条件失效；
      今天 ``is_equipped`` 在 ``src/`` 里零生产读点，故由本判据自己守住这条纪律；
    - 命中后追加一条 ``ActivateSkill(skill_id=ctx.skill_id)``（= 本技能自己的 id）：引擎在
      【闪】窗口见到**未登记提供者**的 ``ActivateSkill`` 就压判定帧（``open_skill_judgment``），
      判红 ⇒ ``skill_result=True`` ⇒ 与真【八卦阵】走**同一条** ``_accept(virtual_dodge(...))``
      使用链。故八阵**不新增**任何"视为使用"机制，也不写座位状态；
    - 连续型（`edge-cases.md` 2.5 的 M2）⇒ **查询期算**：每次开窗现读装备区，"装备区一变
      立刻跟着变"是结构事实，没有事件接缝要同步。
    """
    if ctx.scope is not CandidateScope.DODGE:
        return current
    if ctx.owner != ctx.subject:
        return current
    if any(
        state.card(card_id).slot is SlotKind.ARMOR
        for card_id in state.player(ctx.subject).equip
    ):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def wolong_zhugeliang_skill_definitions() -> tuple[SkillDef, ...]:
    """火包·OL 卧龙诸葛的**非转化**技能定义（当前只有【八阵】一条）。

    【火计】/【看破】是转化技，走 :data:`CONVERSION_SKILLS` 那一份声明（同一张表、同一族
    构造子）；本函数只收"不是转化声明"的那一条，故三条技能仍各一条 ``SkillDef``。

    八阵是**锁定技**（牌面写「锁定技」）⇒ 派发环的 forced 分支不问"是否发动"
    （`shapes.md` §2 的锁定技变体注）；``skill_type = NORMAL``（锁定不是技能类型）；
    ``carrier = HERO`` ⇒ 优先级档由载体派生（武将档）。

    ``requirement`` 取**效果句**「判定为红则视为使用【闪】」而不是牌面全文：它在【闪】窗口里
    与真正的【八卦阵】并排出现，两处文案不同会让玩家以为是两件事（票 01 §1 询问剧本）。
    """
    return (
        SkillDef(
            skill_id=BAZHEN,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.CANDIDATES, _bazhen_candidate),),
            requirement="判定为红则视为使用【闪】",
        ),
    )


_FORCED_CONVERSION_SKILL_IDS: frozenset[str] = frozenset(
    skill_id
    for skill in CONVERSION_SKILLS
    if skill.forced
    for skill_id in skill.skill_ids
)
"""声明为**强制转化**（‘只能’型）的全部技能 id（当前只有 ``hero.shen_guanyu_mobile.wushen``）。

由 :data:`CONVERSION_SKILLS` 的 :attr:`ConversionSkill.forced` 一次派生——不给"哪些技能
默认画产物"留第二份名单。
"""


def _modifier(skill: ConversionSkill) -> Modifier:
    """一条转化技能的 ``CANDIDATES`` 修饰器（回调 = 该声明的 :meth:`candidate`）。"""
    return Modifier(ModifierPoint.CANDIDATES, skill.candidate)


_H_FAMILY_SKILL_IDS: frozenset[str] = frozenset(
    skill_id for skill in CONVERSION_SKILLS for skill_id in skill.skill_ids
)
"""H 族声明表覆盖的技能 id（由 :data:`CONVERSION_SKILLS` 派生，不另抄名单）。

用于把"**不在**声明表里的转化"从单来源归属的兜底里认出来：装备转化技（朱雀羽扇）与
bespoke 回调的武将转化（火包·OL 颜良文丑【双雄】——它的颜色判据读座位状态，
``ConversionSkill.predicate`` 的签名表达不了）都走那条兜底。"""


def _owned_conversion_offers(
    state: GameState,
    seat: int,
    products: Iterable[str],
    *,
    scope: CandidateScope | None = None,
) -> tuple[tuple[int, str, str], ...]:
    """逐条转化声明筛源（:func:`conversion_offers` 与 :func:`near_death_conversion_offers`
    共用的**唯一**内核）：``((来源实体牌 id, 技能 id, 产物牌名), …)``。

    ``products`` = 本次要的产物牌名集合；``scope`` 非空时只收 ``scopes`` 含它的声明
    （02x 路线 A 的 `CandidateScope.PEACH` 收窄，见 :func:`near_death_conversion_offers`）。
    """
    wanted = frozenset(products)
    owned = {
        instance.skill_id
        for instance in resolve_instances(state)
        if instance.owner == seat
    }
    # 【定乱】牌不能作为转化【杀】的源牌（feng-yujin 票 03 / R4=A）：界面归属面与真候选
    # 同一条判据（否则界面画出按钮、引擎却产不出候选）。
    slash_products = {
        product: is_slash(CARD_BEHAVIORS[product]) for product in wanted
    }
    offers: list[tuple[int, str, str]] = []
    for skill in CONVERSION_SKILLS:
        if skill.target_type_id not in wanted:
            continue
        if scope is not None and scope not in skill.scopes:
            continue
        for owned_id in skill.skill_ids:
            if owned_id not in owned:
                continue
            for card_id in skill.select(state, seat):
                if slash_products[skill.target_type_id] and forbidden_as_slash(
                    state, state.card(card_id)
                ):
                    continue
                offers.append((card_id, owned_id, skill.target_type_id))
    return tuple(offers)


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

    **不看 ``scopes``**（本函数是"这个产物牌名能不能被这几张牌转出来"的查询）；
    濒死求【桃】窗口那一路另走 :func:`near_death_conversion_offers`（02x 路线 A）。
    """
    return tuple(
        (card_id, skill_id)
        for card_id, skill_id, _product in _owned_conversion_offers(
            state, seat, (target_type_id,)
        )
    )


_NEAR_DEATH_CONVERSION_PRODUCTS: tuple[str, ...] = tuple(
    dict.fromkeys(
        skill.target_type_id
        for skill in CONVERSION_SKILLS
        if skill.target_type_id == "peach"
        or CARD_BEHAVIORS[skill.target_type_id].target_rule.near_death_self_only
    )
)
"""**濒死语境可用**的转化产物牌名（02x 路线 A 的判据，派生不手抄）。

判据与 :func:`sgs.effects.actions._peach` 的**真牌那一趟**读同一份行为表声明
（``TargetRule.near_death_self_only``；【桃】不声明它，故按牌名单列）：
今天是 ``("peach", "wine")``——【桃】是求桃窗口的正牌，【酒】是"濒死时对自己使用"。
"""


def near_death_conversion_offers(
    state: GameState, seat: int, dying: int
) -> tuple[tuple[int, str, str], ...]:
    """求【桃】窗口的**转化源**候选：``((来源实体牌 id, 技能 id, 产物牌名), …)``。

    02x 路线 A（用户 2026-11-08 签 Q1 = A / Q2 = A2）：判据从写死的 ``"peach"`` 改为
    「产物牌型可在濒死语境使用」（:data:`_NEAR_DEATH_CONVERSION_PRODUCTS`，与真牌那一趟
    同源：``target_rule.near_death_self_only`` 或牌名是 ``peach``）。

    **同时按 ``CandidateScope.PEACH`` 收窄每一条转化**（``scope=`` 参数）：这是泛化后
    最容易漏的一步——界赵云【龙胆】的「【桃】当【酒】」方向声明的是 ``scopes=(PLAY,)``，
    泛化后若不再看 ``scopes``，它会在濒死窗口**多亮一条**候选（把一张【桃】当【酒】用，
    与直接用【桃】等价却废掉一张【桃】）。反向也成立：急救与界【龙胆】的「【酒】当【桃】」
    今天就在濒死窗口出现，故它们的声明**补上了** ``CandidateScope.PEACH``（行为不变）。

    ``near_death_self_only`` 的产物（【酒】）**只在濒死者本人被问时**出现——"濒死时对
    **自己**使用"（规则 §5.5 L605），与 ``_peach`` 的真牌判据同一句；他人的【酒】不当【桃】。
    """
    return tuple(
        (card_id, skill_id, product)
        for card_id, skill_id, product in _owned_conversion_offers(
            state,
            seat,
            _NEAR_DEATH_CONVERSION_PRODUCTS,
            scope=CandidateScope.PEACH,
        )
        if not (
            CARD_BEHAVIORS[product].target_rule.near_death_self_only
            and seat != dying
        )
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


def conversion_skill_ids(state: GameState, owner: int, card: Any) -> tuple[str, ...]:
    """一张**转化牌**归属哪些该座位拥有的转化技（公开名）。

    与清单渲染 / 确认校验**同一份**判据（:func:`_candidate_skill_ids`）：转化候选本身不带
    技能归属（``UseCard`` / ``CardRef`` 是冻结报文），故"这张牌是谁转的"只能由声明回答。
    引擎在"转化候选被**直接**接受"那条路上用它把发动事实记进发动缓冲（lei 票 06）——
    接入层的技能特效通道因此不按卡面 / 技能名自己猜。
    """
    return _candidate_skill_ids(state, owner, card)


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
        # **不在 H 族声明表里**的转化按"产物牌名 + 单来源"归属：装备转化技（M5-05b 的
        # 朱雀羽扇）与 bespoke 回调的武将转化（huo-wolong-yanliang 票 02 的【双雄】——
        # 它的颜色判据读座位状态）。它们的来源谓词内联在 ``CANDIDATES`` 回调里，声明上只有
        # 产物牌名与张数 ⇒ 候选存在本身已证明来源筛过（清单与确认都从这份内部候选池派生）。
        # H 族的技能由上面的 ``offered`` 回答，故这里按声明表扣掉，避免同一条判据两份来源。
        declared = tuple(
            instance.skill_id
            for instance in resolve_instances(state)
            if instance.owner == owner
            and instance.skill_id not in _H_FAMILY_SKILL_IDS
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


def is_forced_conversion(state: GameState, owner: int, card: Any) -> bool:
    """这张虚拟牌候选是不是**强制转化**（‘只能’型，如【武神】）转出来的产物。

    归属仍读 :func:`_candidate_skill_ids`（与清单 / 确认**同一份**判据），再问声明上的
    :attr:`ConversionSkill.forced` —— **不读** ``SkillDef.locked``：锁定技是另一条规则
    类型（失效 / 形态表），"只能 / 可以"是效果类型，二者正交。

    纯虚拟牌（``virtual_sources`` 为空，如八卦阵的视为【闪】）不属于任何转化技 ⇒ ``False``。
    展示层（``sgs_web.projection``）用它给 ``transform_products[*].forced``：手牌区只把
    ``True`` 的产物画成默认牌面；``False`` 的产物仍下发（预发动提示要写「将以【X】使用」）。
    """
    if not getattr(card, "virtual_sources", ()):
        return False
    return any(
        skill_id in _FORCED_CONVERSION_SKILL_IDS
        for skill_id in _candidate_skill_ids(state, owner, card)
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
      去重后的集合，见 :func:`sgs.effects.target_sets_match`）；没有目标步 ⇒ 目标由牌
      本身决定（huo-mobile-batch 票 07 的 :func:`_targets_are_decided_by_the_card`），
      提交里**不该**带目标集，目标改由候选池给出——池是唯一真源，且同一组来源的目标集
      必须**唯一**，否则报「这次转化缺少目标」（过期的那一份不会被替玩家选中）。
      同一条判据也是清单的形状；
    - **点击顺序**（huo-mobile-batch 票 06）：候选牌自己的行为表声明了
      :attr:`~sgs.effects.CardBehavior.preserve_target_order`（当前 =【铁索连环】）时，
      两种点击顺序在候选池里本就是**两条**动作（``_play_phase`` / ``virtual_card_use``
      按 ``target_combos(..., ordered=True)`` 摊开），目标因此按 ``tuple(choices) ==
      tuple(action.targets)`` 的**顺序**匹配——按集合匹配会永远拿到候选池里座位序那条
      （实机报障 ③：产物先点 3 再点 1，结算仍按 (1, 3)）。其余牌保持集合比较（顺序无关），
      判据读行为表，不按技能 id 特判；1 名目标时两种比较等价；
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
    matched: list[Any] = []
    for action in candidates:
        card = getattr(action, "card", None)
        if card is None or not card.virtual_sources:
            continue
        if skill_id not in _candidate_skill_ids(state, owner, card):
            continue
        if tuple(sorted(int(source) for source in card.virtual_sources)) != wanted_sources:
            continue
        source_matched = True
        if needs_target:
            targets = tuple(int(target) for target in getattr(action, "targets", ()))
            # M5-bugfix 的同一判据（huo-mobile-batch 票 06）：声明了
            # ``preserve_target_order`` 的牌把两种点击顺序摊成两条候选，确认必须按
            # **点击顺序**对回那一条；其余牌仍按集合比较（与点击顺序无关）。
            if behavior_for(card).preserve_target_order:
                if tuple(wanted_targets) != targets:
                    continue
            elif not target_sets_match(wanted_targets, targets):
                continue
        matched.append(action)
    if not source_matched:
        return "来源牌不合法：这组牌此刻不能这样转化"
    if not matched:
        return "目标不合法：这组来源没有那条目标"
    if needs_target:
        return matched[0]
    # **没有目标步**（huo-mobile-batch 票 07）：目标由牌本身决定（全体存活 / 只对自己 /
    # 无目标），提交里不含目标 ⇒ 目标集只能取自候选池，而它必须**唯一**：池里同一组来源
    # 出现两条不同目标集（例如一份已经过期）时，替玩家挑一条就是把过期目标放行。
    if len(
        {
            frozenset(int(target) for target in (getattr(action, "targets", None) or ()))
            for action in matched
        }
    ) > 1:
        return "这次转化缺少目标"
    return matched[0]


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
      才下发，座位步在界面上是多选；
    - **目标由牌本身决定时不下发目标步**（huo-mobile-batch 票 07）：本技能这次询问的
      **全部**候选产物的行为表都判为"目标不由玩家选"
      （:func:`_targets_are_decided_by_the_card`：``ALL_ALIVE`` / ``ALL_ALIVE_WITH_SELF``
      / ``self_only`` / 无目标）⇒ 清单只有来源步，确认只提交来源。判据读行为表，不在
      技能 id 上特判；产物不止一种方向（界【龙胆】的【闪】当【杀】与【桃】当【酒】会在
      同一问并存）时，只要有一个方向要玩家选，目标步就保留——那一步表达不了"按方向
      分叉的清单形状"。

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
    target_rules: dict[str, set[TargetRule]] = {}
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
        # 产物行为表（目标数来源是**牌面事实**）：清单要不要目标步由它回答（票 07）。
        rule = behavior_for(card).target_rule
        for skill_id in skill_ids:
            rows = groups.setdefault(skill_id, {}).setdefault(sources, [])
            if targets not in rows:
                rows.append(targets)
            target_rules.setdefault(skill_id, set()).add(rule)
        for source in sources:
            source_zones.setdefault(source, locate(state, source).zone)

    out: dict[str, tuple[Step, ...]] = {}
    for skill_id in sorted(groups):
        steps = _manifest_steps(
            groups[skill_id],
            source_zones,
            target_rules[skill_id],
            fixed_target=fixed_target,
        )
        if steps is not None:
            out[skill_id] = steps
    return out


def _manifest_steps(
    per_source: dict[tuple[int, ...], list[tuple[int, ...]]],
    source_zones: dict[int, Zone],
    target_rules: Iterable[TargetRule],
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
      ``(Bn,)`` 是候选排序的自然首条，故目标步首项 = Bn）。这条判据**优先于**下一条：
      窗口里出现两份不同目标集时池已不自明，即使行为表判得出"目标由牌决定"也照旧下发
      目标步——否则确认只能报「这次转化缺少目标」；
    - **目标由牌本身决定时不下发目标步**（huo-mobile-batch 票 07）：``target_rules`` 是
      本技能这次询问全部候选产物的 ``TargetRule``——**每一个**都判为"目标不由玩家选"
      （:func:`_targets_are_decided_by_the_card`）⇒ 清单只有来源步。只要有一个产物
      方向要玩家选目标（界【龙胆】的【闪】当【杀】与【桃】当【酒】可在同一问并存），
      目标步照旧下发——一条步骤表达不了"按方向分叉"的清单；
    - **合法来源组合**（huo-mobile-batch 票 10）：多来源步（``count > 1``）的来源步带出
      ``Step.groups`` = ``per_source`` 的键（"哪几张能配成一组"），且只在**存在非法组合**
      时才下发（任意 ``count`` 张都能配 ⇒ 省略，见 :func:`_source_groups`）。判据只读候选池
      ——不看技能 id / 牌名，界面也不必自己判花色。
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
            kind="card",
            zones=tuple(zone for zone in _SOURCE_ZONE_ORDER if zone in zones_used),
            min=count,
            max=count,
            pool=(StepPool(when=(), options=options),),
            order=0,
            groups=_source_groups(per_source, count, options),
        )
    ]
    combos = [targets for rows in per_source.values() for targets in rows]
    if not combos or all(not targets for targets in combos):
        return tuple(steps)
    # 窗口承载目标的判据**优先**（票 07 之后的次序修正）：``fixed_target`` 语境只认窗口里
    # 的目标集是否唯一。不唯一时池已不自明，**不**走"目标由牌决定"的省步分支——否则确认
    # 只能报「这次转化缺少目标」，而目标步本来可以让玩家选（票 07 之前的形状）。
    if fixed_target:
        if _targets_are_truly_unique(combos):
            return tuple(steps)
    elif all(_targets_are_decided_by_the_card(rule) for rule in target_rules):
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
            kind="seat",
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


def _source_groups(
    per_source: dict[tuple[int, ...], list[tuple[int, ...]]],
    count: int,
    options: tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    """来源步的**合法来源组合**（huo-mobile-batch 票 10；``Step.groups`` 的唯一写方）。

    判据只读**候选池**：``per_source`` 的键就是引擎认的"能一起提交"的来源组（多源转化按
    谓词分组时，键 = 组内那几张实体牌），这里原样转成文本 id（与 ``StepPool.options`` 同一
    口径），故"同花色的一对"这类规则**不在界面 / 不按技能 id 特判**。

    两条收窄：

    - ``count <= 1`` ⇒ 空：单来源步没有"组合"可言（缺省 = 全组合合法，旧载荷逐字段不变）；
    - 任意 ``count`` 张都能配（案例 = 【丈八蛇矛】的"任意两张"，``per_source`` 里 C(n,2) 个
      键全在）⇒ 空：下发一串"每一对都在里面"的数据没有信息量，界面退回旧行为。

    ``options`` 是全组合的参照集：它的 ``count`` 元子集 = "这些候选里所有可能的搭配"。
    """
    if count <= 1:
        return ()
    legal = {frozenset(sources) for sources in per_source}
    everything = {
        frozenset(combo)
        for combo in combinations(
            tuple(int(option) for option in options), count
        )
    }
    if legal == everything:
        return ()
    return tuple(tuple(str(source) for source in sources) for sources in per_source)


def _targets_are_decided_by_the_card(target_rule: TargetRule) -> bool:
    """这张牌的产物是否**由牌本身决定目标集**（清单因此没有目标步，票 07）。

    判据只读行为表（:attr:`~sgs.effects.CardBehavior.target_rule` 的
    ``TargetCount`` 档），**不在技能 id 上特判**：

    - ``ALL_ALIVE`` / ``ALL_ALIVE_WITH_SELF``：目标是按座位规则算出来的全体存活角色
      （含 / 不含使用者），玩家没有可选的第二步（【万箭齐发】/【桃园结义】）；
    - ``self_only``：产物只能指向自己（【酒】/出牌阶段的【桃】），同样没有第二步；
    - ``rated_targets == 0``：这张牌本来就没有目标（【闪】/【无懈可击】）。

    区间目标（【铁索连环】1–2 名）与额定 ≥1 名（【杀】/【顺手牵羊】1 名）仍要玩家选，
    返回 ``False``——它们的目标步照旧留在清单里。
    """
    if target_rule.is_range:
        return False
    if target_rule.count is not TargetCount.RATED:
        return True
    return target_rule.self_only or target_rule.rated_targets == 0


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

    锁定与否**按声明**（``ConversionSkill.locked``；缺省 False = 既有转化技不变）：
    转化入口本身不问，但锁定技不受「所有非锁定技失效」影响；``skill_type = NORMAL``；
    ``carrier = HERO`` ⇒ 优先级档由载体派生（武将档先于装备档）。
    """
    grouped: dict[str, list[ConversionSkill]] = {}
    for skill in CONVERSION_SKILLS:
        for skill_id in skill.skill_ids:
            grouped.setdefault(skill_id, []).append(skill)
    definitions = tuple(
        SkillDef(
            skill_id=skill_id,
            locked=skills[0].locked,
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
    # 多源转化是**配对谓词**（"两张花色相同"），单源声明的 `predicate` 表达不了 ⇒ 另立
    # 一条独立定义（与【丈八蛇矛】同一台 `virtual_candidates` 构造器）。
    return (*definitions, _LUANJI_DEF)
