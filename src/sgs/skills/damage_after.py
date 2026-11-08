r"""伤害后触发（B 族，票 05）：逐点触发的判读、三条技能的载体与回调。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` / :mod:`sgs.skills.target_pool` /
:mod:`sgs.skills.move_after` 同构：一条 :class:`~sgs.skills.catalog.SkillDef` = 一个
``hero.<武将 id>.<短 id>`` 技能，定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是
**唯一入口**。

B 族的形态差别只有一处：它挂在 ``DAMAGE_AFTER_TAKE`` / ``DAMAGE_AFTER_DEAL`` 上，而
**这两个时机在同一次伤害里可能派发多轮**——引擎按伤害值逐轮派发，轮次写进帧私有
:data:`DAMAGE_POINT_KEY`（票 05 §要做什么 1；票 15 把逐点扩到造成侧）。故"每次伤害事件
一次"与"受到 / 造成 1 点伤害一次"是**技能定义上的一个可选字段**
（:attr:`~sgs.skills.catalog.Hook.per_point`），不是引擎里的特判：

=========================  ======  =========  ==========================================
技能                       锁定    per_point  效果
=========================  ======  =========  ==========================================
曹操【奸雄】               非锁定  ``False``  获得**造成此伤害的牌**（在处理区里找）
界曹操【奸雄】             非锁定  ``False``  同上，**并摸一张牌**（§十九 L2029）
司马懿【反馈】             非锁定  ``False``  获得**伤害来源**的一张牌（手牌隐藏槽 + 装备可见槽）
界司马懿【反馈】           非锁定  ``True``   同上，逐点触发（"受到 **1 点**伤害后"，§十九 L2036）
郭嘉【遗计】               非锁定  ``True``   观看牌堆顶 2 张 → 交给任意角色（可一次两张）
界郭嘉【遗计】             非锁定  ``True``   摸两张 → 从**手牌**里分配给至多两名其他角色
夏侯惇【刚烈】             非锁定  ``False``  判定 → 非红桃时**伤害来源**二选一：弃两张手牌 / 受 1 点伤害
界夏侯惇【刚烈】           非锁定  ``True``   判定 → 红：对来源 1 点伤害 / 黑：弃来源一张牌
界李典【忘隙】             非锁定  ``True``   造成 / 受到 1 点伤害后摸两张，交给该角色其中一张
界华雄【耀武】             锁定    ``False``  受到**牌造成的**伤害时：红色来源摸一张，否则自己摸一张
魏延【狂骨】               锁定    ``True``   造成 1 点伤害后，若**扣减体力前**你计算与其的距离 ≤ 1，回复 1 点体力
=========================  ======  =========  ==========================================

除两条**锁定技**（界【耀武】、魏延【狂骨】）外都是**非锁定技**（技能面写了"你可以"）⇒
发动是询问出来的（锁定技不问，派发环的 forced 分支直接跑回调）。它们在回调里可以
**提问**（【反馈】选牌、【遗计】看牌与交给、【刚烈】判定后问来源），返回 ``"stay"``
让出控制权；引擎按"同一轮重入不重问、轮次推进"的帧内游标范式继续（票 19 §8）。
【遗计】的面是**观看**（票 15）：牌面只在**持有者本人**的那次询问里（询问的
``revealed`` 授予），不进任何 ``PlayerView``、不落带牌面的 delta；同一个伤害点内
**一次可以选两张一起交给同一名角色**，也可以一张一交、每次换人。界【遗计】的"分配"
与【忘隙】的"交给该角色"走同一条既有给牌路径（``SELECT_CARDS`` / ``SELECT_OPTION``
询问 + ``PUT`` 进对方手牌），不新造私有 Action。

**"获得造成伤害的牌"**读 ``frame.data["card"]``（= 官方六要素的**渠道牌**，与
``channel`` 同源）经 :func:`~sgs.effects.locate` 定位：牌在处理区里才拿得到；已被
【寒冰剑】类效果移走（或已进弃牌堆）⇒ **找不到就跳过**，不抛错。

**B 族第四形态由 M6 票 01 落地**（R6-Q3 当时登记为"缺席"）：【刚烈】是"判定帧 +
``ask_option`` + 条件伤害"的组合，判定结果要读**花色**而不是布尔（红桃终止，方块是红色
但继续），故引擎的 :meth:`~sgs.engine.Engine.note_skill_judgment` 从本票起额外回写
:data:`~sgs.skills.SKILL_JUDGMENT_CARD_KEY`。选项与"弃哪两张"都问**伤害来源**
（``actor`` = 来源），不是技能持有者——这与同族三条"问自己"相反，是本形态的关键差别。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sgs.data import Color, SkillType, Suit, color_of_suit, hero_skill_id
from sgs.effects import (
    CarrierKind,
    DamageNature,
    behavior_for,
    check_targets,
    distance,
    is_slash,
    locate,
)
from sgs.state import (
    CardRef,
    FrameKind,
    GameState,
    MoveReason,
    SelectionSpec,
    Slot,
    SlotKind,
    Zone,
    ZoneRef,
    alive_seats,
    seats_from,
)
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    SKILL_JUDGMENT_CARD_KEY,
    HeroSkillDecl,
    Hook,
    SkillDef,
)
from .flow import (
    FlowAnswers,
    PickCards,
    PickSeats,
    SkillFlow,
    option_seat,
    seat_choice,
    zero_step_effect,
)
from .virtual import pure_virtual_card

__all__ = [
    "DAMAGE_AFTER_SKILLS",
    "JIE_GANGLIE",
    "JIE_KUANGGU_MOBILE",
    "JIE_KUANGGU_MOBILE_DEF",
    "JIE_JIANXIONG",
    "JIE_JIANXIONG_FLOW",
    "JIE_LIYU",
    "JIE_QIAOMENG",
    "JIE_YAOWU",
    "JIE_YIJI",
    "JIE_YIJI_DRAW",
    "JIE_YIJI_GIVE_LIMIT",
    "WANGXI",
    "DAMAGE_POINT_KEY",
    "DAMAGE_RECORDED_KEY",
    "DAMAGE_DEAL_POINT_KEY",
    "DAMAGE_DEAL_RECORDED_KEY",
    "DamageAfterSkill",
    "FANKUI_FLOW",
    "FANGZHU",
    "FANGZHU_FLOW",
    "FANGZHU_REQUIREMENT",
    "GANGLIE_OPTIONS",
    "JIANXIONG_FLOW",
    "damage_after_skill_definitions",
    "damage_point_index",
    "fankui_step_key",
    "SHIBEI",
    "SHIBEI_DEF",
]

DAMAGE_POINT_KEY = "damage_point_index"
"""帧私有槽名：**本次伤害的第几点**（0 起）。

引擎在 ``DAMAGE_AFTER_TAKE`` 的每一轮写它（票 05 §要做什么 1），技能回调与
:func:`~sgs.skills.catalog._matching_hooks` 的"逐点"过滤都读它。帧私有数据不进记录流，
故这一步不动任何既有基准。
"""

DAMAGE_RECORDED_KEY = "damage_recorded"
"""帧私有槽名：**同一次伤害事件的时机记录是否已经落过**（票 05）。

同一次伤害事件只落一条 ``DAMAGE_AFTER_TAKE`` 记录（M2 逐字节不变，``amount=3`` 的
【闪电】也只有一条），逐点只体现为"这一条记录内多轮派发"。第 1 轮写它，第 2 轮起据此
把"这一轮只派发、不再记"传给派发口（``emit_record=False``）。
"""

DAMAGE_DEAL_POINT_KEY = "damage_deal_point_index"
"""帧私有槽名：**造成伤害后**那一侧逐点派发到第几点（M6 票 15）。

``DAMAGE_AFTER_TAKE`` 的逐点游标（:data:`DAMAGE_POINT_KEY`）在 ``damage_after_take``
里从 0 起跑；``DAMAGE_AFTER_DEAL`` 在它**之前**执行（``DAMAGE_STEPS`` 的顺序），故
造成侧的游标另存一个键，跑完再把 :data:`DAMAGE_POINT_KEY` 交还 0。技能回调不读它，
只有 :func:`~sgs.engine.steps.damage_after_deal` 的循环用它续跑。
"""

DAMAGE_DEAL_RECORDED_KEY = "damage_deal_recorded"
"""帧私有槽名：**造成伤害后**的时机记录是否已经落过（M6 票 15）。

与 :data:`DAMAGE_RECORDED_KEY` 同一条纪律（同一次伤害事件恰好一条时机记录），只是分属
两个时机——共用一个键会让 ``DAMAGE_AFTER_TAKE`` 在 ``DAMAGE_AFTER_DEAL`` 之后误以为
自己已经记过。
"""


def damage_point_index(frame: Any) -> int:
    """当前伤害点序号；不在 ``DAMAGE_AFTER_TAKE`` / ``DAMAGE_AFTER_DEAL`` 派发中时是 ``0``。"""
    return frame.data.get(DAMAGE_POINT_KEY, 0)


# ---------------------------------------------------------------------------
# 三个可读判据（条件与回调共用同一处，避免"问不问"与"做了没做"各写一份）
# ---------------------------------------------------------------------------


def _damage_channel_card(frame: Any) -> CardRef | None:
    """造成这次伤害的**实体牌**（官方六要素的渠道牌）；无牌来源时是 ``None``。"""
    return frame.data.get("card")


def _in_processing(state: GameState, card: CardRef | None) -> bool:
    """``card`` 此刻是否**还在处理区**（【奸雄】拿得到它的唯一前提）。

    "找不到那张牌"（已被【寒冰剑】类效果移走 / 已进弃牌堆）一律算"不在处理区"：
    技能**跳过**而不是抛错（票 05 §验收 3）。
    """
    if card is None or card.is_virtual:
        return False
    try:
        return locate(state, card.card_id) == ZoneRef(Zone.PROCESSING)
    except ValueError:
        # ``locate`` 对"不在任何区域"的牌 fail-closed；这里把那个错误读成"拿不到"。
        return False


def _fankui_source(state: GameState, frame: Any, owner: int | None) -> int | None:
    """【反馈】的选取对象：**造成伤害的角色**；无来源 / 是自己 / 无牌可拿 ⇒ ``None``。

    无来源伤害（【闪电】一类）不进候选；"来源自己"也不进——技能面写的是"获得**伤害来源**
    的一张牌"，自己不是伤害来源。``owner`` 由调用方给：条件体传
    :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（持有者自校，票 13），效果体传
    ``skill_owner``（此时已写好）。``owner`` 为 ``None`` 的兜底分支仍按"来源有牌"判，
    但条件体那侧已由 :func:`_hurt_owner` 短路，走不到它。
    """
    source = frame.data.get("source")
    if source is None or source == owner:
        return None
    player = state.player(source)
    return source if player.hand or player.equip else None


def fankui_step_key(frame: Any) -> str:
    """【反馈】选牌步的**帧内缓存键**：``fankui:<点号>``。

    点号是必须的（票 07 §5 的"逐字节等价"要求）：同一次伤害事件会按伤害值**逐轮派发**，
    而 ``ask_selection`` 的答案按帧内键缓存 —— 键若只认步骤名（``"card"``），第 2 点会
    命中第 1 点的缓存答案、**只搬一张牌**。迁移前的键是 ``fankui:<来源>:<点号>``，
    来源在一条流水线里是常量，故这里只补点号；而点号的真源
    :data:`DAMAGE_POINT_KEY` 就住本模块（``flow.py`` 反向不得 import 本模块）。
    """
    return f"fankui:{damage_point_index(frame)}"


# ---------------------------------------------------------------------------
# 逐技能落位（票 05 §要做什么 2 的表）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DamageAfterSkill(HeroSkillDecl):
    """B 族一条技能的**声明**：触发条件 + 回调（票 05 §要做什么 2 一行）。

    ``requirement``（M6 票 08 的加法，缺省 ``None``）：覆盖 :data:`_REQUIREMENTS` 的
    短 id 文案——标准 / 界两版【刚烈】的牌面是整句重写，短 id 相同而"需要做什么"不同。
    ``step_requirements``（M6 票 15 的加法，缺省 ``None``）：逐步文案的覆盖；与
    ``requirement`` 同一条理由——标准 / 界【遗计】共用一个短 id ``yiji`` 而分配步的
    问法不同（标准看牌堆顶、界版从手牌里挑），故声明侧给界版一份自己的表。
    ``timings``（M6 票 15 的加法，缺省空 = 只挂 ``DAMAGE_AFTER_TAKE`` + ``condition``）：
    这条技能挂在哪些伤害后时机上、各用什么触发条件。【忘隙】是首个**双向**载体（造成 /
    受到各一条钩子）：两侧的"我是谁"判据相反，故条件**逐时机**声明，而不是一条条件
    兼顾两侧（后者会让同一次伤害的两个时机都命中同一个持有者）。
    """

    per_point: bool
    condition: Any
    handler: Any = None
    flow: Any = None
    requirement: str | None = None
    step_requirements: dict[str, str] | None = None
    timings: tuple[tuple[Timing, Any], ...] = ()
    locked: bool = False
    """是否锁定技（M6 票 18 的加法；缺省 ``False`` = 既有七条一字不改）。

    【耀武】是 B 族第一条**锁定技**（§十九 L2218–L2219）：没有"是否发动"询问，派发环
    按 ``locked`` 直接跑回调。此前 B 族全是"你可以"（非锁定），故这个字段由本票引入，
    不改变任何既有技能的声明形状。"""


def _jianxiong_condition(host: Any, frame: Any) -> bool:
    """【奸雄】的触发条件：**受伤的是持有者自己**，且造成伤害的那张牌**还在处理区**。

    持有者判据是必须的：派发环会给每个存活玩家的实例求条件（见 :func:`_hurt_owner`）。
    """
    return _hurt_owner(frame) and _in_processing(host.state, _damage_channel_card(frame))


def _gain_channel_card(host: Any, frame: Any, owner: int) -> Any:
    """把**造成这次伤害的牌**从处理区搬进 ``owner`` 手牌；返回移动的 ``Control``。

    【奸雄】标准版与界版的**共用体**：两版的差别只在界版多摸一张（见
    :func:`_jie_jianxiong`），"拿哪张牌、从哪搬、什么理由"逐字相同。返回
    :meth:`~sgs.engine.Engine.move_in_frame` 的控制值（``"stay"`` = 搬牌挂起了），
    由调用方决定要不要等它——标准版早先忽略了这个返回（搬牌内联派发"失去牌后"时
    会静默少续跑一次），抽这一层时一并交给调用方处理。
    """
    card = _damage_channel_card(frame)
    if not _in_processing(host.state, card):
        return None
    assert card is not None  # _in_processing 已保证
    return host.move_in_frame(
        frame,
        (card,),
        to_zone=ZoneRef(Zone.HAND, owner),
        reason=MoveReason.PUT,
        owner=owner,
        actor=owner,
    )


def _jianxiong(host: Any, frame: Any) -> Any:
    """曹操【奸雄】：你可以获得造成此伤害的牌（规则 972）。"""
    return _gain_channel_card(host, frame, frame.data["skill_owner"])


JIANXIONG_FLOW = SkillFlow(name="jianxiong", steps=(), effect=zero_step_effect(_jianxiong))
"""【奸雄】的零步流水线（W6-01）：[是否发动] → 获得造成伤害的牌（无步骤）。

与 :data:`FANKUI_FLOW` 同一形态：**模块级具名常量**，与 Hook 同住本模块
（``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例）。
"""


JIE_JIANXIONG = "hero.jie_caocao.jianxiong"
"""界曹操【奸雄】的全限定技能 id（§十九 L2028–L2029）。"""

_JIE_JIANXIONG_MOVED_KEY = "jie_jianxiong_moved"
"""帧私有：造成伤害的牌已经搬进手牌（搬牌挂起后的续跑不重搬）。"""

_JIE_JIANXIONG_DRAWN_KEY = "jie_jianxiong_drawn"
"""帧私有：界【奸雄】"并摸一张牌"的那一张已经摸过（续跑不重摸）。"""


def _jie_jianxiong_condition(host: Any, frame: Any) -> bool:
    """界【奸雄】的触发条件：**受伤的是持有者自己**（不要求渠道牌还在处理区）。

    牌面是"获得对你造成伤害的牌**并摸一张牌**"（§十九 L2029）：两段效果并列，"摸一张牌"不
    以"获得了牌"为前提。伤害来自**技能**（没有渠道牌，如【刚烈】/【黩武】）时，第一段无从
    执行、第二段照常——故条件只剩"我受伤"这一条（用户裁定，票 23；与 `参考资料/技能描述
    歧义核查.md` B-15 的结论一致）。
    """
    del host
    return _hurt_owner(frame)


def _jie_jianxiong(host: Any, frame: Any) -> Any:
    """界曹操【奸雄】：获得造成此伤害的牌（若有）**并摸一张牌**（§十九 L2029）。

    与标准版共用 :func:`_gain_channel_card`，但**渠道牌不必存在**：没有牌时跳过第一段、
    只摸那一张（:func:`_jie_jianxiong_condition` 已把"牌在处理区"这条前提去掉）。
    两段各一个帧私有游标：搬牌 / 摸牌都可能内联派发而挂起，续跑必须既不重做也不漏做
    （与界【集智】/界【刚烈】同一范式）。
    """
    owner = frame.data["skill_owner"]
    if not frame.data.get(_JIE_JIANXIONG_MOVED_KEY):
        frame.data[_JIE_JIANXIONG_MOVED_KEY] = True
        control = _gain_channel_card(host, frame, owner)
        if control == "stay":
            return "stay"
    if not frame.data.get(_JIE_JIANXIONG_DRAWN_KEY):
        frame.data[_JIE_JIANXIONG_DRAWN_KEY] = True
        host.draw_for_hand(frame, owner, 1)
    return None


JIE_JIANXIONG_FLOW = SkillFlow(
    name="jie_jianxiong", steps=(), effect=zero_step_effect(_jie_jianxiong)
)
"""界【奸雄】的零步流水线：[是否发动] → 获得造成伤害的牌 → 摸一张牌。"""


def _fankui_condition(host: Any, frame: Any) -> bool:
    """【反馈】的触发条件：**受伤的是持有者自己**（规则"当**你**受到伤害后"），
    且伤害来源存在、不是自己、且**有牌可拿**。

    持有者判据 :func:`_hurt_owner` 与其他伤害后技能同源，且是**必须的**：``condition`` 在
    派发器写 ``frame.data["skill_owner"]`` **之前**求值，故持有者只能从派发器在求值前写好的
    :data:`~sgs.skills.catalog.MATCH_OWNER_KEY` 读。漏掉它，条件就退化成"来源有牌"，
    派发环遍历**每个存活玩家**的实例 ⇒ 任何人挨打、司马懿都被问，答"发动"后真去拿来源的牌
    （票 13；用户实机报障）。
    """
    return (
        _hurt_owner(frame)
        and _fankui_source(host.state, frame, frame.data.get(MATCH_OWNER_KEY)) is not None
    )


def _fankui_source_seat(state: Any, frame: Any, owner: int, answers: Any) -> Any:
    """**步 1 的选取面**：有牌可拿时是"伤害来源那一个座位"，否则**空元组** = 这一步不存在。

    这是"派生参数读帧"的落点（票 07 §3）：``frame`` 给出伤害来源，``owner`` 是技能持有者
    （派发环显式传 ``instance.owner``）。

    返回空元组（而不是一个座位号）时，执行器的"**退化面不询问**"判据接管：这一步**一个询问
    都不发**、整条流水线按取消收尾（与迁移前 :func:`_fankui` 的提前返回同形）。

    票 13 之前，这一档是**必需的**：``_fankui_condition`` 在条件求值期读不到持有者，
    判据退化成"来源有牌"，于是"来源自己"那个实例也会过闸、也会被问"是否发动"，只能由这里
    挡掉后续的选牌询问。票 13 让条件读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY` 自校后，
    过闸的实例已经只剩"受伤的持有者"一个；这一档作为**独立的第二道闸**原样保留——与
    :func:`_fankui_effect` 的复核同姿态，也继续兜住直接调用（不经派发环）的情形。
    """
    del state, answers
    source = _fankui_source_from_frame(frame)
    if source is None or source == owner:
        return ()
    return source


def _fankui_source_from_frame(frame: Any) -> int | None:
    """帧里的伤害来源座位；没有来源时 ``None``。"""
    source = frame.data.get("source")
    return None if source is None else int(source)


def _fankui_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：把选中的那张牌从**伤害来源**的区搬进持有者手牌（``PUT``）。

    **复核一次"来源不是自己"**（与迁移前的 :func:`_fankui` 逐字同形）：上面的空池已经
    挡掉了"来源是自己"的实例，但效果是**独立的第二道闸**——守卫住"无论怎么走到这里，
    都不会把自己手里的牌搬给自己"，不依赖步骤声明这一个前提。

    搬牌可能内联派发"失去牌后"并挂起，故重入时必须只搬一次 —— 由
    :meth:`Engine.move_in_frame` 的重入守卫保证（`CODING_STANDARDS.md` 规则 3）。
    """
    owner = frame.data["skill_owner"]
    if _fankui_source(host.state, frame, owner) is None:
        return None
    return host.move_in_frame(
        frame,
        host.state.refs(answers["card"]),
        to_zone=ZoneRef(Zone.HAND, owner),
        reason=MoveReason.PUT,
        owner=owner,
        actor=owner,
    )


FANKUI_FLOW = SkillFlow(
    name="fankui",
    steps=(
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            # **有意保留现状**（票 07 §5）：迁移前的选牌步没有 ``Pass``，"取消"的唯一出口
            # 是发动询问那一道。改成 ``True`` 会改记录流与既有录像。
            optional=False,
            # 帧内**缓存键**带点号：同一次伤害事件会按伤害值逐轮派发，键若只认步骤名，
            # 第 2 点就命中第 1 点的缓存答案（见 :func:`fankui_step_key`）。效果读答案
            # 仍按 ``key="card"``，不受逐点影响。
            cache_key=fankui_step_key,
            # ``face``（铺谁的区）在这里指回**伤害来源**，而 ``actor``（谁应答）保持缺省
            # = 发动者 = 派发环传进来的 ``instance.owner``。两者在【反馈】里再次分开：
            # 持有者选牌、选的是**来源**的区。
            face=_fankui_source_seat,
            # 来源的手牌是**隐藏槽**（只有位置、没有牌面），与迁移前的
            # ``_fankui_face`` 逐字同形；装备区照旧可见。
            reveal_hand=False,
        ),
    ),
    effect=_fankui_effect,
)
"""【反馈】的一步流水线（票 07 §5）：选伤害来源的一张牌（手牌隐藏槽 + 装备可见槽）→ 获得。

选取面走引擎的 :meth:`Engine.selection_face`（``reveal_hand=False``）：它与迁移前手写的
``_fankui_face`` 铺的是同一个东西（HAND 逐张打乱隐藏、EQUIP 可见），**随机数消耗次序也
相同**，故既有 Golden Replay 一行不改。
"""


def _hurt_owner(frame: Any) -> bool:
    """**这次伤害打在技能持有者身上吗**。

    伤害后技能的共同前提（官方【遗计】【奸雄】【反馈】开头都是"当**你**受到伤害后"）。
    判据读派发器在条件求值期写好的 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`
    （同 `move_after.py` 的口径）——``skill_owner`` 要到回调前才写，条件里读不到。

    **为什么必须自校**：``_dispatch`` 遍历**每个存活玩家**的实例，只按
    ``timing`` / ``per_point`` / ``condition`` 过滤。"条件恒真"因此等于"任何人挨打都触发"，
    而【遗计】的回调会把牌堆顶的牌**移动给持有者**——别人挨打、郭嘉白拿牌。
    """
    return frame.data.get(MATCH_OWNER_KEY) == frame.data.get("target")


def _yiji_condition(host: Any, frame: Any) -> bool:
    """【遗计】逐点触发：**持有者**每受一点伤害各成立一次。

    逐点由帧私有 ``damage_point_index`` 驱动（牌堆重洗由 ``peek_deck`` 沿用同一套口径）。
    """
    del host
    return _hurt_owner(frame)


def _yiji_keys(point: int) -> tuple[str, str]:
    """【遗计】在**第 ``point`` 点伤害**上的两个帧私有槽名：本点亮出的牌 / 本点已交出的牌 id。

    "逐点触发"意味着同一次伤害里有多轮独立的【遗计】；槽名带点号，两轮的观看与进度
    才不会互相顶掉（否则第 2 点会读到第 1 点剩的那张）。
    """
    return (f"{YIJI_SEEN_KEY}:{point}", f"{YIJI_GIVEN_KEY}:{point}")


_ASK_YIJI_PICK = "hero.guojia.yiji.pick"
"""手写询问位置（票 22）：【遗计】从亮牌里选牌的那次 ``ask_selection``（轮次由 ``key``
的 ``:<point>:<round>`` 改**询问名**）。"""


def _yiji(host: Any, frame: Any) -> Any:
    """郭嘉【遗计】：观看牌堆顶 2 张，然后交给任意角色（规则 993；逐点触发）。

    **只看不排**：走票 10 的授予通道（``peek_deck`` + 询问的 ``revealed``）——观看的
    牌面只出现在这次询问里，不进任何 ``PlayerView``、不落带牌面的 delta。用户口径
    逐字（票 15 / `acceptance.md` §C）：不是"摸两张再从手牌区交出"，也不是"展示牌堆顶
    两张"，而是**观看**，且**仅持有者可见**。

    **一次触发内分配**（票 05 修"两张只发得出一张"，票 15 加"一次两张"）：官方原文是
    "观看牌堆顶的两张牌，然后**将这些牌**交给任意角色"——是复数的"这些牌"，不是一张。
    故本回调在**同一个伤害点**内循环：

        while 还有没交出的亮牌：
            选牌（``SELECT_CARDS``，面里只铺**剩余**亮牌，``total=(1, 2)``、**不可放弃**）
            → 选座次（``SELECT_OPTION``，选项 ``seat:N`` = 任意存活角色，**不可放弃**）
            → 把这批牌一起移进收牌人的手牌

    选一张 = 现有的一轮一张路径（两张分两轮、每轮可换人）；选两张 = **一次交出两张**
    给同一名角色。两条路径共用下面同一段"选座 → 移动"，只是 ``picked`` 的长度不同。

    旧实现把"下一张"交给**下一个伤害点**驱动：1 点伤害只有一轮 ⇒ 第二张永远发不出去；
    2 点伤害则第二轮**重新观看牌堆顶**（第一张已被移走）⇒ 两张亮牌被换成另一对。

    **两步都不可放弃**（U2-12 / spec D2 + D9.1）：官方原文是"可以将这些牌交给任意角色"，
    是"交给谁"的选择，不是"可以不交"——技能一旦发动就必然把两张都分配出去。旧实现在选牌
    步给 ``optional=True``，``Pass`` = 剩下的亮牌留在牌堆顶；本轮按 D9.1 删掉它（超时兜底改
    由接入层"把剩下的牌交给自己"，见 ``sgs_web.timeout.fallback_action``）。进度落在**帧
    私有键**里（点号 + 已交牌 id），故"挂起 → 应答 → 续跑"重入本回调时既不复述也不重发
    询问；移动本身由 ``move_in_frame`` 的重入守卫保证只发生一次（`CODING_STANDARDS.md`
    规则 3）。
    """
    owner = frame.data["skill_owner"]
    point = damage_point_index(frame)
    seen_key, given_key = _yiji_keys(point)
    seen: tuple[CardRef, ...] | None = frame.data.get(seen_key)
    if seen is None:
        seen = host.peek_deck(frame, YIJI_COUNT)
        frame.data[seen_key] = seen
        frame.data[given_key] = ()
    if not seen:
        return None
    given: tuple[int, ...] = tuple(frame.data.get(given_key, ()))
    remaining = tuple(card for card in seen if card.card_id not in given)
    if not remaining:
        # 本点已经看完并分配完：**保留帧私有键**，重入直接作罢。
        # 这一点很关键：同一个伤害点的派发可能因**另一个钩子**（同族技能 / 移动后）挂起而
        # 被重入；键若被清掉，重入就会重新观看牌堆顶、把已经处理过的两张再问一遍。
        return None
    round_index = len(given)
    # 票 15：**一次可选两张**（用户口径"一次给出这两张牌时需要允许同时选择两张牌"）。
    # ``total=(1, 2)`` ⇒ 面内同时铺 1 张与 2 张两种候选：选 2 张 = 一次交同一名角色；
    # 选 1 张 = 现有的一轮一张路径（两张分两轮、每轮可换人）。两条路径共用下面同一段
    # "选座 → 移动"，故"分两次给"的行为一字不改。
    # U2-12（spec D9.1）：**不可放弃**——``optional=False``，候选面里不再有 ``Pass``。
    # "你可以"只到技能发动询问为止（那一问的 ``optional`` 仍为真）；发动之后必然要分配。
    spec = SelectionSpec(zones=(Zone.HAND,), total=(1, 2), optional=False)
    picked = host.ask_selection(
        frame,
        owner=_ASK_YIJI_PICK,
        key=f"yiji:pick:{point}:{round_index}",
        actor=owner,
        timing=Timing.DAMAGE_AFTER_TAKE,
        spec=spec,
        build_face=lambda: (
            tuple(Slot(ref=ZoneRef(Zone.HAND, owner), card=item) for item in remaining),
            tuple(item.card_id for item in remaining),
        ),
        revealed=remaining,
    )
    if host.suspended(picked):
        return "stay"
    if not picked:
        # 防御分支：``optional=False`` 时引擎不会给出空答案（候选面里没有 ``Pass``）；
        # 宿主级直接调回调的测试若硬塞空元组，按"本点收尾"处理而不是死循环。
        return None
    # ---- 交给谁（官方："将这些牌**交给任意角色**"）--------------------------------
    # 用 ``SELECT_OPTION`` 承载：它的 ``options`` 是**开放闭集**（任意字符串元组），
    # 不需要新造 QueryKind，界面上也已经是一条一个按钮、点得动的形态。
    # ``skill_id`` 带**点号 + 轮号**——``ask_option`` 的答案缓存键是
    # ``("option", skill_id, actor)``，同一点内的下一张若同名就会把第 2 轮读成第 1 轮的答案。
    choice = host.ask_option(
        frame,
        owner,
        Timing.DAMAGE_AFTER_TAKE,
        skill_id=f"yiji:give:{point}:{round_index}",
        options=tuple(f"seat:{seat}" for seat in alive_seats(host.state)),
    )
    if host.suspended(choice):
        return "stay"
    receiver = int(choice.choice.split(":", 1)[1])
    # 进度**先落、再搬牌**：``move_in_frame`` 返回 ``"stay"`` 的意思是「牌**已经搬完**了，
    # 只是『失去牌后』的钩子挂起了」（它的重入守卫 = ``_MOVE_DONE`` + "牌已在目标区"）。
    # 旧实现等到搬完才记进度，重入时于是又搬一遍——而「失去牌后」的钩子（界【清俭】）可能
    # 已经把其中一张转交给了别人，第二次 ``move_card`` 的两张来源区因此不同 ⇒
    # ``ValueError: 一次 move_card 只能有一个来源区域``（wuming 票 01 实测：郭嘉 + 界夏侯惇
    # 同场；回归 = ``test_yiji_does_not_move_the_same_cards_again_when_the_receiver_relocates_one``）。
    # ``ask_selection`` 返回的是**实体牌 id**（不是 ``CardRef``）；一次两张时两条 id
    # **一起**记进 ``given``——进度按"这一批已经交给谁"推进，重入不会把其中一张再发一遍。
    frame.data[given_key] = given + tuple(picked)
    control = host.move_in_frame(
        frame,
        host.state.refs(picked),
        to_zone=ZoneRef(Zone.HAND, receiver),
        reason=MoveReason.PUT,
        owner=receiver,
        actor=owner,
    )
    if control == "stay":
        return "stay"
    return _yiji(host, frame)


YIJI_COUNT = 2
"""【遗计】"观看牌堆顶"的张数（规则 993：两张）。"""

YIJI_SEEN_KEY = "yiji_seen"
"""帧私有槽名前缀：本点伤害里【遗计】已经观看过的那批牌（槽名再带点号）。"""

YIJI_GIVEN_KEY = "yiji_given"
"""帧私有槽名前缀：本点伤害里【遗计】已经交出去的那批牌 id（槽名再带点号）。"""


# ---------------------------------------------------------------------------
# 界郭嘉【遗计】（M6 票 15）：摸两张，然后把至多两张**手牌**交给至多两名其他角色
# ---------------------------------------------------------------------------

JIE_YIJI = "hero.jie_guojia.yiji"
"""界郭嘉【遗计】的全限定技能 id（§十九 L2058–L2060）。"""

JIE_YIJI_DRAW = 2
"""界【遗计】"摸两张牌"的张数（与标准版观看的张数无关，是**两种**效果）。"""

JIE_YIJI_GIVE_LIMIT = 2
"""「至多两名其他角色共计至多两张手牌」的两个上限（角色数 = 牌数 = 2）。

逐张给、每张各选一名其他角色 ⇒ 两个上限同时成立：最多两轮，每轮至多一名角色。
"""

JIE_YIJI_DRAWN_KEY = "jie_yiji_drawn"
"""帧私有槽名前缀：本点伤害里界【遗计】的"摸两张"已经做过（槽名再带点号）。"""

JIE_YIJI_GIVEN_KEY = "jie_yiji_given"
"""帧私有槽名前缀：本点伤害里界【遗计】已经交出的 ``(牌 id, 收牌座位)``（槽名再带点号）。"""

_ASK_JIE_YIJI_PICK = "hero.jie_guojia.yiji.pick"
"""手写询问位置（票 22）：界【遗计】从**自己手牌**里挑要交出去的牌。"""


def _jie_yiji_other_seats(state: GameState, owner: int) -> tuple[int, ...]:
    """界【遗计】的收牌人池："**其他**角色" ⇒ 存活、且不是持有者本人（座位序）。"""
    return tuple(player.seat for player in state.players if player.alive and player.seat != owner)


def _jie_yiji(host: Any, frame: Any) -> Any:
    """界郭嘉【遗计】：受到 1 点伤害后摸两张，然后可不分配（§十九 L2058–L2060）。

    与标准【遗计】是**两种**效果：标准版看牌堆顶两张、交给任意角色（含自己）；界版先
    **摸两张**，再从**自己的手牌**里选至多两张交给至多两名**其他**角色。逐点触发
    （"受到 1 点伤害后"）与标准版同一条 :func:`_yiji_condition`。

    分配走**既有的给牌路径**（票面验收）：``SELECT_CARDS`` 挑牌 → ``SELECT_OPTION``
    选人 → :meth:`~sgs.engine.Engine.move_in_frame`（``PUT`` 进收牌人手牌），与标准
    【遗计】的"交给谁"同一条；不新造私有 Action。

    逐轮推进：每轮一张，最多 :data:`JIE_YIJI_GIVE_LIMIT` 轮；选牌步 ``optional=True``
    是"至多"的出口（答 ``Pass`` = 不再交）。进度落帧私有键（带点号），故挂起 → 应答 →
    续跑既不重摸、也不重问已答过的轮次。
    """
    owner = frame.data["skill_owner"]
    point = damage_point_index(frame)
    drawn_key = f"{JIE_YIJI_DRAWN_KEY}:{point}"
    given_key = f"{JIE_YIJI_GIVEN_KEY}:{point}"
    if not frame.data.get(drawn_key):
        frame.data[drawn_key] = True
        # 摸牌也算「获得牌」（票 24 的 Q1）：摸出来的两张可能触发别人的【清俭】并挂起
        # （票 25 的嵌套挂起）。挂起时**原样上抛**，否则后面的选牌 / 选人询问会覆盖那条
        # 待答询问，答复随后错配给另一个问句。
        if host.draw_for_hand(frame, owner, JIE_YIJI_DRAW) == "stay":
            return "stay"
    given: tuple[tuple[int, int], ...] = tuple(frame.data.get(given_key, ()))
    if len(given) >= JIE_YIJI_GIVE_LIMIT:
        return None
    others = _jie_yiji_other_seats(host.state, owner)
    if not others:
        return None
    round_index = len(given)
    picked = host.ask_selection(
        frame,
        owner=_ASK_JIE_YIJI_PICK,
        key=f"jie_yiji:pick:{point}:{round_index}",
        actor=owner,
        timing=Timing.DAMAGE_AFTER_TAKE,
        # "至多两张"：每轮一张、最多两轮；这一轮不交就答 ``Pass``。
        spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1), optional=True),
        # 选的是**自己的**手牌 ⇒ 可见槽（票 19 §3 的口径）。
        build_face=lambda: host.selection_face(owner, (Zone.HAND,), reveal_hand=True),
    )
    if host.suspended(picked):
        return "stay"
    if not picked:
        return None
    choice = host.ask_option(
        frame,
        owner,
        Timing.DAMAGE_AFTER_TAKE,
        # 点号 + 轮号进询问名：``ask_option`` 的答案缓存键是 ``(skill_id, actor)``，同名会
        # 把第 2 轮读成第 1 轮的答案（标准【遗计】的既有口径）。短 id 仍是 ``yiji``，
        # 界面按"给谁"这一步取文案。
        skill_id=f"yiji:give:{point}:{round_index}",
        options=tuple(f"seat:{seat}" for seat in others),
    )
    if host.suspended(choice):
        return "stay"
    receiver = int(choice.choice.split(":", 1)[1])
    control = host.move_in_frame(
        frame,
        host.state.refs(picked),
        to_zone=ZoneRef(Zone.HAND, receiver),
        reason=MoveReason.PUT,
        owner=receiver,
        actor=owner,
    )
    if control == "stay":
        return "stay"
    frame.data[given_key] = given + ((picked[0], receiver),)
    return _jie_yiji(host, frame)


# ---------------------------------------------------------------------------
# 界李典【忘隙】（M6 票 15）：造成 / 受到 1 点伤害后摸两张并交给该角色其中一张
# ---------------------------------------------------------------------------

WANGXI = "hero.jie_lidian.wangxi"
"""界李典【忘隙】的全限定技能 id（§十九 L2071–L2073）。"""

WANGXI_DRAW = 2
"""【忘隙】"摸两张牌"的张数。"""

WANGXI_DRAWN_KEY = "wangxi_drawn"
"""帧私有槽名前缀：本次触发摸到的两张牌（槽名再带方向与点号）。"""

_ASK_WANGXI_GIVE = "hero.jie_lidian.wangxi.give"
"""手写询问位置（票 22）：【忘隙】从**刚摸到的两张**里挑一张交出去。"""


def _wangxi_other(
    state: GameState, frame: Any, owner: int | None, *, side: str
) -> int | None:
    """【忘隙】的"该角色"：**另一个**当事角色；这一侧不成立 ⇒ ``None``。

    ``side`` 决定"我是谁"：

    - ``"deal"``（``DAMAGE_AFTER_DEAL``）：持有者必须是**来源**，"该角色" = 受伤者；
    - ``"take"``（``DAMAGE_AFTER_TAKE``）：持有者必须是受伤者，"该角色" = 来源。

    "其他角色"排除自己；无来源（【闪电】）与来源已阵亡都不触发（给不出牌）。与
    :func:`_fankui_source` 的存活 / 非自己判据同姿态。**两侧的判据必须分开**：一条
    "我是双向之一"的判据会让同一次伤害的两个时机都命中同一个人（票 15 构造测试逮到过）。
    """
    if owner is None:
        return None
    if side == "deal":
        if frame.data.get("source") is None or int(frame.data["source"]) != int(owner):
            return None
        other = frame.data.get("target")
    else:
        if frame.data.get("target") is None or int(frame.data["target"]) != int(owner):
            return None
        other = frame.data.get("source")
    if other is None or int(other) == int(owner):
        return None
    other = int(other)
    return other if state.player(other).alive else None


def _wangxi_deal_condition(host: Any, frame: Any) -> bool:
    """【忘隙】造成侧：持有者是伤害来源，且受伤者是另一个存活角色。"""
    return (
        _wangxi_other(host.state, frame, frame.data.get(MATCH_OWNER_KEY), side="deal")
        is not None
    )


def _wangxi_take_condition(host: Any, frame: Any) -> bool:
    """【忘隙】受到侧：持有者是受伤者，且来源是另一个存活角色。"""
    return (
        _wangxi_other(host.state, frame, frame.data.get(MATCH_OWNER_KEY), side="take")
        is not None
    )


def _wangxi_side(frame: Any, owner: int) -> tuple[str, Timing]:
    """这次触发来自哪一侧：``("deal", DAMAGE_AFTER_DEAL)`` / ``("take", ..._TAKE)``。"""
    if frame.data.get("source") is not None and int(frame.data["source"]) == int(owner):
        return "deal", Timing.DAMAGE_AFTER_DEAL
    return "take", Timing.DAMAGE_AFTER_TAKE


def _wangxi_face(host: Any, owner: int, shown: tuple[int, ...]):
    """【忘隙】选牌步的面：**只有刚摸到的两张**（官方"交给该角色**其中一张**牌"）。

    自铺面（不调 ``selection_face``）：那张面会铺开整个手牌区，而这里只允许从"这两张"里
    挑一张。槽的坐标仍是 ``(HAND, owner)``，故移动 / 投影与其它选牌步同形。
    """
    cards = tuple(host.state.card(card_id) for card_id in shown)
    return (
        tuple(Slot(ref=ZoneRef(Zone.HAND, owner), card=card) for card in cards),
        tuple(card.card_id for card in cards),
    )


def _wangxi(host: Any, frame: Any) -> Any:
    """界李典【忘隙】：摸两张，把**其中一张**交给该角色（§十九 L2071–L2073）。

    "造成 1 点伤害后"与"受到 1 点伤害后"共用这一份回调：方向由 :func:`_wangxi_side`
    从帧里读（持有者 = 来源 ⇒ deal，否则 take），"该角色"由 :func:`_wangxi_other` 给出。
    摸到的两张**当场定格**在帧私有键里（``before`` 手牌集合的差集），故重入不重摸、
    选牌面也只铺这两张（不是整手牌）。

    交给谁**不问**：牌面写死是"该角色"（造成伤害的对手 / 伤害来源）。选中的那张走既有
    给牌路径（``PUT`` 进该角色手牌），与【遗计】的分配同一条。
    """
    owner = frame.data["skill_owner"]
    side, timing = _wangxi_side(frame, owner)
    other = _wangxi_other(host.state, frame, owner, side=side)
    if other is None:
        return None
    point = damage_point_index(frame)
    drawn_key = f"{WANGXI_DRAWN_KEY}:{side}:{point}"
    shown: tuple[int, ...] | None = frame.data.get(drawn_key)
    if shown is None:
        before = tuple(host.state.player(owner).hand)
        # 摸牌也可能挂起（票 25 的嵌套挂起 + 票 24 的 Q1）：先把"摸到了哪两张"定格，
        # 再原样上抛 ``"stay"``——重入靠 ``shown is not None`` 跳过摸牌，不能在这里漏算。
        control = host.draw_for_hand(frame, owner, WANGXI_DRAW)
        after = tuple(host.state.player(owner).hand)
        shown = tuple(card_id for card_id in after if card_id not in before)
        frame.data[drawn_key] = shown
        if control == "stay":
            return "stay"
    if not shown:
        return None
    picked = host.ask_selection(
        frame,
        owner=_ASK_WANGXI_GIVE,
        key=f"wangxi:give:{side}:{point}",
        actor=owner,
        timing=timing,
        spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1), optional=False),
        build_face=lambda: _wangxi_face(host, owner, shown),
    )
    if host.suspended(picked):
        return "stay"
    if not picked:
        return None
    control = host.move_in_frame(
        frame,
        host.state.refs(picked),
        to_zone=ZoneRef(Zone.HAND, other),
        reason=MoveReason.PUT,
        owner=other,
        actor=owner,
    )
    return "stay" if control == "stay" else None


# ---------------------------------------------------------------------------
# 界吕布【利驭】（M6 票 18）：【杀】造成伤害后获得其区域内一张牌；非装备则其摸一张，
# 装备则视为你对该角色指定的另一名其他角色使用【决斗】
#
# §十九 L2208–L2209：
#
#     利驭：当你使用【杀】对其他角色造成伤害后，你可以获得其区域内的一张牌，若你获得的
#           牌不为装备牌，则其摸一张牌，否则视为你对该角色指定的另一名其他角色使用一张
#           【决斗】。
#
# 两条裁定（票面 `## 已知歧义`，写进 `06`（§5「程序化约定与偏离一手」））：
#
# - **【决斗】目标由受伤者指定**（"该角色指定的"）——决定者 ≠ 技能持有者，与
#   【护驾】②的"决定者 = 事件当事角色"同一姿态；座位询问发给受伤者；
# - **"区域内的一张牌"** 按 `GLOSSARY.md`「区域默认」= 手牌 + 装备 + 判定区；手牌对
#   选择者（吕布）是**隐藏槽**（与【反馈】同一条选取面）。
# ---------------------------------------------------------------------------

JIE_LIYU = hero_skill_id("jie_lvbu", "liyu")
"""界吕布【利驭】的全限定技能 id（§十九 L2208–L2209）。"""


def _slash_damage_target(state: GameState, frame: Any, owner: int) -> int | None:
    """**我**用【杀】对**另一个**角色造成伤害时的受伤者；不是这个形状 ⇒ ``None``。

    【利驭】与【趫猛】共用的前半句（"当你使用【杀】对一名角色造成伤害后"）：判据只有
    "伤害的来源是我"、"渠道牌是【杀】家族"、"受伤者是别人"三条，区域 / 后续分支各自再判。
    """
    del state
    if frame.data.get("source") is None or int(frame.data["source"]) != int(owner):
        return None
    target = frame.data.get("target")
    if target is None or int(target) == int(owner):
        return None
    card = _damage_channel_card(frame)
    if card is None or not is_slash(behavior_for(card)):
        return None
    return int(target)


LIYU_DUEL_TARGET_ASK = "hero.jie_lvbu.liyu.duel_target"
"""手写询问位置：受伤者指定另一名其他角色（虚拟【决斗】的目标）。"""

_LIYU_STARTED = "liyu_started"
"""帧私有：虚拟【决斗】已经开出（重入不重复开）。"""

_LIYU_MATCHED = "liyu_matched"
"""帧私有：本帧上【利驭】已经命中过（条件求值的重入标记）。

**为什么需要**：条件在每次重入都重新求值，而【利驭】的前半段会把受伤者的牌拿走——
"其区域里有牌可拿"在搬牌之后不再成立。没有这个标记，选牌 / 指定决斗目标的中途挂起
（重新进入同一个钩子）会被条件挡回去，整条流水线静默丢失（本票构造测试逮到过）。
标记只记"这条钩子在本帧已经过闸"，重入时不再重判"还有没有牌"。
"""


def _liyu_target(state: GameState, frame: Any, owner: int, answers: Any) -> Any:
    """选牌步的 ``face``：受伤者的区域；不是"造成伤害的是我"或没有牌 ⇒ 空元组 = 无此步。"""
    del answers
    target = _slash_damage_target(state, frame, owner)
    if target is None:
        return ()
    # 命中之后（``_LIYU_MATCHED`` 已落）不再重判"还有没有牌"：选牌步的**面**在每次重入
    # 都重新求值，而前半段会把那张牌拿走——不认这个标记，重入会看到空面、把整条流水线
    # 判成取消（构造测试逮到过：决斗目标还没问就静默结束）。
    if frame.data.get(_LIYU_MATCHED) == owner:
        return target
    player = state.player(target)
    if not (player.hand or player.equip or player.judge):
        return ()
    return target


def _liyu_condition(host: Any, frame: Any) -> bool:
    """【利驭】的触发条件：**我用【杀】对另一个角色造成伤害**，且其区域里有牌可拿。

    命中后落 :data:`_LIYU_MATCHED`：条件在每次重入都重新求值，而前半段会把受伤者的牌
    拿走 ⇒ 没有这个标记，中途挂起后重入会被"还有没有牌"挡回去。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    if frame.data.get(_LIYU_MATCHED) == owner:
        return True
    if _liyu_target(host.state, frame, owner, None) == ():
        return False
    frame.data[_LIYU_MATCHED] = owner
    return True


def _liyu_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：获得那张牌 → 非装备则其摸一张；装备则受伤者指人，吕布对其使用【决斗】。"""
    owner = frame.data["skill_owner"]
    target = frame.data.get("target")
    card_id = int(answers["card"][0])
    card = host.state.card(card_id)
    control = host.move_in_frame(
        frame,
        (card,),
        to_zone=ZoneRef(Zone.HAND, owner),
        reason=MoveReason.PUT,
        owner=owner,
        actor=owner,
    )
    if control == "stay":
        return "stay"
    if card.slot is None:
        # 不为装备牌 ⇒ 受伤者摸一张（"其" = 受伤者）。这一摸可能触发【清俭】并挂起
        # （票 24：摸牌算获得），挂起必须上抛。
        if target is not None and host.draw_for_hand(frame, int(target), 1) == "stay":
            return "stay"
        return None
    if frame.data.get(_LIYU_STARTED):
        return None
    if target is None:
        return None
    target = int(target)
    duel = pure_virtual_card(host.state, "duel")
    options = tuple(
        seat_choice(seat)
        for seat in check_targets(host.state, owner, duel)
        if seat not in (owner, target)
    )
    if not options:
        # 没有"另一名其他角色"可指定 ⇒ 没有【决斗】；获得装备这件事已经发生。
        return None
    answer = host.ask_option(
        frame,
        target,
        Timing.DAMAGE_AFTER_DEAL,
        skill_id=LIYU_DUEL_TARGET_ASK,
        options=options,
        optional=False,
    )
    if host.suspended(answer):
        return "stay"
    frame.data[_LIYU_STARTED] = True
    host.start_use(
        duel,
        source=owner,
        targets=(option_seat(answer),),
        use_timing=Timing.DAMAGE_AFTER_DEAL,
    )
    return None


LIYU_FLOW = SkillFlow(
    name="liyu",
    steps=(
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP, Zone.JUDGE),
            lo=1,
            hi=1,
            optional=False,
            face=_liyu_target,
            # 受伤者的手牌对吕布是隐藏槽（与【反馈】同姿态）。
            reveal_hand=False,
        ),
    ),
    effect=_liyu_effect,
)
"""【利驭】的一步流水线：从受伤者区域内获得一张牌 → 提交点里分叉（摸牌 / 虚拟【决斗】）。

选牌步复用「反馈」的选取面形状（``face`` 指向别人、``actor`` 是技能持有者）；【决斗】
目标的座位询问住在提交点里，因为"要不要问"取决于刚获得的牌是不是装备——步骤语言表达
不了"按前序答案决定这一步在不在"。
"""


# ---------------------------------------------------------------------------
# 界华雄【耀武】（M6 票 18）：锁定技，受到**牌造成的**伤害时按颜色决定谁摸一张
#
# §十九 L2218–L2219：
#
#     耀武：锁定技，当你受到牌造成的伤害时，若此牌为红色，则伤害来源摸一张牌，否则你
#           摸一张牌。
#
# 三条判据合起来才是"牌造成的伤害"：**渠道牌存在**（无来源伤害 / 失去体力都不算）、
# **受伤的是持有者**（``_hurt_owner``）、**每次伤害事件一次**（牌面没有"1 点"⇒
# ``per_point=False``）。锁定技没有"是否发动"询问，故直接挂 ``handler``。
# ---------------------------------------------------------------------------

JIE_YAOWU = hero_skill_id("jie_huaxiong", "yaowu")
"""界华雄【耀武】的全限定技能 id（§十九 L2218–L2219）。"""


def _yaowu_condition(host: Any, frame: Any) -> bool:
    """触发条件：**我**受到**牌造成的**伤害（渠道牌在场）。"""
    del host
    return _hurt_owner(frame) and _damage_channel_card(frame) is not None


def _yaowu(host: Any, frame: Any) -> None:
    """红色渠道牌 ⇒ 伤害来源摸一张；否则华雄摸一张（无来源 ⇒ 无人可摸）。"""
    owner = frame.data["skill_owner"]
    card = _damage_channel_card(frame)
    if card is None:  # pragma: no cover - 条件已挡
        return None
    color = card.color if card.color is not None else color_of_suit(card.suit)
    if color is Color.RED:
        source = frame.data.get("source")
        if source is None:
            return None
        source = int(source)
        if not host.state.player(source).alive:
            return None
        host.draw_for_hand(frame, source, 1)
        return None
    host.draw_for_hand(frame, owner, 1)
    return None


# ---------------------------------------------------------------------------
# 界公孙瓒【趫猛】（M6 票 18）：【杀】造成伤害后选其区域里的一张牌——坐骑获得、其余弃置
#
# §十九 L2226–L2228：
#
#     趫猛：当你使用【杀】对一名角色造成伤害后，你可以选择其区域里的一张牌，若此牌：
#           为坐骑牌，你获得之；不为坐骑牌，你弃置之。
#
# **"区域里的一张牌"** 按 `GLOSSARY.md`「区域默认」= 手牌 + 装备 + 判定区；受伤者的手牌
# 对选择者（公孙瓒）是**隐藏槽**——可能选到手牌而看不到牌面（票面 `## 已知歧义` 的第三条，
# 写进 `06`（§5「程序化约定与偏离一手」））。
# ---------------------------------------------------------------------------

JIE_QIAOMENG = hero_skill_id("jie_gongsunzan", "qiaomeng")
"""界公孙瓒【趫猛】的全限定技能 id（§十九 L2226–L2228）。"""

_QIAOMENG_MATCHED = "qiaomeng_matched"
"""帧私有：本帧上【趫猛】已经命中过（与【利驭】同一条重入标记的理由）。

选牌步的**面**在每次重入都重新求值，而前半段会把那张牌搬走——不认这个标记，重入会
看到空面、把整条流水线判成取消。
"""

_HORSE_SLOTS: tuple[SlotKind, ...] = (SlotKind.HORSE_OFFENSE, SlotKind.HORSE_DEFENSE)
"""坐骑牌的槽位种类（"为坐骑牌 ⇒ 获得之"的判据，不写死六个牌名 id）。"""


def _qiaomeng_target(state: GameState, frame: Any, owner: int, answers: Any) -> Any:
    """选牌步的 ``face``：受伤者的区域；不是"造成伤害的是我"或没有牌 ⇒ 空元组 = 无此步。"""
    del answers
    target = _slash_damage_target(state, frame, owner)
    if target is None:
        return ()
    if frame.data.get(_QIAOMENG_MATCHED) == owner:
        return target
    player = state.player(target)
    if not (player.hand or player.equip or player.judge):
        return ()
    return target


def _qiaomeng_condition(host: Any, frame: Any) -> bool:
    """触发条件：**我用【杀】**对另一个角色造成伤害，且其区域里有牌可选。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    if frame.data.get(_QIAOMENG_MATCHED) == owner:
        return True
    if _qiaomeng_target(host.state, frame, owner, None) == ():
        return False
    frame.data[_QIAOMENG_MATCHED] = owner
    return True


def _qiaomeng_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：坐骑牌 ⇒ 获得；否则 ⇒ 弃置（"你弃置之"，弃置者是技能持有者）。"""
    owner = frame.data["skill_owner"]
    card = host.state.card(int(answers["card"][0]))
    target = _slash_damage_target(host.state, frame, owner)
    if target is None:  # pragma: no cover - 条件已挡
        return None
    if card.slot in _HORSE_SLOTS:
        control = host.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.HAND, owner),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
    else:
        control = host.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=target,
            actor=owner,
        )
    return "stay" if control == "stay" else None


QIAOMENG_FLOW = SkillFlow(
    name="qiaomeng",
    steps=(
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP, Zone.JUDGE),
            lo=1,
            hi=1,
            optional=False,
            face=_qiaomeng_target,
            reveal_hand=False,
        ),
    ),
    effect=_qiaomeng_effect,
)
"""【趫猛】的一步流水线：选受伤者区域内一张牌 → 坐骑获得 / 其余弃置。"""


# ---------------------------------------------------------------------------
# 夏侯惇【刚烈】（M6 P0 票 01）：判定 → 由**伤害来源**二选一
# ---------------------------------------------------------------------------

GANGLIE_DISCARD_TWO = "discard_two"
"""【刚烈】选项一：伤害来源弃置两张手牌。"""

GANGLIE_TAKE_DAMAGE = "take_damage"
"""【刚烈】选项二：伤害来源受到夏侯惇造成的 1 点伤害。"""

GANGLIE_OPTIONS: tuple[str, ...] = (GANGLIE_DISCARD_TWO, GANGLIE_TAKE_DAMAGE)
"""【刚烈】的选项全集（顺序即询问里的顺序；AI 的 ``_OPTION_VALUES`` 与它同名）。"""

_GANGLIE_DISCARD_COUNT = 2
"""「弃置两张手牌」的张数（规则 §九 L961）。"""

_GANGLIE_JUDGED_KEY = "ganglie_judged"
"""帧私有：本帧的判定已经开过（重入时不再压第二个 JUDGE 帧）。"""

_GANGLIE_SUIT_KEY = "ganglie_suit"
"""帧私有：判定牌的花色（"heart" 终止；其余一律结算——方块虽红也要结算）。"""

_GANGLIE_CHOICE_KEY = "ganglie_choice"
"""帧私有：伤害来源选的那一项（重入时不再重问）。"""

_GANGLIE_DONE_KEY = "ganglie_done"
"""帧私有：本帧的效果已经执行 / 已经压出伤害子帧（重入直接收尾）。"""

_ASK_GANGLIE_DISCARD = "hero.xiahoudun.ganglie.discard"
"""手写询问位置：【刚烈】「弃置两张手牌」的**选牌步**（:meth:`Engine.ask_selection` 的
``owner``；询问名 ``key`` 取 ``"ganglie:discard"``）。"""

GANGLIE_SKILL_ID = "hero.xiahoudun.ganglie"
"""【刚烈】的全限定技能 id（判定帧回写坐标用；与数据侧同源派生）。"""


def _ganglie_source(state: GameState, frame: Any, owner: int | None) -> int | None:
    """【刚烈】的结算对象：**造成这次伤害的角色**；无来源 / 是自己 / 已阵亡 ⇒ ``None``。

    无来源伤害（【闪电】一类）没有"伤害来源"可问；自己是来源时"对伤害来源造成伤害 /
    弃置伤害来源的手牌"退化成自伤，与【反馈】的口径一致地排除（技能面写的是"伤害来源"，
    不是"一名角色"）。来源已阵亡时不向死者发询问。
    """
    source = frame.data.get("source")
    if source is None or owner is None or int(source) == int(owner):
        return None
    source = int(source)
    if not state.player(source).alive:
        return None
    return source


def _ganglie_condition(host: Any, frame: Any) -> bool:
    """【刚烈】的触发条件：**受伤的是持有者自己**，且有一个可结算的伤害来源。

    持有者判据与同族三条同源（读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`）：派发环
    遍历每个存活玩家的实例，漏掉它就会"任何人挨打、夏侯惇都判一次"。
    """
    return _hurt_owner(frame) and _ganglie_source(
        host.state, frame, frame.data.get(MATCH_OWNER_KEY)
    ) is not None


def _ganglie_options(state: GameState, source: int) -> tuple[str, ...]:
    """"弃两张手牌"付不起（手牌 < 2）时只留伤害一项（票面「手牌不足两张时只有一个选项」）。"""
    if len(state.player(source).hand) >= _GANGLIE_DISCARD_COUNT:
        return GANGLIE_OPTIONS
    return (GANGLIE_TAKE_DAMAGE,)


def _push_ganglie_damage(host: Any, frame: Any, *, source: int, target: int, amount: int = 1) -> None:
    """压一个无渠道牌的 ``DAMAGE`` 子帧（【刚烈】的"受到你造成的 1 点伤害"）。

    ``source`` = 夏侯惇（技能持有者）、``target`` = 伤害来源；``channel`` 恒 ``None``
    （这次伤害不是任何一张牌造成的，绝不能继承外层伤害的渠道牌）。
    """
    host.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": None,
            "use_timing": frame.data.get("use_timing"),
            "target": target,
            "channel": None,
            "nature": DamageNature.NORMAL,
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )


def _ganglie(host: Any, frame: Any) -> Any:
    """夏侯惇【刚烈】：受伤后判定；非红桃则伤害来源二选一（规则 §九 L960–961）。

    三段，每段都可挂起，靠帧私有游标续跑（与【铁骑】同一范式）：

    1. **判定**：:meth:`Engine.open_skill_judgment` 压 JUDGE 帧；结算后引擎把判定牌回写进
       本帧（:data:`~sgs.skills.SKILL_JUDGMENT_CARD_KEY`），取它的**花色**：
       红桃 ⇒ 终止；方块 / 黑牌 ⇒ 继续。
    2. **二选一**：``ask_option`` 问 **伤害来源**（``actor = source``，不是持有者），
       选项 = 弃两张手牌 / 受 1 点伤害；手牌不足两张时只有后者。
    3. **执行**：选伤害 ⇒ 压 DAMAGE 子帧（来源 = 持有者）；选弃牌 ⇒ 问来源选两张手牌、
       经 :meth:`Engine.move_in_frame` 进弃牌堆（``reason = DISCARD``，走同一条"失去牌"
       派发口，【连营】一类钩子照常触发）。

    每一步的完成事实都记在帧私有键里，故重入既不复述也不重问；压伤害子帧前先落
    ``done``——子帧出栈后重入直接收尾，不会打第二遍。
    """
    state = host.state
    owner = frame.data["skill_owner"]
    source = _ganglie_source(state, frame, owner)
    if source is None or frame.data.get(_GANGLIE_DONE_KEY):
        return None
    if not frame.data.get(_GANGLIE_JUDGED_KEY):
        frame.data[_GANGLIE_JUDGED_KEY] = True
        host.open_skill_judgment(frame, owner, GANGLIE_SKILL_ID)
        return "stay"
    if _GANGLIE_SUIT_KEY not in frame.data:
        judge_card = frame.data.pop(SKILL_JUDGMENT_CARD_KEY, None)
        # 同帧的布尔结果只有本技能读（判定是刚开的、没有别人写），取完即弹。
        frame.data.pop("skill_result", None)
        frame.data[_GANGLIE_SUIT_KEY] = None if judge_card is None else judge_card.suit
    if frame.data[_GANGLIE_SUIT_KEY] == Suit.HEART.value:
        frame.data[_GANGLIE_DONE_KEY] = True
        return None
    choice = frame.data.get(_GANGLIE_CHOICE_KEY)
    if choice is None:
        answer = host.ask_option(
            frame,
            source,
            Timing.DAMAGE_AFTER_TAKE,
            skill_id="ganglie:choice",
            options=_ganglie_options(state, source),
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        choice = answer.choice
        frame.data[_GANGLIE_CHOICE_KEY] = choice
    if choice == GANGLIE_TAKE_DAMAGE:
        frame.data[_GANGLIE_DONE_KEY] = True
        _push_ganglie_damage(host, frame, source=owner, target=source)
        return "stay"
    picked = host.ask_selection(
        frame,
        owner=_ASK_GANGLIE_DISCARD,
        key="ganglie:discard",
        actor=source,
        timing=Timing.DAMAGE_AFTER_TAKE,
        spec=SelectionSpec(
            zones=(Zone.HAND,),
            total=(_GANGLIE_DISCARD_COUNT, _GANGLIE_DISCARD_COUNT),
            optional=False,
        ),
        # 应答的是**手牌持有者本人** ⇒ 可见槽（与【贯石斧】/【雌雄】的自选口径一致）。
        build_face=lambda: host.selection_face(source, (Zone.HAND,), reveal_hand=True),
    )
    if host.suspended(picked):
        return "stay"
    frame.data[_GANGLIE_DONE_KEY] = True
    if not picked:
        # 防御分支：``optional=False`` 时引擎不会给空答案；直接调用方硬塞空元组时收尾。
        return None
    control = host.move_in_frame(
        frame,
        state.refs(picked),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=source,
        actor=source,
    )
    if control == "stay":
        return "stay"
    return None


# ---------------------------------------------------------------------------
# 界夏侯惇【刚烈】（M6 票 08；较界版整句重写）
#
# §十九 L2039–L2041：
#
#     刚烈：当你受到 1 点伤害后，你可以进行一次判定，若结果为：红色，你对伤害来源
#           造成 1 点伤害；黑色，你弃置伤害来源的一张牌。
#
# 与标准【刚烈】（票 01："判定 → 来源二选一"）**不是**同一句：判据从"不为红桃"变成
# 红 / 黑二分，结算从"来源选择弃两张手牌或受 1 点伤害"变成"红色你打他 / 黑色你拆他"。
# 因此不复用 :func:`_ganglie`，只共用同形的触发条件（受伤者是自己 ∧ 有可结算来源）与
# 判定原语（``open_skill_judgment`` / ``SKILL_JUDGMENT_CARD_KEY``）。
# ---------------------------------------------------------------------------

JIE_GANGLIE = "hero.jie_xiahoudun.ganglie"
"""界夏侯惇【刚烈】的全限定技能 id（§十九 L2039–L2041）。"""

_JIE_GANGLIE_JUDGED_KEY = "jie_ganglie_judged"
"""帧私有：本帧的判定已经开过（重入不再压第二个 JUDGE 帧）。"""

_JIE_GANGLIE_SUIT_KEY = "jie_ganglie_suit"
"""帧私有：判定牌的花色（红 / 黑二分，取完即弹）。"""

_JIE_GANGLIE_DONE_KEY = "jie_ganglie_done"
"""帧私有：效果已经执行 / 已经压出伤害子帧（重入直接收尾）。"""

_ASK_JIE_GANGLIE_DISCARD = "hero.jie_xiahoudun.ganglie.discard"
"""手写询问位置：界【刚烈】黑色分支「你弃置来源的一张牌」的选牌步。"""


def _jie_ganglie_condition(host: Any, frame: Any) -> bool:
    """触发条件：**受伤的是持有者自己**，且有一个可结算的伤害来源。

    与标准【刚烈】同源（:func:`_hurt_owner` + :func:`_ganglie_source`）：牌面写"伤害来源"，
    无来源 / 来源是自己 / 来源已阵亡时连"是否发动"都不问。
    """
    return _hurt_owner(frame) and _ganglie_source(
        host.state, frame, frame.data.get(MATCH_OWNER_KEY)
    ) is not None


def _jie_ganglie_keys(frame: Any) -> tuple[str, str, str]:
    """逐点触发时的帧私有键：**按伤害点号加后缀**。

    ``Hook.per_point=True`` 的技能在同一条 DAMAGE 帧里被派发多轮（一点一轮），故"判定开过 /
    花色 / 收尾"三个游标必须逐点分开——共用一套键会让第 2 点直接看到第 1 点的"已收尾"而
    静默跳掉整条效果（本票的实现一开始就踩了这一条）。
    """
    suffix = f":{damage_point_index(frame)}"
    return (
        f"{_JIE_GANGLIE_JUDGED_KEY}{suffix}",
        f"{_JIE_GANGLIE_SUIT_KEY}{suffix}",
        f"{_JIE_GANGLIE_DONE_KEY}{suffix}",
    )


def _jie_ganglie(host: Any, frame: Any) -> Any:
    """界【刚烈】：受伤后判定；红 ⇒ 对来源 1 点伤害，黑 ⇒ 弃置来源的一张牌。

    三段、每段一个帧私有游标（与标准【刚烈】/【铁骑】同一范式）：

    1. **判定**：``open_skill_judgment`` 压 JUDGE 帧，结算后判定牌回写进本帧；取花色后用
       :func:`~sgs.data.color_of_suit` 判红 / 黑（黄月英式"颜色"判据的唯一真源）；
    2. **红色**：压一个无渠道牌的 DAMAGE 子帧（来源 = 持有者、目标 = 伤害来源）；
    3. **黑色**：问**持有者**从来源的三区里选一张（"你弃置"），搬进弃牌堆。来源没有牌时
       跳过选牌（没有可弃的牌），不产生空询问。
    """
    state = host.state
    owner = frame.data["skill_owner"]
    judged_key, suit_key, done_key = _jie_ganglie_keys(frame)
    source = _ganglie_source(state, frame, owner)
    if source is None or frame.data.get(done_key):
        return None
    if not frame.data.get(judged_key):
        frame.data[judged_key] = True
        host.open_skill_judgment(frame, owner, JIE_GANGLIE)
        return "stay"
    if suit_key not in frame.data:
        judge_card = frame.data.pop(SKILL_JUDGMENT_CARD_KEY, None)
        # 同帧的布尔结果只有本技能读（判定是刚开的、没有别人写），取完即弹。
        frame.data.pop("skill_result", None)
        frame.data[suit_key] = None if judge_card is None else judge_card.suit
    if color_of_suit(frame.data[suit_key]) is Color.RED:
        frame.data[done_key] = True
        _push_ganglie_damage(host, frame, source=owner, target=source)
        return "stay"
    frame.data[done_key] = True
    if not host.has_zone_cards(source):
        return None
    picked = host.ask_selection(
        frame,
        owner=_ASK_JIE_GANGLIE_DISCARD,
        key=f"jie_ganglie:discard:{damage_point_index(frame)}",
        actor=owner,
        timing=Timing.DAMAGE_AFTER_TAKE,
        spec=SelectionSpec(
            zones=(Zone.HAND, Zone.EQUIP),
            total=(1, 1),
            optional=False,
        ),
        # 选牌者是**夏侯惇**（不是来源）⇒ 面铺来源的手牌 + 装备区，手牌是隐藏槽。
        # 牌面只写「一张牌」，默认不含判定区（票 15 默认读法）。
        build_face=lambda: host.selection_face(source, (Zone.HAND, Zone.EQUIP)),
    )
    if host.suspended(picked):
        frame.data[done_key] = False
        return "stay"
    if not picked:
        return None
    control = host.move_in_frame(
        frame,
        state.refs(picked),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=source,
        actor=owner,
    )
    return "stay" if control == "stay" else None


# ---------------------------------------------------------------------------
# 魏延【狂骨】（风包）：造伤后按「扣减体力前」的距离判定，回复 1 点体力
#
# 牌面（用户录入 `D:\SGS_HEROS\神话再临-风包\魏延\技能.txt`，逐字）：
#
#     狂骨：锁定技，当你对一名角色造成1点伤害后，若其扣减体力前你计算与其的距离
#           不大于 1，你回复 1 点体力。
#
# 读法（`docs/agents/skill-authoring/readings.md` 的五行表，逐行落到账本）：
#
# - **触发粒度 = 逐点**：牌面「1 点」⇒ 造成 N 点就判 N 次（默认读法（三）；`三国杀详细
#   规则.txt` §4.14 L404–L407 逐字「按'造成 / 受到 1 点伤害后'判定的技能可发动 X 次」）。
# - **指代**：「其」= 本次伤害的受伤者（帧里的 ``target``）；「你」= 持有者**且必须是伤害
#   来源**（牌面写"**你**对…造成"）⇒ 条件自校 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`
#   ``== frame["source"]``，否则"任何人造成伤害、魏延都回血"。
# - **「一名角色」含自己**（默认读法（二）①）：自伤时距离 0（``distance`` 自身恒 0）⇒ 同样
#   成立；本仓现有牌面走不到，但不写"排除自己"的判据。
# - **「扣减体力前」= 距离的取值时点**（§4.15 L412 逐字把【狂骨】的发动条件检测放在
#   「扣减体力前」）：见 :func:`_kuanggu_snapshot` 为什么必须**定格**而不能在造伤后现算。
# - **锁定技** ⇒ 一次"是否发动"询问都不发（`:func:`~sgs.skills.catalog._dispatch` 的 forced
#   分支直接跑回调）；本技能没有代价、没有目标、没有选项，故**整条技能零询问**。
# ---------------------------------------------------------------------------

KUANGGU = "hero.weiyan.kuanggu"
"""魏延【狂骨】的全限定技能 id（风包牌面，用户录入的 `技能.txt`）。"""

KUANGGU_DISTANCE_KEY = "kuanggu_distance"
"""帧私有槽名：**扣减体力前**「你计算与其的距离」（伤害帧上，由受伤害时定格）。

帧私有数据不进记录流，故对既有 47 条金录像逐字节无影响；它只服务"造伤后仍按扣减体力前的
场况判"，寿命 = 这一次伤害事件（伤害帧出栈即弃）。
"""

KUANGGU_DONE_KEY = "kuanggu_done"
"""帧私有槽名前缀：本点伤害的回复子帧已经压过（槽名再带点号，逐点各一份）。"""

KUANGGU_RANGE = 1
"""牌面「距离不大于 1」的那条线（`distance` 的下限是 1、自身 0，故相等也算）。"""


def _kuanggu_deals(host: Any, frame: Any) -> bool:
    """**这次伤害是持有者造成的吗**（两条钩子共用的前提）。

    读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（派发环在条件求值前写）而不是
    ``skill_owner``（回调执行期才写）：条件必须能按"我是谁"过滤——派发环遍历每个存活玩家的
    实例，漏掉它就会"任何人造成伤害、魏延都回血"（与 :func:`_hurt_owner` 同一姿态、方向相反）。

    无来源伤害（【闪电】一类）的 ``source`` 是 ``None``，与 ``owner`` 永不相等 ⇒ 天然排除。
    """
    del host
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    if frame.data.get("source") != owner:
        return False
    return frame.data.get("target") is not None


def _kuanggu_snapshot(host: Any, frame: Any) -> None:
    """**扣减体力前**把「你计算与其的距离」写进伤害帧（``DAMAGE_TAKE`` 钩子）。

    为什么必须定格、不能在 :func:`_kuanggu` 里现算（本技能唯一的实现难点）：

    1. 规则把发动条件检测放在**扣减体力前**（§4.15 L412），牌面也写"其扣减体力前你计算
       与其的距离"；
    2. 而「造成伤害后」是在**濒死 / 死亡结算之后**才跑的（`三国杀详细规则.txt` §4.14 第 4 条 +
       本仓的栈形状：目标被打死、对局继续时 ``DAMAGE_AFTER_DEAL`` 照跑，此刻死者
       ``alive=False``、其区域已被死亡结算清空）。死者的**距离修饰器**因此不再解析 ——
       装备技能（防御马 +1）与【义从】这类"其他角色计算与你的距离 +1"都随实例一起消失
       ⇒ 现算会把距离**低估 1**：相邻目标带【的卢】（+1 防御马）时，扣减体力前距离是
       **2**（狂骨**不**该触发），打死后再算变成 **1**（会白回 1 点体力）。
    3. 座位环本身不受影响（死者的座位距离与存活时逐值相同：`distance` 的退化读法
       `_counting_distance` 就是"数两方向中间的存活者 + 1"），故差异只来自**修饰器**这一侧；
       但正是这一侧决定"够不够得着"。

    时点选 ``DAMAGE_TAKE``（受到伤害时）而不是更早的 ``DAMAGE_DEAL``：它是扣减体力之前**最后
    一个派发点**——``damage_finalize_amount`` 夹在两者之间，只算数值、不 dispatch、不改存活
    集合，故"此刻的距离"与"扣减体力前的距离"逐值相同。伤害被防止时（帧标签 ``PREVENTED``）
    ``DAMAGE_AFTER_DEAL`` 整条不派发，这份快照自然无人读。

    若将来做"伤害转移"（天香一类，转移发生在受伤害时之内），本快照的时点须跟着挪到转移之后。
    """
    owner = int(frame.data["skill_owner"])
    target = frame.data["target"]
    frame.data[KUANGGU_DISTANCE_KEY] = distance(host.state, owner, int(target))


def _kuanggu_condition(host: Any, frame: Any) -> bool:
    """触发条件：**我是伤害来源** ∧ 受伤者存在 ∧ 扣减体力前的距离 ≤ 1。"""
    if not _kuanggu_deals(host, frame):
        return False
    snapshot = frame.data.get(KUANGGU_DISTANCE_KEY)
    return snapshot is not None and int(snapshot) <= KUANGGU_RANGE


def _kuanggu(host: Any, frame: Any) -> Any:
    """魏延【狂骨】：逐点结算 —— 每 1 点伤害回一次 1 点体力（§4.14 L404–L407）。

    效果走**既有的回复体力路径**（压 ``RECOVER`` 子帧、``amount = 1``）：`回复体力` 是一类
    独立事件（§4.15 L424–L426），`RECOVER_VALUE` / `RECOVER_AFTER` 上的钩子（界孙权【救援】
    这类改回复量的技能）因此看得到它；回复量夹在体力上限内由 RECOVER 帧自己负责。

    **满体力时不压帧**（§4.15 L426 逐字"满体力时不回复"）：锁定技照常"发动"，但什么也不
    发生——没有 delta、也没有回复事件可供别人挂钩。

    逐点游标 ``kuanggu_done:<点号>`` 在压帧**之前**落位：子帧出栈后派发环会从头重入本回调，
    游标让第 N 点既不重压第二个回复帧、也不挡掉第 N+1 点（与界【刚烈】的逐点键同一范式）。
    """
    owner = int(frame.data["skill_owner"])
    done_key = f"{KUANGGU_DONE_KEY}:{damage_point_index(frame)}"
    if frame.data.get(done_key):
        return None
    frame.data[done_key] = True
    player = host.state.player(owner)
    if player.hp >= player.hp_max:
        return None
    host.push_frame(
        FrameKind.RECOVER,
        {
            "source": owner,
            "targets": (owner,),
            "card": None,
            "use_timing": frame.data.get("use_timing"),
            "amount": 1,
        },
        timing=Timing.RECOVER_VALUE,
    )
    return "stay"


KUANGGU_DEF = SkillDef(
    skill_id=KUANGGU,
    locked=True,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        # ① 扣减体力前定格距离（不问、只写帧私有键）。
        Hook(Timing.DAMAGE_TAKE, handler=_kuanggu_snapshot, condition=_kuanggu_deals),
        # ② 造成伤害后逐点回复（条件读 ① 的快照）。
        Hook(
            Timing.DAMAGE_AFTER_DEAL,
            handler=_kuanggu,
            condition=_kuanggu_condition,
            per_point=True,
        ),
    ),
    requirement=(
        "锁定技：当你对一名角色造成 1 点伤害后，若其扣减体力前你计算与其的距离不大于 1，"
        "你回复 1 点体力"
    ),
)
"""【狂骨】一条定义：两条钩子（定格距离 / 逐点回复），锁定技 ⇒ 零询问、无流水线步骤。

不并进 :data:`DAMAGE_AFTER_SKILLS`：那张表是"受伤侧一条时机 + 一条条件 + 一个回调"的声明
形状（同一个 handler 挂到 ``timings`` 里的每个时机），而【狂骨】的两个时机要跑**不同**的回调
（受伤害时定格、造伤后回复）。它是 B 族里第一条**造伤侧**的锁定技，故用整份 ``SkillDef`` 声明
——仍是既有原语（钩子 + 帧私有键 + RECOVER 子帧），不新增机制。
"""


# ---------------------------------------------------------------------------
# 沮授【矢北】（一将成名-2014；jushou-lusu 票 01，用户 2026-11-08 签 Q2 / Q5）
#
# 规则原文（用户 `技能.txt` 逐字；OL 一将成名官网同句的展开式见票面 §0.1）：
#
#     矢北：锁定技，当你受到伤害后，若此次伤害是你本回合受到的第一次伤害，则你回复1点体力，
#           否则你失去1点体力。
#
# 三个判据都落在**引擎侧座位事实**上（:attr:`~sgs.state.Player.damage_taken_this_turn`）：
#
# - **「本回合」= 当前这个游戏回合**（谁的回合都算；一手官网逐字「每当你于一名角色的回合内
#   受到第一次伤害后」，账本 L889 同向）⇒ 复位点 = 回合末对所有座位清零，**不是**自己的
#   ``TURN_START``；
# - **逐事件、不是逐点**（牌面没有「1 点」限定词，账本 L777）：一次 2 点伤害只算 1 次、
#   只回复 1 点；
# - **0 伤害 / 被防止的伤害不算受到伤害**（规则 §4.14 L396–L397）⇒ 既不消耗"第一次"、
#   也不推进次数（写点与派发环两处同判据）。
# ---------------------------------------------------------------------------

SHIBEI = hero_skill_id("jushou", "shibei")
"""沮授【矢北】的全限定技能 id（一将成名-2014 包）。"""

_SHIBEI_DONE_KEY = "shibei_done"
"""帧私有：这次伤害事件已经结算过【矢北】（子帧出栈后派发环重入，不重做）。"""


def _shibei(host: Any, frame: Any) -> Any:
    """【矢北】的效果：本回合第 1 次伤害 ⇒ 回复 1 点；第 2 次及以后 ⇒ 失去 1 点。

    两个分支各压一个**既有**子帧（与【狂骨】/【乱武】同形），故回复 / 失去体力两条链上的
    钩子（【救援】一类改回复量、濒死窗口）都照常看得到：

    - 回复走 ``RECOVER`` 帧；**满体力时不压帧**（规则 §4.15 L426「满体力时不回复」）——
      锁定技照常"发动"，但没有 delta、也没有回复事件可供别人挂钩；
    - 失去体力走 ``LIFE_DECREASE`` 帧、``source = None``（失去体力没有来源）；
      体力扣到 0 的濒死由该帧自己处理（规则 §4.15 L415–L416 / §4.17）。

    游标在压帧**之前**落位：子帧出栈后派发环从头重入本回调，没有它会把同一次伤害结算两遍。
    """
    owner = int(frame.data["skill_owner"])
    if frame.data.get(_SHIBEI_DONE_KEY):
        return None
    frame.data[_SHIBEI_DONE_KEY] = True
    player = host.state.player(owner)
    if player.damage_taken_this_turn <= 1:
        # 第 1 次（含"构造局面直接写 hp 却没走伤害帧"以外的所有正常路径）。
        if player.hp >= player.hp_max:
            return None
        host.push_frame(
            FrameKind.RECOVER,
            {
                "source": owner,
                "targets": (owner,),
                "card": None,
                "use_timing": frame.data.get("use_timing"),
                "amount": 1,
            },
            timing=Timing.RECOVER_VALUE,
        )
        return "stay"
    host.push_frame(
        FrameKind.LIFE_DECREASE,
        {"target": owner, "source": None, "amount": 1},
        timing=Timing.LIFE_DECREASE_BEFORE,
    )
    return "stay"


SHIBEI_DEF = SkillDef(
    skill_id=SHIBEI,
    locked=True,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(Hook(Timing.DAMAGE_AFTER_TAKE, handler=_shibei),),
    requirement=(
        "锁定技：当你受到伤害后，若此次伤害是你本回合受到的第一次伤害，你回复 1 点体力；"
        "否则你失去 1 点体力"
    ),
)
"""【矢北】一条定义：锁定技 ⇒ 不问是否发动、无步骤（`shapes.md` §2 第 1 行的锁定技变体）。

自校持有者由派发环的 ``MATCH_OWNER_KEY`` 保证（``frame.data["skill_owner"]`` 恒是实例
持有者），故"任何人挨打都触发"不会发生——与【耀武】同姿态。比较的左侧不是帧载荷而是
座位事实 ``damage_taken_this_turn``：它在 :func:`~sgs.engine.steps.damage_finalize_amount`
里**本事件已计入**（写点早于 ``DAMAGE_AFTER_TAKE`` 派发），故第 1 次伤害读到的就是 1。
"""


# ---------------------------------------------------------------------------
# 界魏延移动版【狂骨】（jie-feng-batch 票 02；口径 = 移动版、显示名带「移动版」后缀）
#
#     当你对一名角色造成1点伤害后，若其扣减体力前你计算与其的距离不大于1，
#     你可以选择一项：1.回复1点体力；2.摸一张牌。
#
# 用户 2026-11-06 签字轮：**非锁定**（每点先问「是否发动」）；**每点各问一次选项**
# （选项答案键只含 skill_id + `_DISPATCH_SCOPE`，而逐点派发不写 scope ⇒ 手写询问的
# `skill_id` 必须带点号）；满体力两项都列出、不置灰（回复 = 零效果）；牌堆空选摸牌 =
# 短摸判平局。距离仍按**扣减体力前**定格（规则 §4.15 L412 + 账本「【狂骨】的距离按扣减
# 体力前取值」），快照键与标版分开。
# ---------------------------------------------------------------------------

JIE_KUANGGU_MOBILE = hero_skill_id("jie_weiyan_mobile", "kuanggu")
"""界魏延移动版【狂骨】的全限定技能 id。"""

JIE_KUANGGU_MOBILE_DISTANCE_KEY = "jie_kuanggu_mobile_distance"
"""帧私有：**扣减体力前**「你计算与其的距离」（与标版的 `kuanggu_distance` 分开）。"""

JIE_KUANGGU_MOBILE_DONE_KEY = "jie_kuanggu_mobile_done"
"""帧私有槽名前缀：本点的选项已经问过（再带点号，逐点各一份）。"""

JIE_KUANGGU_MOBILE_DRAWN_KEY = "jie_kuanggu_mobile_drawn"
"""帧私有槽名前缀：本点的「摸一张牌」已经执行（再带点号；摸牌可能挂起）。"""

KUANGGU_HEAL = "kuanggu_heal"
"""选项 1：回复 1 点体力（标签在 `tools/sgs_web/static/options.js:OPTION_LABELS`）。"""

KUANGGU_DRAW = "kuanggu_draw"
"""选项 2：摸一张牌。"""


def _jie_kuanggu_mobile_snapshot(host: Any, frame: Any) -> None:
    """``DAMAGE_TAKE``：把「扣减体力前」的距离写进本次伤害帧（照抄标版的手法）。"""
    owner = int(frame.data["skill_owner"])
    target = frame.data["target"]
    frame.data[JIE_KUANGGU_MOBILE_DISTANCE_KEY] = distance(
        host.state, owner, int(target)
    )


def _jie_kuanggu_mobile_condition(host: Any, frame: Any) -> bool:
    """触发条件：**我是伤害来源** ∧ 受伤者存在 ∧ 扣减体力前距离 ≤ 1。"""
    if not _kuanggu_deals(host, frame):
        return False
    snapshot = frame.data.get(JIE_KUANGGU_MOBILE_DISTANCE_KEY)
    return snapshot is not None and int(snapshot) <= KUANGGU_RANGE


def _jie_kuanggu_mobile(host: Any, frame: Any) -> Any:
    """效果：逐点问「选择一项」——回复 1 点体力（走 ``RECOVER`` 子帧）或摸一张牌。

    选项询问的 ``skill_id`` 带**点号**（``hero.jie_weiyan_mobile.kuanggu:opt:<点号>``）：
    选项答案键 = ``(option, skill_id, actor, _DISPATCH_SCOPE)``，而逐点派发不写 scope ⇒
    没有点号时第 2 点会命中第 1 点的答案、玩家不会再被问（``tests/test_hero_jie_feng.py``
    的 `test_jie_kuanggu_asks_the_choice_again_for_the_second_point` 钉住）。
    """
    state = host.state
    owner = int(frame.data["skill_owner"])
    point = damage_point_index(frame)
    done_key = f"{JIE_KUANGGU_MOBILE_DONE_KEY}:{point}"
    if frame.data.get(done_key):
        return None
    answer = host.ask_option(
        frame,
        owner,
        Timing.DAMAGE_AFTER_DEAL,
        skill_id=f"{JIE_KUANGGU_MOBILE}:opt:{point}",
        options=(KUANGGU_HEAL, KUANGGU_DRAW),
        optional=False,
    )
    if host.suspended(answer):
        return "stay"
    frame.data[done_key] = True
    if getattr(answer, "choice", "") == KUANGGU_HEAL:
        player = state.player(owner)
        if player.hp >= player.hp_max:
            # 满体力时不回复（规则 §4.15 L426）：不压帧、零 delta。
            return None
        host.push_frame(
            FrameKind.RECOVER,
            {
                "source": owner,
                "targets": (owner,),
                "card": None,
                "use_timing": frame.data.get("use_timing"),
                "amount": 1,
            },
            timing=Timing.RECOVER_VALUE,
        )
        return "stay"
    drawn_key = f"{JIE_KUANGGU_MOBILE_DRAWN_KEY}:{point}"
    if not frame.data.get(drawn_key):
        control = host.draw_for_hand(frame, owner, 1)
        frame.data[drawn_key] = True
        if control == "stay":
            return "stay"
    return None


JIE_KUANGGU_MOBILE_DEF = SkillDef(
    skill_id=JIE_KUANGGU_MOBILE,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        # ① 扣减体力前定格距离（记账、**不问** ⇒ `forced=True`）。
        Hook(
            Timing.DAMAGE_TAKE,
            handler=_jie_kuanggu_mobile_snapshot,
            condition=_kuanggu_deals,
            forced=True,
        ),
        # ② 造成伤害后逐点问「是否发动」→ 发动后逐点问「选择一项」。
        Hook(
            Timing.DAMAGE_AFTER_DEAL,
            handler=_jie_kuanggu_mobile,
            condition=_jie_kuanggu_mobile_condition,
            per_point=True,
        ),
    ),
    requirement=(
        "当你对一名角色造成1点伤害后，若其扣减体力前你计算与其的距离不大于1，"
        "你可以选择一项：1.回复1点体力；2.摸一张牌。"
    ),
    step_requirements={"opt": "选择一项：1.回复1点体力；2.摸一张牌"},
)
"""界【狂骨】一条定义：非锁定 + 两条钩子（定格距离 / 逐点询问），无流水线步骤。"""


# ---------------------------------------------------------------------------
# 神话再临·林包·移动版·神曹操【归心】（lin-batch 票 01）
#
#     归心
#     　当你受到1点伤害后，你可以获得每名其他角色区域里的一张牌，然后你翻面。
#
# 口径（用户 2026-11-08 两轮裁定的**移动版**文本，见 `.scratch/lin-batch/issues/01-shen-caocao.md`
# §R 与 `01b` 的 `## Answer`）：
#
# - **选择获得**（不是 OL 现行的「随机获得」）⇒ 逐座位**未知区域弹窗**、由神曹操自选 1 张；
# - **「区域里」= 三区**（手牌 + 装备 + 判定），`reveal_hand=False`
#   （账本默认读法（一）③ / `06` L674 / 规则 L1280；与【趫猛】【利驭】【涯角】逐字同形）；
# - **Q1 = B**：全场无人有牌也照发「是否发动」（翻面是 toggle，背面朝上时"只翻面"有收益）；
# - **Q2 = A**：发动后**不可**逐座少拿（`optional=False`、候选里没有 ``Pass``）；
# - **Q3 = A**：逐座位顺序 = **从持有者下家起的座位序**（`seats_from` 的座位环读法）。
#
# 形状 = **手写钩子**（逐点 + 变长逐座位循环，没有 ``SkillFlow``）⇒ 形态表「多轮技能」格，
# 与【遗计】/ 界魏延移动版【狂骨】同格（`shapes.md` §2 第 51 行）。翻面不另算形状：它是
# 状态位（`Engine.set_flipped`，唯一写入口），与【据守】同一条。
#
# **两个游标**（票面 §1 边界 G7）：① 本点已取的**座位集合**（帧私有、带点号）；
# ② 搬牌的完成由 `move_in_frame` 自己的重入守卫保证（"牌已在目标区"）。选牌询问的帧内键
# 带 **点号 + 座位号**（`guixin:<点号>:<座位号>`）—— 否则第 2 个座位会命中第 1 个座位的
# 答案（`06` L645 记下的那个坑，L899 的逐点答案键同一条纪律）。
# ---------------------------------------------------------------------------

GUIXIN = hero_skill_id("shen_caocao_mobile", "guixin")
"""神曹操移动版【归心】的全限定技能 id（lin-batch 票 01）。"""

GUIXIN_TAKEN_KEY = "guixin_taken"
"""帧私有槽名前缀：本点伤害里【归心】**已经取过**的座位（槽名再带点号）。"""

GUIXIN_FLIPPED_KEY = "guixin_flipped"
"""帧私有槽名前缀：本点的翻面已经落过（再带点号；重入不重翻）。"""

_ASK_GUIXIN_PICK = "hero.shen_caocao_mobile.guixin.pick"
"""手写询问位置（票 22）：【归心】逐座位"从三区里挑一张"的那次 ``ask_selection``。"""

_GUIXIN_ZONES: tuple[Zone, ...] = (Zone.HAND, Zone.EQUIP, Zone.JUDGE)
"""「区域里」= **三区**（账本默认读法（一）③）：手牌 + 装备区 + 判定区，一起铺成一张面。"""


def _guixin_condition(host: Any, frame: Any) -> bool:
    """触发条件：**受伤的是持有者自己**（``_hurt_owner``，B 族的共同前提）。

    **Q1 = B**（用户裁定）：这里**不**判"全场有没有牌"——「可以」的放弃出口只在发动那一问，
    而翻面是 toggle，背面朝上时"只翻面"本身有收益。场上无牌时照发问、发动后取 0 张。
    """
    del host
    return _hurt_owner(frame)


def _guixin_seats(state: GameState, owner: int) -> tuple[int, ...]:
    """此刻的座位池：**从持有者下家起**的座位序里、三区任一有牌的**其他**角色。

    顺序读 :func:`~sgs.state.seats_from` 的座位环读法（起点 = 持有者下家，用户 Q3 = A）；
    它**只给存活座位**，故这里只需再判两件事：「不是自己」与「三区里有牌」（R3：无牌跳过，
    同趫猛那句 ``if not (player.hand or player.equip or player.judge)``）。

    **每轮现算**（票面 §1 边界 G9）：搬牌会内联派发「失去牌后」的钩子，可能把某人打死或
    改变某座位的牌，故池不能预先快照。
    """
    total = len(state.players)
    pool: list[int] = []
    for seat in seats_from(state, (owner + 1) % total, total):
        if seat == owner:
            continue
        player = state.player(seat)
        if player.hand or player.equip or player.judge:
            pool.append(seat)
    return tuple(pool)


def _guixin(host: Any, frame: Any) -> Any:
    """神曹操【归心】：逐座位（座位序）从三区自选 1 张 → 收完**翻面**（逐点触发）。

    手写循环的骨架抄【遗计】：帧私有游标按**点号**分桶，重入既不重问已答过的座位、也不
    重搬已搬过的牌；每次进入都**重算座位池**（跳过已取座位 / 无牌座位 / 已死座位）。
    座位全部处理完才翻面——牌面写的是「**然后**你翻面」，翻面是效果的末段。
    """
    owner = int(frame.data["skill_owner"])
    point = damage_point_index(frame)
    taken_key = f"{GUIXIN_TAKEN_KEY}:{point}"
    flip_key = f"{GUIXIN_FLIPPED_KEY}:{point}"
    while True:
        taken: tuple[int, ...] = tuple(frame.data.get(taken_key, ()))
        seat = next(
            (s for s in _guixin_seats(host.state, owner) if s not in taken), None
        )
        if seat is None:
            break
        picked = host.ask_selection(
            frame,
            owner=_ASK_GUIXIN_PICK,
            # **点号 + 座位号**：帧内答案键 = (询问位置, 询问名)，缺一段就会串味（L645 / L899）。
            key=f"guixin:{point}:{seat}",
            actor=owner,
            timing=Timing.DAMAGE_AFTER_TAKE,
            # 恰好 1 张（G3）、发动后不可逐座放弃（Q2 = A）⇒ ``optional=False``、没有 ``Pass``。
            spec=SelectionSpec(zones=_GUIXIN_ZONES, total=(1, 1), optional=False),
            # 三区选取面，走引擎的同一个 ``selection_face``（手牌隐藏槽 / 装备与判定可见槽）。
            build_face=lambda seat=seat: host.selection_face(
                seat, _GUIXIN_ZONES, reveal_hand=False
            ),
        )
        if host.suspended(picked):
            return "stay"
        if not picked:
            # 防御分支：``optional=False`` 时引擎不会给出空答案；宿主级直调回调的测试若硬塞
            # 空元组，按"这一座跳过"处理而不是死循环。
            frame.data[taken_key] = (*taken, seat)
            continue
        # 进度**先落、再搬牌**（与【遗计】同一条理由）：``move_in_frame`` 返回 ``"stay"`` 的
        # 意思是「牌已经搬完了，只是『失去牌后』的钩子挂起了」，重入时不能再问 / 再搬一次。
        frame.data[taken_key] = (*taken, seat)
        control = host.move_in_frame(
            frame,
            host.state.refs(picked),
            to_zone=ZoneRef(Zone.HAND, owner),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    if frame.data.get(flip_key):
        return None
    if host.finished:
        return None
    # 「然后你翻面」= **toggle**（正 ↔ 背；L783）。翻面不是牌、不在任何区域，故不进牌守恒。
    host.set_flipped(frame, owner, not host.state.player(owner).flipped)
    frame.data[flip_key] = True
    return None


# ---------------------------------------------------------------------------
# 火包·移动版·荀彧【节命】（huo-taishici-xunyu 票 02）
#
#     当你受到1点伤害后，你可以令一名角色将手牌摸至体力上限（至多摸至五张）。
#
# 形状 = B 族逐点钩子（``DamageAfterSkill(per_point=True)``）+ 单步 ``PickSeats`` +
# ``effect`` 里手写摸牌（``Engine.draw_for_hand``，既有做法，不是第六种机制）。
#
# 数值口径 = **用户 2026-11-04 裁定的读法 B**：手牌终值 = ``min(体力上限, 5)``；
# 摸牌数 = ``max(0, 终值 − 现有手牌)``。依据：官方移动版站「摸至X张（X为其体力上限且
# 最多为5）」、官方规则集 3.0「补至X张（X为其体力上限且至多为5）」，且界荀彧同短 id 的
# 重写「摸X张，然后其将手牌弃置至X张（X为其体力上限且至多为5）」终值同样是 X。
# ---------------------------------------------------------------------------

JIEMING = hero_skill_id("xunyu", "jieming")
"""荀彧【节命】的全限定技能 id。"""

JIEMING_CAP = 5
"""「至多摸至五张」的上限（读法 B：**手牌终值**的上限）。"""


def _jieming_condition(host: Any, frame: Any) -> bool:
    """触发条件：**受伤的是荀彧自己**（B 族的共同前提，``_hurt_owner`` 的唯一判据）。"""
    del host
    return _hurt_owner(frame)


def _jieming_pool(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """目标池：**全部存活角色（含自己）**——「一名角色」按默认读法（二）①。

    槽位恒非空（至少持有者自己活着），故不会出现"没有候选的询问"。
    """
    del frame, owner, chosen
    return tuple(player.seat for player in state.players if player.alive)


def _jieming_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：把目标的手牌摸至 ``min(体力上限, 5)``（读法 B）。"""
    seat = next(iter(int(value) for value in answers.get("seat", ())), None)
    if seat is None:
        return None
    player = host.state.player(seat)
    cap = min(player.hp_max, JIEMING_CAP)
    need = max(0, cap - len(player.hand))
    if not need:
        # 手牌已在上限：仍是**合法发动**（G2「付得起但没收益」），只是摸 0 张。
        return None
    if host.draw_for_hand(frame, seat, need) == "stay":
        return "stay"
    return None


JIEMING_FLOW = SkillFlow(
    name="jieming",
    steps=(
        PickSeats(lo=1, hi=1, pool=_jieming_pool, optional=False, key="seat"),
    ),
    effect=_jieming_effect,
)
"""【节命】的一步流水线：选一名角色（含自己）→ 把手牌摸至 ``min(体力上限, 5)``。"""


# ---------------------------------------------------------------------------
# 林包·移动版·曹丕【放逐】（lin-batch2 票 01；用户 2026-11-08 签 Q4 / Q7 = A）
#
#     放逐
#     　当你受到伤害后，你可以令一名其他角色翻面，然后该角色摸X张牌（X为你已损失的体力值）。
#
# 五条读法（票面 §2 的读法表 / 六元组，逐条落成下面的代码）：
#
# - **触发粒度**：牌面**没有**「1 点」⇒ ``per_point=False``（每次伤害事件一次）。
#   对照【节命】写「受到 1 点伤害后」⇒ ``per_point=True``，两档不同。
# - **次序**：**先翻面、后摸牌**（Q4 = A）。三处一手（用户 `技能.txt` / 移动版官网 /
#   OL 官网）次序一致；仓内 `三国杀详细规则.txt` §13.4 的转写把两段反了过来，按账本 L773
#   （文本以一手为准）取一手 + 规则 §4.16 L464–L466「按技能描述的顺序处理效果」。
#   次序可观测：`flipped_change` 的 delta 落在摸牌那条 `card_move` 之前。
# - **X** = **持有者**的已损失体力值（体力上限 − 当前体力），**发动时定格**（账本 L741 /
#   L954 的同型口径：【英魂】签过的"发动时定格"）；不是目标的、也不逐次重算。
# - **摸牌者** = 目标（移动版把摸牌者点名为「该角色」），不是曹丕。
# - **目标池** = 除持有者以外的全部存活角色（含已背面朝上者 —— 翻面是 toggle，
#   翻回正面是合法收益）。
# ---------------------------------------------------------------------------

FANGZHU = hero_skill_id("caopi_mobile", "fangzhu")
"""曹丕移动版【放逐】的全限定技能 id。"""

FANGZHU_REQUIREMENT = (
    "当你受到伤害后，你可以令一名其他角色翻面，然后该角色摸X张牌（X为你已损失的体力值）。"
)
"""一句话文案 = 移动版牌面逐字（票面 §0.1：移动版把摸牌者写成「然后该角色」）。"""

_FANGZHU_X_KEY = "fangzhu_x"
"""帧私有：发动时**定格**的 X（持有者的已损失体力值）。"""

_FANGZHU_FLIPPED_KEY = "fangzhu_flipped"
"""帧私有：翻面已经做完（重入不重翻；G7 / G9）。"""

_FANGZHU_DRAWN_KEY = "fangzhu_drawn"
"""帧私有：摸牌已经开始（``draw_for_hand`` 不幂等，游标必须在**摸牌之前**落位）。"""


def _fangzhu_pool(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """目标池：**除持有者以外的全部存活角色**（「一名其他角色」，账本 L776②）。

    不按牌数 / 距离 / 翻面状态收窄：牌面没有这些限定词；已背面朝上的角色照样合法
    （翻面是 toggle，这一手把它翻回正面是真收益）。池空 ⇒ 座位步取消 ⇒ 整条流水线取消，
    不发空询问（S4；条件里也先挡一次）。
    """
    del frame, chosen
    return tuple(
        player.seat for player in state.players if player.alive and player.seat != owner
    )


def _fangzhu_condition(host: Any, frame: Any) -> bool:
    """触发条件：**受伤的是持有者自己** ∧ 场上至少有一名其他存活角色。

    前半句复用 B 族的 :func:`_hurt_owner`（条件求值期读派发环写好的
    :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`，不读 ``skill_owner``）；后半句与目标池
    共用同一个谓词 —— 池空时整条技能不产出候选、连"是否发动"都不问。
    """
    if not _hurt_owner(frame):
        return False
    owner = frame.data.get(MATCH_OWNER_KEY)
    return bool(_fangzhu_pool(host.state, frame, int(owner), ()))


def _fangzhu_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：**先**翻面（toggle）、**然后**目标摸 X 张；X = 持有者已损失体力值（定格）。

    "然后"是**同一次发动里的两段**，不是两问：座位步只收集目标，两段效果都在这里跑。

    重入纪律（G7）：``fangzhu_x`` / ``fangzhu_flipped`` / ``fangzhu_drawn`` 三个帧私有
    游标先落位再动作 —— 摸牌会内联派发「获得牌后」（【清俭】一类）并挂起，续跑既不重翻
    也不重摸（``draw_for_hand`` 返回 ``"stay"`` 时**原样上抛**）。
    """
    owner = int(frame.data["skill_owner"])
    seat = next(iter(int(value) for value in answers.get("seat", ())), None)
    if seat is None:
        return None
    x = frame.data.get(_FANGZHU_X_KEY)
    if x is None:
        # X = 体力上限 − 当前体力（账本 L741 的「已受伤」口径同一条算式）。
        player = host.state.player(owner)
        x = max(0, player.hp_max - player.hp)
        frame.data[_FANGZHU_X_KEY] = x
    if not frame.data.get(_FANGZHU_FLIPPED_KEY):
        frame.data[_FANGZHU_FLIPPED_KEY] = True
        # 翻面 = **toggle**（账本 L783 / 规则 §13.2）：正 → 背、背 → 正，落一条
        # `flipped_change`（唯一写入口 `Engine.set_flipped`）。
        host.set_flipped(frame, seat, not host.state.player(seat).flipped)
    if frame.data.get(_FANGZHU_DRAWN_KEY):
        return None
    # 摸牌**不幂等** ⇒ 游标在调用之前落位（与【英魂】的 `yinghun_drawn` 同姿态）。
    frame.data[_FANGZHU_DRAWN_KEY] = True
    if host.draw_for_hand(frame, seat, int(x)) == "stay":
        return "stay"
    return None


FANGZHU_FLOW = SkillFlow(
    name="fangzhu",
    steps=(
        # 单步选座（`shapes.md` §2 第 45 行「单步 → 效果」的选座位版；骨架抄【节命】，
        # 档位抄【反馈】的 `per_point=False` + 单步 flow）。``optional=False``：发动之后
        # 必须选一名角色 —— "翻面 + 摸 X 张"是整段，没有"发动了但什么都不做"的出口。
        PickSeats(lo=1, hi=1, pool=_fangzhu_pool, optional=False, key="seat"),
    ),
    effect=_fangzhu_effect,
)
"""【放逐】的一步流水线：选一名其他存活角色 → 翻面（toggle）→ 该角色摸 X 张。"""


DAMAGE_AFTER_SKILLS: tuple[DamageAfterSkill, ...] = (
    # 荀彧【节命】（火包·移动版）：逐点触发 → 选一名角色（含自己）→ 手牌摸至 min(上限, 5)。
    DamageAfterSkill(
        "xunyu",
        "jieming",
        True,
        _jieming_condition,
        None,
        JIEMING_FLOW,
        requirement="令一名角色将手牌摸至体力上限（至多摸至五张）",
        step_requirements={"seat": "选择一名角色，令其将手牌摸至体力上限（至多摸至五张）"},
    ),
    # 曹操【奸雄】：你可以获得造成此伤害的牌（规则 972）——每次伤害事件一次。
    # W6-01：零步技能迁 ``run_flow``（模块级 ``JIANXIONG_FLOW``），行为与旧回调逐字等价。
    DamageAfterSkill("caocao", "jianxiong", False, _jianxiong_condition, None, JIANXIONG_FLOW),
    # 司马懿【反馈】：你可以获得伤害来源的一张牌（规则 1014）——每次伤害事件一次。
    # 票 07：它是**被动技步骤流水线**（``SkillFlow``）的第一个消费者。
    DamageAfterSkill("simayi", "fankui", False, _fankui_condition, None, FANKUI_FLOW),
    # 郭嘉【遗计】：观看牌堆顶两张牌，然后交给任意角色（规则 993）——每受到 1 点伤害一次。
    DamageAfterSkill("guojia", "yiji", True, _yiji_condition, _yiji),
    # 夏侯惇【刚烈】：受伤后判定，非红桃则由伤害来源二选一（规则 §九 L960–961）。
    # 每次伤害事件一次（"受到伤害后"，不是"受到 1 点伤害后"）。
    DamageAfterSkill("xiahoudun", "ganglie", False, _ganglie_condition, _ganglie),
    # 界曹操【奸雄】（M6 票 14）：牌面比标准版多一句"并摸一张牌"（§十九 L2029），
    # 触发条件与"拿哪张牌"逐字相同 ⇒ 共用条件 / 搬牌体，只多一个摸一张的提交点。
    DamageAfterSkill(
        "jie_caocao",
        "jianxiong",
        False,
        _jie_jianxiong_condition,
        None,
        JIE_JIANXIONG_FLOW,
        requirement="获得造成伤害的牌并摸一张牌",
    ),
    # 界司马懿【反馈】（M6 票 14）：牌面是"当你受到 **1 点** 伤害后"（§十九 L2036），
    # 故与界【刚烈】同档：`per_point=True`（标准版是"当你受到伤害后"，每次伤害事件一次）。
    # 条件 / 流水线 / 缓存的逐点键（``fankui_step_key``）全与标准版共用。
    DamageAfterSkill("jie_simayi", "fankui", True, _fankui_condition, None, FANKUI_FLOW),
    # 界夏侯惇【刚烈】（M6 票 08）：整句重写——逐点触发（"受到 1 点伤害后"），
    # 红色对来源造成 1 点伤害、黑色弃置来源的一张牌。
    DamageAfterSkill(
        "jie_xiahoudun",
        "ganglie",
        True,
        _jie_ganglie_condition,
        _jie_ganglie,
        requirement="受伤后判定：红色对伤害来源造成 1 点伤害，黑色弃置伤害来源的一张牌",
    ),
    # 界郭嘉【遗计】（M6 票 15）：整句重写——逐点触发，摸两张后从**手牌**里分配。
    # 触发条件与标准版同源（受伤的是持有者），故共用 :func:`_yiji_condition`。
    DamageAfterSkill(
        "jie_guojia",
        "yiji",
        True,
        _yiji_condition,
        _jie_yiji,
        requirement="受到 1 点伤害后摸两张牌，然后可以将至多两张手牌分配给其他角色",
        step_requirements={
            "card": "选择要交给其他角色的手牌（至多两张）",
            "give": "选择将这张牌交给哪名其他角色",
        },
    ),
    # 界李典【忘隙】（M6 票 15）：**双向**触发——造成 / 受到 1 点伤害后各一次（两处
    # "1 点"都是逐点，故 ``per_point=True`` 在两侧都成立）。两侧的"我是谁"判据相反，
    # 故 ``timings`` 逐时机给条件；回调共用同一份（方向由帧里的 source / target 读出）。
    # ``condition`` 是声明形状的必填位；``timings`` 在场时派发只读它里面的两条。
    DamageAfterSkill(
        "jie_lidian",
        "wangxi",
        True,
        _wangxi_take_condition,
        _wangxi,
        requirement="造成或受到 1 点伤害后，摸两张牌并交给该角色其中一张牌",
        step_requirements={"card": "选择交给该角色的一张牌"},
        timings=(
            (Timing.DAMAGE_AFTER_DEAL, _wangxi_deal_condition),
            (Timing.DAMAGE_AFTER_TAKE, _wangxi_take_condition),
        ),
    ),
    # 界华雄【耀武】（M6 票 18）：锁定技，B 族第一条不询问的声明（``locked=True``）。
    DamageAfterSkill(
        "jie_huaxiong",
        "yaowu",
        False,
        _yaowu_condition,
        _yaowu,
        requirement="锁定技：受到牌造成的伤害时，红色则伤害来源摸一张，否则你摸一张",
        locked=True,
    ),
    # 界公孙瓒【趫猛】（M6 票 18）：【杀】造成伤害后选其区域里的一张牌（§十九 L2226–L2228）。
    DamageAfterSkill(
        "jie_gongsunzan",
        "qiaomeng",
        False,
        _qiaomeng_condition,
        None,
        QIAOMENG_FLOW,
        requirement=(
            "使用【杀】对一名角色造成伤害后，选择其区域里的一张牌："
            "为坐骑牌则获得之，否则弃置之"
        ),
        step_requirements={"card": "选择受伤角色区域里的一张牌"},
    ),
    # 界吕布【利驭】（M6 票 18）：**我用【杀】**对另一个角色造成伤害后获得其区域内一张牌
    # （§十九 L2208–L2209）。"每次伤害事件一次"（牌面没有"1 点"）⇒ ``per_point=False``。
    DamageAfterSkill(
        "jie_lvbu",
        "liyu",
        False,
        _liyu_condition,
        None,
        LIYU_FLOW,
        requirement=(
            "使用【杀】对一名角色造成伤害后，获得其区域内的一张牌："
            "不为装备牌则其摸一张牌，否则视为你对该角色指定的另一名其他角色使用一张【决斗】"
        ),
        step_requirements={"card": "获得受伤角色区域内的一张牌"},
    ),
    # 神曹操移动版【归心】（lin-batch 票 01）：逐点 → 逐座位（座位序）从三区自选 1 张 →
    # 翻面。**手写回调**（变长逐座位循环，没有 ``SkillFlow``），与【遗计】同一条手写形。
    # 范围 = **每名**其他角色（不是"一名"）⇒ 没有 ``PickSeats`` 步；「区域里」= 三区。
    DamageAfterSkill(
        "shen_caocao_mobile",
        "guixin",
        True,
        _guixin_condition,
        _guixin,
        requirement="当你受到1点伤害后，你可以获得每名其他角色区域里的一张牌，然后你翻面。",
        step_requirements={"card": "获得一名其他角色区域里的一张牌"},
    ),
    # 曹丕移动版【放逐】（lin-batch2 票 01）：**非逐点** → 选一名其他存活角色 →
    # **先**翻面（toggle）**后**该角色摸 X 张（X = 持有者已损失体力值，发动时定格）。
    # 档位 = 【反馈】的 `per_point=False` + 单步 flow；骨架 = 【节命】的单步 `PickSeats`。
    DamageAfterSkill(
        "caopi_mobile",
        "fangzhu",
        False,
        _fangzhu_condition,
        None,
        FANGZHU_FLOW,
        requirement=FANGZHU_REQUIREMENT,
        step_requirements={"seat": "选择一名其他角色"},
    ),
)
"""B 族技能的逐字声明（票 05 §要做什么 2 的表 + M6 票 01 / 08 / 14 / 15 的界版）。"""

_REQUIREMENTS: dict[str, str] = {
    "jianxiong": "获得造成伤害的牌",
    "fankui": "获得伤害来源的一张牌",
    "yiji": "观看牌堆顶两张牌，然后交给任意角色",
    "ganglie": "受伤后判定：非红桃则伤害来源弃两张手牌或受到 1 点伤害",
}
"""B 族四条技能的"需要做什么"文案（item 4）；键 = 短 id，与声明同处。"""

_STEP_REQUIREMENTS: dict[str, dict[str, str]] = {
    # 票 07：走步骤流水线的被动技逐步文案。键的取法同 A 族 —— ``SELECT_CARDS`` 恒读
    # ``"card"``（见 :func:`~sgs_web.projection._step_requirement`）。【反馈】只有一步。
    "fankui": {"card": "获得伤害来源的一张牌"},
    # 票 11：【遗计】有意保持**手写**（裁定见票的 ``## Answer``），但逐步文案与是否走流水线
    # 无关 —— 两步各有各的问法。选牌步键 = ``"card"``；"给谁"步的询问 ``skill_id`` 是
    # ``yiji:give:<点号>:<轮号>``（逐轮缓存键），投影剥掉末尾序号段后命中 ``"give"``。
    # 票 15：选牌步**一次可选两张**（用户口径），故文案写"一至两张"；"给谁"步承认复数的
    # "这些牌"——一次两张与一张一交共用它（只选一张时读起来也通顺）。
    "yiji": {"card": "从亮出的牌里选择一至两张", "give": "选择将这些牌交给哪名角色"},
    # M6 票 01【刚烈】同样是手写（判定 → 选项 → 选牌三步），逐步文案按询问自带的标识取键：
    # ``SELECT_OPTION`` 的 ``skill_id`` = ``ganglie:choice`` ⇒ ``"choice"``；
    # ``SELECT_CARDS`` ⇒ ``"card"``。
    "ganglie": {
        "choice": "选择弃置两张手牌或受到 1 点伤害",
        "card": "选择弃置的两张手牌",
    },
}
"""**多步被动技**逐步的"需要做什么"（票 07）；键 = 短 id → 步骤名 → 文案。"""


def damage_after_skill_definitions() -> tuple[SkillDef, ...]:
    """B 族技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    主体（:data:`DAMAGE_AFTER_SKILLS`）是**受伤侧**的声明表：``carrier = HERO`` ⇒ 优先级档由
    载体派生；``skill_type = NORMAL``（都不是主公技）；``per_point`` / ``timings`` 由声明给出
    （【遗计】逐点；【忘隙】在造成 / 受到两侧都逐点）。``handler`` 与 ``flow`` 恰有其一
    （【刚烈】【遗计】（标准 / 界）是手写多轮回调，其余走流水线）；其中【耀武】是唯一的锁定技
    （``locked=True`` ⇒ 不问是否发动）。

    另有**一条独立声明** :data:`KUANGGU_DEF`（风包魏延【狂骨】）：它挂在造伤侧（
    ``DAMAGE_AFTER_DEAL``），且两个时机要跑不同回调（受伤害时定格距离 / 造伤后逐点回复），
    表达不了 :class:`DamageAfterSkill` 的"一条条件 + 一个回调挂到若干时机"的形状，故按整份
    ``SkillDef`` 声明后并进来——仍是既有原语，不新增机制。
    """
    declared = tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=skill.locked,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=tuple(
                Hook(
                    timing,
                    skill.handler,
                    condition,
                    per_point=skill.per_point,
                    flow=skill.flow,
                )
                for timing, condition in (
                    skill.timings or ((Timing.DAMAGE_AFTER_TAKE, skill.condition),)
                )
            ),
            requirement=skill.requirement or _REQUIREMENTS[skill.short_id],
            step_requirements=(
                skill.step_requirements or _STEP_REQUIREMENTS.get(skill.short_id)
            ),
        )
        for skill in DAMAGE_AFTER_SKILLS
    )
    return (*declared, KUANGGU_DEF, JIE_KUANGGU_MOBILE_DEF, SHIBEI_DEF)
