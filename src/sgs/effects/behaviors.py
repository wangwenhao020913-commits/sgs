"""牌行为表：声明式的"用这张牌会发生什么"（票 07 §2）。

步骤只有两类（票 07 §1 的两层切分）：

- :class:`PushFrame` —— 压出一个带时机的结算帧（子帧按约定继承使用帧的
  ``source`` / ``targets`` / ``card``，``scalars`` 只放标量字面量）；
- :class:`CallOp` —— 直接调一个叶子原语，操作名进 :data:`sgs.effects.OPS` 注册表。

**不建** conditions、不建取值表达式：条件内联在回调里（票 07 §4）。
行为表是"使用时机 / 目标规则 / 次数限制"的**唯一真源**，M2 不迁进数据文件；
一致性由加载期的**双向覆盖校验**补偿（票 09 §2 / §7 #9）。

M2 的三处结构变化：

- :class:`TargetRule` 增"目标数来源"枚举（额定 / 全体存活不含自己 / 全体存活含自己）；
  目标池的限制一律走技能侧的候选池修饰器，不给目标规则加面向具体牌名的布尔。
- :class:`CardBehavior` 增 ``final_zone``：结算完实体牌停在哪（默认 ``DISCARD``、
  装备 = ``EQUIP``、延时锦囊 = ``JUDGE``）。基本 53 张用默认值，故其行为**零改动**。
- :class:`CardBehavior` 增 ``response_window``（票 11 §7 / 票 28）：这张牌在自己的
  "生效前"给谁开窗口——【杀】= ``DODGE``（M1 的【闪】窗口一字不动）、锦囊 = ``NULLIFY``
  （收集式无懈窗口）、其余 ``None``。它取代原先"效果里有没有伤害帧"的布尔判据。
- 牌名扩到标准版全表 32 个；M5-01a 再补军争篇 11 个（行为表 43 条）。本票先把**静态面**
  （使用时机 / 目标规则 / 次数限制 / 最终去向区 / 响应窗口）补齐，需询问或多步的**效果**
  由后续票落进 ``effects``（票 26 / 27 / 29 / 30）；军争的属性 / 连环 / 酒 / 装备效果
  分别由 M5-02–06 落地，本文件只放可加载的骨架。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeAlias

from sgs.state import CardRef, FrameKind
from sgs.timing import Timing

__all__ = [
    "CARD_BEHAVIORS",
    "DODGE_RESPONSE_CARD",
    "NULLIFY_RESPONSE_CARD",
    "CallOp",
    "CardBehavior",
    "DamageNature",
    "EffectStep",
    "FinalZone",
    "PushFrame",
    "ResponseWindow",
    "TargetCount",
    "TargetRule",
    "UseContext",
    "UseLimit",
    "UseTiming",
    "behavior_for",
    "satisfies_required",
]


class DamageNature(str, Enum):
    """伤害的**属性**（官方六要素之一）；M1 恒 ``NORMAL``（无属性伤害 / 无连环）。"""

    NORMAL = "NORMAL"
    FIRE = "FIRE"
    THUNDER = "THUNDER"


class UseContext(str, Enum):
    """一张牌可以在哪种语境下被使用（票 07 §2 的 ``use_timing``）。"""

    PLAY_PHASE = "PLAY_PHASE"
    NEAR_DEATH = "NEAR_DEATH"
    RESPONSE = "RESPONSE"


class TargetCount(str, Enum):
    """目标数的**来源**（票 09 §5）；只有三档，不允许出现第四档"自定义"。

    - ``RATED``：额定数值（读 ``rated_targets``），如【杀】1、【顺手牵羊】1；
    - ``ALL_ALIVE``：全体存活角色**不含**使用者，如【南蛮入侵】【万箭齐发】；
    - ``ALL_ALIVE_WITH_SELF``：全体存活角色**含**使用者，如【桃园结义】【五谷丰登】。
    """

    RATED = "RATED"
    ALL_ALIVE = "ALL_ALIVE"
    ALL_ALIVE_WITH_SELF = "ALL_ALIVE_WITH_SELF"


class ResponseWindow(str, Enum):
    """这张牌在自己的"生效前"给**响应者**开哪种窗口（票 11 §7 / 票 28）。

    - ``DODGE``：【杀】——目标用【闪】响应（M1 的 ``DODGE`` 询问与记录形状一字不动）；
    - ``NULLIFY``：锦囊——全体存活按优先级收齐声明、只有最高者实际使用无懈。

    取代原先"效果里有没有伤害帧"的布尔判据：它是**牌面事实**，落在行为表上，
    调用点不再重算。``None`` = 生效前不开任何窗口。
    """

    DODGE = "DODGE"
    NULLIFY = "NULLIFY"


NULLIFY_RESPONSE_CARD = "nullify"
"""无懈窗口里可用的牌名（票 11 §7 的 ``required``）。

它是**牌面事实**：本文件是行为表的唯一真源，故约束值也住在这里，``engine/`` 不出现具体牌名。
"""

DODGE_RESPONSE_CARD = "dodge"
"""【闪】窗口里可用的牌名（B1 的 ``required``）。

与 :data:`NULLIFY_RESPONSE_CARD` 同姿态：它是**牌面事实**，候选生成与抵消判定都读这一处。
两条响应路径（独立的 ``QueryKind.DODGE`` 与 ``RESPOND``）都按**牌名**而不是按询问种类
收口——【无懈可击】的 ``use_timings`` 同样命中"响应 + 生效前"，只按语境取牌就会把它请进
【闪】候选（B1 的根因之一）。
"""


class FinalZone(str, Enum):
    """结算完实体牌的**最终去向区**（票 09 §6）。

    默认 ``DISCARD``；装备牌 = ``EQUIP``（结算完留装备区）；延时锦囊 = ``JUDGE``
    （置入目标的判定区）。取显式字段而非"按类别硬编码"：延时锦囊属 ``trick``
    却要进判定区，硬编码立刻破功。
    """

    DISCARD = "DISCARD"
    EQUIP = "EQUIP"
    JUDGE = "JUDGE"


@dataclass(frozen=True, slots=True)
class UseTiming:
    """一种可用语境；``RESPONSE`` 时带响应窗口的时机 id（【闪】= 生效前）。"""

    context: UseContext
    timing: Timing | None = None


@dataclass(frozen=True, slots=True)
class TargetRule:
    """目标数来源与目标范围（票 07 §4 / 票 09 §5 的判据表）。

    ``self_only`` = 只能指定自己（出牌阶段【桃】、装备牌、延时锦囊【闪电】）；
    ``requires_wounded_self`` = 指定自己的前提是**未满体力**（4.13"满体力时不能回复"）。
    濒死语境下【桃】的目标是濒死者而非自己，由求桃询问直接给出，不走本规则（票 21）。

    ``includes_self`` 只在 ``count is RATED`` 时读；两个 ``ALL_ALIVE*`` 档的
    含不含自己由枚举本身表达，避免同一条事实写两处。

    ``max_distance`` = 这条牌的**额定距离上限**（【顺手牵羊】= 1，与武器无关）；
    ``needs_distance`` 才是"距离 ≤ 攻击范围"（【杀】）。两者可分别成立。
    """

    rated_targets: int = 0
    count: TargetCount = TargetCount.RATED
    includes_self: bool = False
    needs_distance: bool = False
    self_only: bool = False
    requires_wounded_self: bool = False
    near_death_self_only: bool = False
    """**濒死语境**下是否只能对**濒死者自己**使用（M5-04 的【酒】）。

    与 ``self_only`` 分开：``self_only`` 管**出牌阶段**的目标池（【桃】/【酒】都只能对自己），
    而濒死求桃窗口对【桃】允许**他人**对濒死者使用——目标由求桃询问直接给出，不走本规则
    （票 21）。【酒】的濒死用法只允许自救，故需要这条独立的、只影响求桃候选的声明。
    标准 108 的【桃】保持 ``False``，候选面因此一字不变。
    """
    max_distance: int | None = None
    min_targets: int | None = None
    """**目标数区间**的下界（M5-03；``None`` = 不启用区间规则）。

    区间规则与 ``rated_targets`` 的"额定 N 个"是**两种形状**：区间给的是"一次使用可指定
    ``min..max`` 个"（铁索连环 = 1–2），组合由 :func:`sgs.effects.target_combos` 摊开。
    ``min_targets`` 同时就是"**0 目标非法**"的落点（铁索 = 1）。两个都为 ``None`` 时行为
    与 M2 逐字相同（额定 / 全体存活两档），故既有牌型一行不改。
    """

    max_targets: int | None = None
    """**目标数区间**的上界（``None`` = 不设上限，取候选池大小）。"""

    @property
    def is_range(self) -> bool:
        """这条规则是否用**目标数区间**（``min_targets`` / ``max_targets``）表达。"""
        return self.min_targets is not None or self.max_targets is not None

    def includes_self_in_pool(self) -> bool:
        """候选池是否含使用者自己（由目标数来源 + ``includes_self`` 派生）。"""
        if self.count is TargetCount.ALL_ALIVE:
            return False
        if self.count is TargetCount.ALL_ALIVE_WITH_SELF:
            return True
        return self.includes_self

    def target_count(self, pool_size: int) -> int:
        """本牌这一问**至少**要指定几个目标；``ALL_ALIVE*`` 档取整个候选池。

        区间规则返回下界（铁索 = 1）：它同时是"0 目标非法"的读点
        （``steps.use_push_resolve`` 用 ``> 0`` 判"要不要压 RESOLVE"）。
        """
        if self.is_range:
            return self.min_targets or 0
        if self.count is TargetCount.RATED:
            return self.rated_targets
        return pool_size

    def target_range(self, pool_size: int) -> tuple[int, int]:
        """``(下界, 上界)``：区间规则的候选组合由此摊开；其余档退化为"恰好 N 个"。"""
        if self.is_range:
            low = self.min_targets or 0
            high = self.max_targets if self.max_targets is not None else pool_size
            return low, high
        count = self.target_count(pool_size)
        return count, count


@dataclass(frozen=True, slots=True)
class UseLimit:
    """额定 / 额外两个上限；``rated=None`` 表示不限次数（票 07 §6）。

    ``count_key`` 是**本回合次数计数的键**：``None`` = 按牌型计（``card.type``）；
    非空 = 若干牌型共用一个键（M5-02：普通【杀】/ 火【杀】/ 雷【杀】共用 ``"slash"``，
    规则 5.1 + 5.4「作为【杀】使用或打出时作用完全相同」）。键是牌面事实，故住行为表；
    ``engine/`` 只读这个键，不认识具体牌名。
    """

    rated: int | None
    extra: int = 0
    counts: bool = True
    ignore_distance: bool = False
    count_key: str | None = None


@dataclass(frozen=True, slots=True)
class PushFrame:
    """压出一个带时机的子帧；``scalars`` 是标量字面量（如 ``amount=1``）。

    伤害步可以再带一个 ``nature`` 标量声明这次伤害的属性（火杀 = ``DamageNature.FIRE``、
    雷杀 = ``THUNDER``）；不声明即普通伤害。属性是**伤害步的事实**而不是 ``CardRef``
    的字段（ADR-003），故转化 / 虚拟牌按产物牌型读到的是产物行为表里的这一步。
    """

    kind: FrameKind
    scalars: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class CallOp:
    """直接调一个叶子原语；``op`` 必须是 :data:`sgs.effects.OPS` 注册表里的函数名。"""

    op: str
    scalars: tuple[tuple[str, int], ...] = ()


EffectStep: TypeAlias = PushFrame | CallOp


@dataclass(frozen=True, slots=True)
class CardBehavior:
    """一张牌名的完整行为；字段对齐 `开发文档/03` §2「卡牌数据模型」、由票 07 §2 冻结。

    M2 票 27 在静态面上补三样**通用**声明（``TargetRule`` 因此不新增面向具体牌名的
    布尔字段；谓词本身当然可以引用牌名，但只住在 ``effects/``，``engine/`` 一个都不出现）：

    - ``target_filters``：**第 0 层合法性**的附加谓词名（``effects.actions.TARGET_FILTERS``），
      全部为真该候选才进目标集合（【过河拆桥】/【顺手牵羊】的"区域里有牌"、
      【借刀杀人】的"有武器且有合法【杀】目标"）；
    - ``effectiveness``：**第 2 层"无效"**的谓词名（``effects.actions.EFFECTIVENESS``），
      全部为真该目标才在 C1 之后继续结算（【桃园结义】"对未受伤的角色无效"）；
    - ``timing_ops``：绑定在**某个 L4/L5 时机**上的调用型步骤（【五谷丰登】"指定目标后亮牌"
      与"使用结算结束后弃余牌"）。它不是技能钩子，只是"这张牌在这个时机有个动作"。
    """

    card_type_id: str
    use_timings: tuple[UseTiming, ...]
    target_rule: TargetRule
    use_limit: UseLimit
    effects: tuple[EffectStep, ...]
    final_zone: FinalZone = FinalZone.DISCARD
    passes_to_next_judge: bool = False
    """延时锦囊结算完**仍在处理区**时是否顺延到下一个合法目标的判定区（票 11 §4）。

    只有【闪电】为真：它"不命中就传下家"；其余延时锦囊（【乐不思蜀】）直接进弃牌堆。
    是**牌面事实**，故落在行为表而不是引擎的牌名分支。
    """

    target_filters: tuple[str, ...] = ()
    """第 0 层目标合法性的附加谓词名（票 15 §5 的三层模型）。"""

    effectiveness: tuple[str, ...] = ()
    """第 2 层"无效"的谓词名（票 15 §5）：被指定、但到 C1 终止，故不进 C2。"""

    timing_ops: tuple[tuple[Timing, CallOp], ...] = ()
    """绑定在具体时机上的调用型步骤（票 27 为【五谷丰登】的亮牌 / 弃余牌落地）。"""

    response_window: ResponseWindow | None = None
    """"生效前"给响应者开的窗口（票 11 §7 / 票 28）：【杀】= 闪、锦囊 = 无懈、其余无。"""

    reforgeable: bool = False
    """这张牌能否**重铸**（M5-03 / 决策 D6）。

    重铸是牌面声明的一条**独立动作**（不是使用）：出牌阶段里可重铸的牌各给一条
    ``Reforge`` 候选，结算 = 弃置 + 摸一张。目前只有【铁索连环】声明为真。
    """

    preserve_target_order: bool = False
    """这张牌是否**按使用者的选择顺序**结算目标（M5-bugfix）。

    缺省 ``False`` = 引擎的通用排法（从当前回合角色起、逆时针，见
    :func:`~sgs.state.seating.order_targets`）；【铁索连环】声明为真——它的特殊规则是
    “按使用者选择的顺序结算”（spec §5.1.2），而多目标【杀】（【方天画戟】）仍走通用排法。
    这是**牌面事实**，故落在行为表，``engine/`` 不出现牌名分支。
    """

    @property
    def count_key(self) -> str:
        """本回合次数计数的键：行为表声明的共用键，缺省 = 牌型 id（M5-02）。"""
        return self.use_limit.count_key or self.card_type_id

    @property
    def is_delayed(self) -> bool:
        """延时锦囊：使用当时只走 L4，实体牌置入目标判定区，L5 推迟到判定阶段（票 26）。

        谓词取 ``final_zone`` 派生而不是新字段：延时锦囊的判据就是"结算完停判定区"。
        """
        return self.final_zone is FinalZone.JUDGE


_PLAY_PHASE = UseTiming(UseContext.PLAY_PHASE)
_NEAR_DEATH = UseTiming(UseContext.NEAR_DEATH)
_RESPONSE_BEFORE_EFFECT = UseTiming(UseContext.RESPONSE, Timing.RESOLVE_BEFORE_EFFECT)
_UNLIMITED = UseLimit(rated=None)

SLASH_FAMILY_KEY = "slash"
"""【杀】家族**共享的计数键**（M5-02）：普通 / 火 / 雷与转化、虚拟产物都归它。

它同时是"这次使用算不算一张【杀】"的唯一判据（:func:`is_slash`）：`engine/` 只调本
函数决定"酒加伤兑现给哪次使用"，不认识任何具体牌名 / 牌型 id。
"""

WINE_COUNT_KEY = "wine"
"""【酒】的**每回合一次**计数键（M5-04 / spec §5.1 酒）。

它按**游戏回合**结算，而不是按座位自己的回合：出牌阶段的加伤用法记在回合角色身上，
而濒死自救可能发生在**别人的回合**、记在濒死者身上。故回合末要清掉**所有座位**的这个键
（``Engine._turn_after_end``），不能只依赖座位自己的 ``TURN_START`` 重置。键只在这里
定义一次：酒的 ``UseLimit.count_key`` 与引擎的回合末清理都读它，``engine/`` 不写死牌名。
"""


def is_slash(behavior: CardBehavior) -> bool:
    """这次使用是不是一张【杀】——含普通 / 火 / 雷与**转化、虚拟产物**。

    判据 = 行为表的共享计数键（M5-02 把三种【杀】的 ``count_key`` 都声明成 ``"slash"``；
    转化 / 虚拟牌按**产物牌型**查行为表，故同一个键覆盖整个家族）。【酒】的加伤因此
    只认"是不是【杀】"，不认打出去的是哪张实体牌。
    """
    return behavior.count_key == SLASH_FAMILY_KEY


def satisfies_required(card: CardRef, required: str) -> bool:
    """:class:`CardRef` 能不能充当 ``required`` 牌名的响应（**牌名闸门的唯一判据**）。

    ``required`` 的语义是"这次需要一张什么牌"（B1 的 ``_required_hand``、转化候选的
    ``respond_required`` 都读它）。判据默认是**逐字相等的牌名**；唯一放宽的是【杀】族：
    ``required == "slash"`` 时火【杀】/ 雷【杀】/ 转化与虚拟【杀】都算——它们与普通【杀】
    共享 :data:`SLASH_FAMILY_KEY`，故 M5-05b 起直接读 :func:`is_slash`，不写具体牌名。

    **不放宽其它牌名**：``required == "dodge"`` 仍只接受产物牌型 ``dodge``（藤甲的
    "普通【杀】无效"同理是 normal-only 判据，见 ``skills/catalog.py`` 的
    ``_VINE_ARMOR_IMMUNE_CARD_TYPES``）。两个调用点（真牌响应窗口 / 转化候选）因此
    不可能各写一份"什么牌能当什么牌"。
    """
    return card.type == required or (
        required == SLASH_FAMILY_KEY and is_slash(behavior_for(card))
    )


def _one_other(
    card_type_id: str,
    *,
    final_zone: FinalZone = FinalZone.DISCARD,
    effects: tuple[EffectStep, ...] = (),
    target_filters: tuple[str, ...] = (),
    max_distance: int | None = None,
) -> CardBehavior:
    """额定一名他人（【过河拆桥】【顺手牵羊】【决斗】【借刀杀人】【乐不思蜀】）。"""
    return CardBehavior(
        card_type_id=card_type_id,
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(rated_targets=1, max_distance=max_distance),
        use_limit=_UNLIMITED,
        effects=effects,
        final_zone=final_zone,
        target_filters=target_filters,
        response_window=ResponseWindow.NULLIFY,
    )


def _self_only(
    card_type_id: str,
    *,
    final_zone: FinalZone = FinalZone.DISCARD,
    effects: tuple[EffectStep, ...] = (),
    passes_to_next_judge: bool = False,
    response_window: ResponseWindow | None = None,
) -> CardBehavior:
    """只能指定自己（【无中生有】、装备牌、【闪电】）。"""
    return CardBehavior(
        card_type_id=card_type_id,
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(rated_targets=1, self_only=True),
        use_limit=_UNLIMITED,
        effects=effects,
        final_zone=final_zone,
        passes_to_next_judge=passes_to_next_judge,
        response_window=response_window,
    )


def _all_alive(
    card_type_id: str,
    *,
    with_self: bool,
    effects: tuple[EffectStep, ...] = (),
    effectiveness: tuple[str, ...] = (),
    timing_ops: tuple[tuple[Timing, CallOp], ...] = (),
) -> CardBehavior:
    """全体存活（【南蛮入侵】【万箭齐发】不含自己；【桃园结义】【五谷丰登】含自己）。"""
    return CardBehavior(
        card_type_id=card_type_id,
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(
            count=TargetCount.ALL_ALIVE_WITH_SELF if with_self else TargetCount.ALL_ALIVE
        ),
        use_limit=_UNLIMITED,
        effects=effects,
        effectiveness=effectiveness,
        timing_ops=timing_ops,
        response_window=ResponseWindow.NULLIFY,
    )


def _response(
    card_type_id: str, *, response_window: ResponseWindow | None = None
) -> CardBehavior:
    """在别人的结算里响应的牌（【闪】/【无懈可击】）：无座位目标、效果为空。

    两者的差别在**它自己**的"生效前"开不开窗口：【闪】不开（它不可被反抵消），
    【无懈可击】开无懈窗口（无懈可击本身也能被无懈）。
    """
    return CardBehavior(
        card_type_id=card_type_id,
        use_timings=(_RESPONSE_BEFORE_EFFECT,),
        target_rule=TargetRule(rated_targets=0),
        use_limit=_UNLIMITED,
        effects=(),
        response_window=response_window,
    )


def _slash_variant(card_type_id: str, *, nature: DamageNature) -> CardBehavior:
    """火【杀】/ 雷【杀】的静态面与【杀】一字不差，伤害步声明自己的属性（M5-02）。

    D2 把它们建成独立牌型（而不是在 ``CardRef`` 上加属性字段）：属性落在**产物牌型的
    行为表**里，转化 / 虚拟牌因此按产物牌型自动带出属性（朱雀羽扇的普通杀 → 火杀由
    M5-05 产出 ``fire_slash`` 产物即可，不需要另开声明通道）。
    """
    return CardBehavior(
        card_type_id=card_type_id,
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(rated_targets=1, includes_self=False, needs_distance=True),
        use_limit=UseLimit(
            rated=1, extra=0, counts=True, ignore_distance=False, count_key="slash"
        ),
        effects=(
            PushFrame(FrameKind.DAMAGE, (("amount", 1), ("nature", nature))),
        ),
        response_window=ResponseWindow.DODGE,
    )


CARD_BEHAVIORS: dict[str, CardBehavior] = {
    # ---- 基本牌（M1 三条，字段一字不改）----
    "slash": CardBehavior(
        card_type_id="slash",
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(rated_targets=1, includes_self=False, needs_distance=True),
        use_limit=UseLimit(
            rated=1, extra=0, counts=True, ignore_distance=False, count_key="slash"
        ),
        effects=(PushFrame(FrameKind.DAMAGE, (("amount", 1),)),),
        response_window=ResponseWindow.DODGE,
    ),
    "peach": CardBehavior(
        card_type_id="peach",
        use_timings=(_PLAY_PHASE, _NEAR_DEATH),
        target_rule=TargetRule(
            rated_targets=1, includes_self=True, self_only=True, requires_wounded_self=True
        ),
        use_limit=UseLimit(rated=None),
        effects=(PushFrame(FrameKind.RECOVER, (("amount", 1),)),),
    ),
    "dodge": _response("dodge"),
    # ---- 普通锦囊（效果全部由票 27 的 OPS 回调落地）----
    "dismantlement": _one_other(
        "dismantlement",
        effects=(CallOp("resolve_dismantlement"),),
        target_filters=("has_zone_cards",),
    ),
    "snatch": _one_other(
        "snatch",
        effects=(CallOp("resolve_snatch"),),
        target_filters=("has_zone_cards",),
        max_distance=1,
    ),
    "ex_nihilo": _self_only(
        "ex_nihilo",
        effects=(CallOp("resolve_ex_nihilo"),),
        response_window=ResponseWindow.NULLIFY,
    ),
    "duel": _one_other("duel", effects=(CallOp("resolve_duel"),)),
    "borrowed_sword": _one_other(
        "borrowed_sword",
        effects=(CallOp("resolve_borrowed_sword"),),
        target_filters=("borrowed_sword_target",),
    ),
    "barbarian_invasion": _all_alive(
        "barbarian_invasion",
        with_self=False,
        effects=(CallOp("resolve_barbarian_invasion"),),
    ),
    "arrow_barrage": _all_alive(
        "arrow_barrage",
        with_self=False,
        effects=(CallOp("resolve_arrow_barrage"),),
    ),
    "peach_garden": _all_alive(
        "peach_garden",
        with_self=True,
        effects=(CallOp("resolve_peach_garden"),),
        effectiveness=("wounded",),
    ),
    "harvest": _all_alive(
        "harvest",
        with_self=True,
        effects=(CallOp("pick_harvest"),),
        timing_ops=(
            (Timing.USE_AFTER_DESIGNATE_TARGET, CallOp("reveal_harvest")),
            (Timing.RESOLVE_AFTER_END, CallOp("finish_harvest")),
        ),
    ),
    "nullify": _response("nullify", response_window=ResponseWindow.NULLIFY),
    # ---- 延时锦囊（判定骨架在票 26；L5 的判定帧由 OPS 回调压出）----
    "indulgence": _one_other(
        "indulgence",
        final_zone=FinalZone.JUDGE,
        effects=(CallOp("resolve_indulgence"),),
    ),
    "lightning": _self_only(
        "lightning",
        final_zone=FinalZone.JUDGE,
        effects=(CallOp("resolve_lightning"),),
        passes_to_next_judge=True,
        response_window=ResponseWindow.NULLIFY,
    ),
    # ---- 装备（使用面 = 置入装备区；效果由票 29 / 票 30 落地）----
    "crossbow": _self_only("crossbow", final_zone=FinalZone.EQUIP),
    "qinggang_sword": _self_only("qinggang_sword", final_zone=FinalZone.EQUIP),
    "double_sword": _self_only("double_sword", final_zone=FinalZone.EQUIP),
    "green_dragon_blade": _self_only("green_dragon_blade", final_zone=FinalZone.EQUIP),
    "serpent_spear": _self_only("serpent_spear", final_zone=FinalZone.EQUIP),
    "axe": _self_only("axe", final_zone=FinalZone.EQUIP),
    "halberd": _self_only("halberd", final_zone=FinalZone.EQUIP),
    "qilin_bow": _self_only("qilin_bow", final_zone=FinalZone.EQUIP),
    "ice_sword": _self_only("ice_sword", final_zone=FinalZone.EQUIP),
    "eight_diagrams": _self_only("eight_diagrams", final_zone=FinalZone.EQUIP),
    "renwang_shield": _self_only("renwang_shield", final_zone=FinalZone.EQUIP),
    "chitu": _self_only("chitu", final_zone=FinalZone.EQUIP),
    "jueying": _self_only("jueying", final_zone=FinalZone.EQUIP),
    "dilu": _self_only("dilu", final_zone=FinalZone.EQUIP),
    "zhuahuang_feidian": _self_only("zhuahuang_feidian", final_zone=FinalZone.EQUIP),
    "dawan": _self_only("dawan", final_zone=FinalZone.EQUIP),
    "zixing": _self_only("zixing", final_zone=FinalZone.EQUIP),
    # ---- 军争篇（M5-01a 的静态骨架；规则效果由 M5-02–06 落地）----
    # 火杀 / 雷杀：独立牌型，静态面同【杀】；伤害步各自声明 FIRE / THUNDER（M5-02）。
    "fire_slash": _slash_variant("fire_slash", nature=DamageNature.FIRE),
    "thunder_slash": _slash_variant("thunder_slash", nature=DamageNature.THUNDER),
    # 酒：自己 + 出牌阶段 / 濒死；出牌阶段置加伤（M5-04 的 resolve_wine），濒死自救走
    # 求桃窗口的统一回复（与【桃】同一骨架），两种用法共用一个每回合一次的计数键。
    "wine": CardBehavior(
        card_type_id="wine",
        use_timings=(_PLAY_PHASE, _NEAR_DEATH),
        target_rule=TargetRule(
            rated_targets=1,
            includes_self=True,
            self_only=True,
            near_death_self_only=True,
        ),
        use_limit=UseLimit(rated=1, count_key=WINE_COUNT_KEY),
        effects=(CallOp("resolve_wine"),),
    ),
    # 军争三张需询问 / 判定的牌：火攻 / 铁索 / 兵粮。
    # 火攻（M5-06 / D10 / §5.1.4）：目标须**有手牌**（第 0 层 target filter），**可指定自己**；
    # 效果 = 目标亮 1 张手牌 + 使用者可选弃一张同花色手牌 → 1 点火焰伤害（op 名见 OPS）。
    "fire_attack": CardBehavior(
        card_type_id="fire_attack",
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(rated_targets=1, includes_self=True),
        use_limit=_UNLIMITED,
        effects=(CallOp("resolve_fire_attack"),),
        target_filters=("has_hand_cards",),
        response_window=ResponseWindow.NULLIFY,
    ),
    "iron_chain": CardBehavior(
        card_type_id="iron_chain",
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(min_targets=1, max_targets=2, includes_self=True),
        use_limit=_UNLIMITED,
        effects=(CallOp("resolve_iron_chain"),),
        response_window=ResponseWindow.NULLIFY,
        reforgeable=True,
        preserve_target_order=True,
    ),
    "supply_shortage": _one_other(
        "supply_shortage",
        final_zone=FinalZone.JUDGE,
        max_distance=1,
        effects=(CallOp("resolve_supply_shortage"),),
    ),
    # 5 件装备：使用面 = 置入装备区；技能占位住 ``sgs.skills``（键 = ``equip.<id>``），
    # 效果由 M5-05 落地。
    "silver_lion": _self_only("silver_lion", final_zone=FinalZone.EQUIP),
    "vine_armor": _self_only("vine_armor", final_zone=FinalZone.EQUIP),
    "ancient_scimitar": _self_only("ancient_scimitar", final_zone=FinalZone.EQUIP),
    "vermilion_feather_fan": _self_only(
        "vermilion_feather_fan", final_zone=FinalZone.EQUIP
    ),
    "hualiu": _self_only("hualiu", final_zone=FinalZone.EQUIP),
}
"""43 个牌名的行为（标准 32 + 军争 11）；装备牌的**技能**在 ``sgs.skills`` 注册（键 = ``equip.<id>``）。"""


def behavior_for(card: CardRef) -> CardBehavior:
    """按牌名取行为；未知牌名 fail-closed（不静默套用默认行为）。"""
    try:
        return CARD_BEHAVIORS[card.type]
    except KeyError as error:
        raise KeyError(f"牌 {card.name}（{card.type}）没有行为表条目") from error
