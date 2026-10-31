r"""U 族：使用牌时触发（M6 票 02）——黄月英【集智】。

规则原文：§九 L997「当你使用一张非延时类锦囊牌时，你可以摸一张牌」——**没有**"非转化"；
界版（§十九 L2104–L2105）「当你使用一张非转化的锦囊牌时，你可以摸一张牌」——**没有**
"非延时类"，另追加「若此牌为基本牌，你可以弃置此牌，令你本回合的手牌上限 +1」——本模块
经 :data:`JIE_JIZHI` 落地（M6 票 08）。故两版的牌面判据**不同**：

- **标准版**只排除**延时类**（``behavior.is_delayed``：乐不思蜀 / 闪电 / 兵粮寸断）；
  **转化锦囊照常触发**（票 02 曾把"非转化"写进标准版口径，M6 评审按 §九 L997 订正）；
- **界版**只排除**转化牌**（``CardRef.is_virtual`` 且 ``virtual_sources`` 非空）——§十九
  L2104 的"非转化的"是它**唯一**的排除；**延时类非转化锦囊照常触发**（乐不思蜀 / 兵粮寸断
  / 闪电；M6 评审按界版牌面无"非延时类"订正）。纯虚拟牌（没有来源实体牌）**不**在排除之列：
  §十六 16.2 明写"视为使用"触发"当你使用牌时"类技能，排除词也只是"转化"。

**"此牌" = 集智摸的那一张**（spec D20）：规则书 L2108–L2109 的注记把"此牌"读成被使用的
锦囊、与前半句"使用锦囊牌"冲突（锦囊牌不是基本牌），是误读（`06`（§5「程序化约定与偏离一手」） 登记）。

牌名大类读数据文件的 :func:`sgs.data.card_category`（唯一真源），不抄牌名白名单：将来
新增锦囊时这里不需要改。

**挂点 = ``USE_ON_USE``**（L4.2）：与"使用"同一帧，先于该牌的 C 段结算，故摸到的那张
排在牌自身效果摸到的牌之前（测试钉住手牌序）。**零步流水线**：非锁定技 ⇒ 派发环先问
"是否发动"，答发动才跑 effect；摸牌在帧私有标记下只做一次（`:data:`_DRAWN_KEY`，
`CODING_STANDARDS.md` §3：同一帧里后跑的其他钩子挂起会让本回调重入）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Category, SkillType, card_category, hero_skill_id
from sgs.effects import (
    CarrierKind,
    behavior_for,
    is_slash,
    targets_within_attack_range,
)
from sgs.resolution import Control, Frame
from sgs.state import (
    CardRef,
    GameState,
    MoveReason,
    Pass,
    SelectionSpec,
    Slot,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .catalog import (
    MATCH_ACTOR_KEY,
    MATCH_OWNER_KEY,
    Hook,
    SkillDef,
    mark_round_skill_used,
    round_skill_uses,
)
from .flow import SkillFlow, zero_step_effect

__all__ = [
    "CONGTAO",
    "CONGTAO_PER_ROUND",
    "JIE_JIZHI",
    "JIE_JIZHI_FLOW",
    "JIZHI",
    "JIZHI_FLOW",
    "YAJIAO",
    "use_after_skill_definitions",
]

JIZHI = hero_skill_id("huangyueying", "jizhi")
"""黄月英【集智】的全限定技能 id。"""

JIE_JIZHI = hero_skill_id("jie_huangyueying", "jizhi")
"""界黄月英【集智】的全限定技能 id（§十九 L2104–L2105：摸一张 + 基本牌的 +1 分支）。"""

_DRAWN_KEY = "jizhi_drawn"
"""帧私有：本帧已经因【集智】摸过牌（重入不再摸；不进记录流）。"""

_JIE_DRAWN_KEY = "jie_jizhi_drawn"
"""帧私有：界【集智】本帧已经摸过那张牌。"""

_JIE_CARD_KEY = "jie_jizhi_card"
"""帧私有：界【集智】摸到的那张牌的 ``card_id``（"此牌" = 这一张，D20）。"""

_JIE_CHOICE_KEY = "jie_jizhi_choice"
"""帧私有：界【集智】"要不要弃置它换 +1"的应答（重入不重问）。"""

_JIE_DISCARDED_KEY = "jie_jizhi_discarded"
"""帧私有：这张牌已经弃置（搬牌可能内联挂起，重入不重搬）。"""

_JIE_BONUS_KEY = "jie_jizhi_bonus"
"""帧私有：+1 已经落过（搬牌挂起后的续跑不重复加）。"""

_JIE_LIMIT_BONUS = 1
"""界【集智】弃置基本牌换来的手牌上限增量。"""

_JIE_LIMIT_ASK = "jie_jizhi:limit"
"""界【集智】"是否弃置此牌换 +1"的询问标识（选项闭集 = 一个 ``discard`` + ``Pass``）。"""

_JIE_LIMIT_OPTION = "discard"
"""界【集智】那个唯一选项的取值。"""


def _jizhi_eligible(
    state: GameState,
    card: CardRef | None,
    *,
    exclude_delayed: bool,
    exclude_transformed: bool,
) -> bool:
    """这张牌算不算"使用了一张锦囊牌"（条件求值的唯一牌面判据）。

    两个开关是**两版牌面的全部差别**，各由一版传入、另一版不传：

    - ``exclude_delayed`` = §九 L997 的"**非延时类**锦囊牌"——只标准版有；
    - ``exclude_transformed`` = §十九 L2104 的"**非转化的**锦囊牌"——只界版有。

    故标准版对转化锦囊照常触发（不排除转化）、界版对延时类非转化锦囊照常触发
    （不排除延时）。三段都不读牌名白名单：大类读内容包、延时性读行为表、转化性读虚拟牌
    来源集合。
    """
    if card is None:
        return False
    if exclude_transformed and card.is_virtual and card.virtual_sources:
        return False
    if exclude_delayed and behavior_for(card).is_delayed:
        return False
    return card_category(state.pack_id, card.type) is Category.TRICK


def _jizhi_owner_used_it(frame: Frame) -> bool:
    """触发条件的前半句：**使用者是技能持有者本人**。

    持有者读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（条件求值期由派发环临时写入），
    使用者读 ``frame.data["source"]``（使用帧的既有载荷）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    return owner is not None and frame.data.get("source") == owner


def _jizhi_condition(host: Any, frame: Frame) -> bool:
    """标准【集智】（§九 L997）：本人使用非延时类锦囊 —— **转化锦囊照样触发**。"""
    if not _jizhi_owner_used_it(frame):
        return False
    return _jizhi_eligible(
        host.state,
        frame.data.get("card"),
        exclude_delayed=True,
        exclude_transformed=False,
    )


def _jie_jizhi_condition(host: Any, frame: Frame) -> bool:
    """界【集智】（§十九 L2104–L2105）：本人使用**非转化的**锦囊 —— 延时类照样触发。"""
    if not _jizhi_owner_used_it(frame):
        return False
    return _jizhi_eligible(
        host.state,
        frame.data.get("card"),
        exclude_delayed=False,
        exclude_transformed=True,
    )


def _jizhi(host: Any, frame: Frame) -> Control:
    """效果：摸一张牌（"是否发动"已由派发环问过）。

    帧私有 ``jizhi_drawn`` 挡住重入：同一使用帧上若另一条钩子提问挂起，``run_flow`` 会
    从头重跑 effect，没有这个守卫就会多摸。
    """
    if frame.data.get(_DRAWN_KEY):
        return None
    frame.data[_DRAWN_KEY] = True
    host.draw_for_hand(frame, frame.data["skill_owner"], 1)
    return None


def _jie_jizhi(host: Any, frame: Frame) -> Control:
    """界【集智】：摸一张；若**摸到的**那张是基本牌，可以弃置它换本回合手牌上限 +1。

    三段、每段一个帧私有游标（与【刚烈】同一范式：搬牌 / 询问都可能挂起，重入必须既不
    重做也不漏做）：

    1. **摸一张**（与标准版同句）——记住摸到的牌：摸牌原语把牌追加进手牌尾，故画完之后
       的 ``hand[-1]`` 就是"此牌"（D20）。牌堆短摸 ⇒ 平局，技能到此为止；
    2. **第二个"可以"**：只有"此牌为**基本牌**"才发询问（``SELECT_OPTION``，可选 = 一条
       ``Pass``）；非基本牌连问都不问；
    3. **弃置 + 加式**：牌进弃牌堆（``DISCARD``），然后给持有者记 +1（
       :meth:`~sgs.engine.Engine.add_hand_limit_bonus`）。加式是 D9 的一次性效果 ⇒ 住座位、
       回合末过期、不随【集智】失去 / 失效撤回。
    """
    if frame.data.get(_JIE_BONUS_KEY):
        return None
    state = host.state
    owner = frame.data["skill_owner"]
    if not frame.data.get(_JIE_DRAWN_KEY):
        frame.data[_JIE_DRAWN_KEY] = True
        # 摸牌也算「获得牌」（票 24 的 Q1）：这一张可能触发别人的【清俭】并挂起（票 25
        # 的嵌套挂起）。先把"此牌 = hand[-1]"定格，再原样上抛——重入不会再摸，若这里
        # 先返回而没写 ``_JIE_CARD_KEY``，重入会 KeyError；若忽略挂起继续发问，待答询问
        # 会被那个问句覆盖。
        control = host.draw_for_hand(frame, owner, 1)
        if not control:
            return None
        frame.data[_JIE_CARD_KEY] = state.player(owner).hand[-1]
        if control == "stay":
            return "stay"
    drawn = state.card(int(frame.data[_JIE_CARD_KEY]))
    if card_category(state.pack_id, drawn.type) is not Category.BASIC:
        frame.data[_JIE_BONUS_KEY] = True
        return None
    answer = frame.data.get(_JIE_CHOICE_KEY)
    if answer is None:
        answer = host.ask_option(
            frame,
            owner,
            Timing.USE_ON_USE,
            skill_id=_JIE_LIMIT_ASK,
            options=(_JIE_LIMIT_OPTION,),
            optional=True,
        )
        if host.suspended(answer):
            return "stay"
        frame.data[_JIE_CHOICE_KEY] = answer
    if isinstance(answer, Pass):
        frame.data[_JIE_BONUS_KEY] = True
        return None
    if not frame.data.get(_JIE_DISCARDED_KEY):
        frame.data[_JIE_DISCARDED_KEY] = True
        if (
            host.move_in_frame(
                frame,
                (drawn,),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=owner,
                actor=owner,
            )
            == "stay"
        ):
            return "stay"
    frame.data[_JIE_BONUS_KEY] = True
    host.add_hand_limit_bonus(owner, _JIE_LIMIT_BONUS)
    return None


JIZHI_FLOW = SkillFlow(name="jizhi", steps=(), effect=zero_step_effect(_jizhi))
"""【集智】的零步流水线：[是否发动] → 摸一张牌（无步骤）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例，
并拒绝"注册了但不在模块里"的流水线。
"""


JIE_JIZHI_FLOW = SkillFlow(name="jie_jizhi", steps=(), effect=zero_step_effect(_jie_jizhi))
"""界【集智】的零步流水线：[是否发动] → 摸一张 →（基本牌时）可选弃置换 +1。"""


# ---------------------------------------------------------------------------
# 界赵云【涯角】（M6 票 16）：回合外使用 / 打出手牌时亮出牌堆顶一张，按类别分叉
#
# §十九 L2085–L2088：
#
#     涯角：当你于回合外使用或打出手牌时，你可以亮出牌堆顶的一张牌，然后若这两张牌类别
#           相同，则你将之交给一名角色或放回牌堆顶；否则你可以弃置一名攻击范围内包含你的
#           其他角色区域里的一张牌，并将亮出的牌放回牌堆顶。
#
# 三段口径（`06`（§5「程序化约定与偏离一手」） 的裁定）：
#
# - **"这两张牌类别相同"** = 被使用 / 打出的那张（转化牌按**产物牌型**）与亮出的那张的
#   **大类**（基本 / 锦囊 / 装备）相同——不是牌名相同，也不是花色 / 颜色；
# - **"使用或打出手牌"** = 来源牌里有手牌。判据取引擎在使用 / 打出帧上记下的
#   ``from_hand``（``start_use`` / ``play_card`` 在牌离开手牌前写入）：装备区转化出来的
#   牌、纯虚拟牌（八卦阵的视为【闪】）都不触发；
# - **"放回牌堆顶"** = ``peek_deck`` 只看不移，故"放回"是无动作；只有"交给一名角色"与
#   "弃置一张牌"会真实搬牌。
#
# 手写多轮（同【忘隙】的姿态）：类别分叉后的步骤数不同（同类别是"给谁 / 放回"二选一，
# 不同类别是"弃不弃 → 弃谁的"），``SkillFlow`` 的静态步骤表达不了条件分支。
# ---------------------------------------------------------------------------

YAJIAO = hero_skill_id("jie_zhaoyun", "yajiao")
"""界赵云【涯角】的全限定技能 id（§十九 L2085–L2088）。"""

_YAJIAO_OPTIONS_SAME: tuple[str, ...] = ("yajiao_give", "yajiao_top")
"""类别相同：交给一名角色 / 放回牌堆顶（**必选**，牌面的"或"是两个动作，不是"可以不做"）。"""

_YAJIAO_OPTION_GIVE = "yajiao_give"

_YAJIAO_OPTION_DISCARD = "yajiao_discard"
"""类别不同：弃置一张牌（**可选**；``Pass`` = 不弃、直接放回）。"""

_YAJIAO_ASK_BRANCH = "yajiao:branch"
_YAJIAO_ASK_GIVE_SEAT = "yajiao:give_seat"
_YAJIAO_ASK_DISCARD_SEAT = "yajiao:discard_seat"
_ASK_YAJIAO_DISCARD = "hero.jie_zhaoyun.yajiao.discard"
"""手写询问位置：从目标区域里挑一张弃置（票 22 的 ``ask_selection`` owner 约定）。"""

_YAJIAO_REVEALED_KEY = "yajiao_revealed"
"""帧私有：亮出的那张牌 id（续跑不重看牌堆）。"""

_YAJIAO_BRANCH_KEY = "yajiao_branch"
"""帧私有：类别相同分支的选择（交给一名角色 / 放回牌堆顶）。"""

_YAJIAO_DISCARD_KEY = "yajiao_discard_choice"
"""帧私有：类别不同分支"弃不弃"的应答（``Pass`` → 空串）。"""

_YAJIAO_SEAT_KEY = "yajiao_seat"
"""帧私有：选中的"交给谁" / "弃谁的牌"座位。"""

_YAJIAO_GIVEN_KEY = "yajiao_given"
"""帧私有：亮出的牌已经交给目标（续跑不重搬）。"""

_YAJIAO_DISCARDED_KEY = "yajiao_discarded"
"""帧私有：目标的那张牌已经弃置（续跑不重搬）。"""


def _yajiao_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**持有者自己**于**回合外**使用 / 打出的是**手牌**。

    持有者读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（条件求值期由派发环写）；
    "手牌"读引擎在帧上记下的 ``from_hand``（见 :meth:`~sgs.engine.Engine.start_use`）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    if not frame.data.get("from_hand"):
        return False
    return host.state.turn_seat != owner


def _yajiao_same_category(state: GameState, used: CardRef | None, revealed: CardRef) -> bool:
    """两张牌的**大类**是否相同（基本 / 锦囊 / 装备）。"""
    if used is None:
        return False
    return card_category(state.pack_id, used.type) is card_category(
        state.pack_id, revealed.type
    )


def _yajiao_discard_seats(state: GameState, owner: int) -> tuple[int, ...]:
    """能弃牌的对象：**其他存活角色**中"攻击范围内包含你"且区域里**有牌**的那些。

    "攻击范围内包含你"不能只看自己的攻击范围：攻击范围是**看的人**的属性（武器不同），
    故这里从候选座位出发，问它能否触及 ``owner``（与【借刀杀人】同一个
    :func:`~sgs.effects.targets_within_attack_range` 判据）。
    """
    return tuple(
        player.seat
        for player in state.players
        if player.alive
        and player.seat != owner
        and owner in targets_within_attack_range(state, player.seat)
        and (player.hand or player.equip or player.judge)
    )


def _yajiao_seat_options(state: GameState, seats: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(f"seat:{seat}" for seat in seats)


def _yajiao_seat(answer: Any) -> int:
    return int(str(answer.choice).split(":", 1)[1])


def _yajiao(host: Any, frame: Frame) -> Control:
    """【涯角】的提交点：亮一张 → 按类别分叉 → （同类别）交给一人 / 放回顶，（不同类别）
    可选弃一张 → 放回顶。

    每段一个帧私有游标：亮牌 / 交给 / 弃置都可能内联派发"失去牌后"等钩子而挂起，
    重入必须既不重做也不漏做。
    """
    state = host.state
    owner = frame.data["skill_owner"]
    if _YAJIAO_REVEALED_KEY not in frame.data:
        revealed = host.peek_deck(frame, 1)
        if not revealed:
            return None
        frame.data[_YAJIAO_REVEALED_KEY] = revealed[0].card_id
        # "亮出"= 公开事实（与【火攻】/界【裸衣】同一个记录面），牌本身仍留在牌堆顶。
        host.reveal_card(frame, owner, revealed[0])
    revealed = state.card(int(frame.data[_YAJIAO_REVEALED_KEY]))
    used = frame.data.get("card")
    if _yajiao_same_category(state, used, revealed):
        branch = frame.data.get(_YAJIAO_BRANCH_KEY)
        if branch is None:
            answer = host.ask_option(
                frame,
                owner,
                Timing.USE_ON_USE,
                skill_id=_YAJIAO_ASK_BRANCH,
                options=_YAJIAO_OPTIONS_SAME,
                optional=False,
            )
            if host.suspended(answer):
                return "stay"
            branch = str(answer.choice)
            frame.data[_YAJIAO_BRANCH_KEY] = branch
        if branch != _YAJIAO_OPTION_GIVE:
            return None  # 放回牌堆顶：牌本来就在顶上，无动作
        target = frame.data.get(_YAJIAO_SEAT_KEY)
        if target is None:
            seats = tuple(player.seat for player in state.players if player.alive)
            answer = host.ask_option(
                frame,
                owner,
                Timing.USE_ON_USE,
                skill_id=_YAJIAO_ASK_GIVE_SEAT,
                options=_yajiao_seat_options(state, seats),
                optional=False,
            )
            if host.suspended(answer):
                return "stay"
            target = _yajiao_seat(answer)
            frame.data[_YAJIAO_SEAT_KEY] = target
        if frame.data.get(_YAJIAO_GIVEN_KEY):
            return None
        frame.data[_YAJIAO_GIVEN_KEY] = True
        control = host.move_in_frame(
            frame,
            (revealed,),
            to_zone=ZoneRef(Zone.HAND, int(target)),
            reason=MoveReason.PUT,
            owner=int(target),
            actor=owner,
        )
        return "stay" if control == "stay" else None
    choice = frame.data.get(_YAJIAO_DISCARD_KEY)
    if choice is None:
        answer = host.ask_option(
            frame,
            owner,
            Timing.USE_ON_USE,
            skill_id=f"{_YAJIAO_ASK_BRANCH}:discard",
            options=(_YAJIAO_OPTION_DISCARD,),
            optional=True,
        )
        if host.suspended(answer):
            return "stay"
        choice = "" if isinstance(answer, Pass) else str(answer.choice)
        frame.data[_YAJIAO_DISCARD_KEY] = choice
    if not choice:
        return None  # 不弃：亮出的牌放回牌堆顶（仍在顶上，无动作）
    target = frame.data.get(_YAJIAO_SEAT_KEY)
    if target is None:
        seats = _yajiao_discard_seats(state, owner)
        if not seats:
            return None
        answer = host.ask_option(
            frame,
            owner,
            Timing.USE_ON_USE,
            skill_id=_YAJIAO_ASK_DISCARD_SEAT,
            options=_yajiao_seat_options(state, seats),
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        target = _yajiao_seat(answer)
        frame.data[_YAJIAO_SEAT_KEY] = target
    if frame.data.get(_YAJIAO_DISCARDED_KEY):
        return None
    picked = host.ask_selection(
        frame,
        owner=_ASK_YAJIAO_DISCARD,
        key="yajiao:discard",
        actor=owner,
        timing=Timing.USE_ON_USE,
        spec=SelectionSpec(
            zones=(Zone.HAND, Zone.EQUIP, Zone.JUDGE), total=(1, 1), optional=False
        ),
        # 别人的手牌是隐藏槽（只有位置、没有牌面），装备 / 判定区照旧可见。
        build_face=lambda: host.selection_face(
            int(target), (Zone.HAND, Zone.EQUIP, Zone.JUDGE), reveal_hand=False
        ),
    )
    if host.suspended(picked):
        return "stay"
    if not picked:
        return None
    frame.data[_YAJIAO_DISCARDED_KEY] = True
    control = host.move_in_frame(
        frame,
        state.refs(picked),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=int(target),
        actor=owner,
    )
    return "stay" if control == "stay" else None


def _yajiao_hooks() -> tuple[Hook, Hook]:
    """【涯角】的两条挂接（使用 / 打出），共用同一条条件与同一份手写回调。"""
    return (
        Hook(Timing.USE_ON_USE, condition=_yajiao_condition, handler=_yajiao),
        Hook(Timing.PLAY_ON_PLAY, condition=_yajiao_condition, handler=_yajiao),
    )


# ---------------------------------------------------------------------------
# 韬·刘备【从讨】（自制包）：攻击范围内的其他角色成为【杀】的目标时，观看牌堆顶两张
# ---------------------------------------------------------------------------

CONGTAO = hero_skill_id("tao_liubei", "congtao")
"""韬·刘备【从讨】的全限定技能 id。

牌面（用户 `技能.txt`）：**每轮限2次**，当你攻击范围内的其它角色成为【杀】的目标时，
你可以观看牌堆顶 2 张牌，将其中一张交给【杀】的使用者，另一张加入弃牌堆或放回牌堆顶。

两处与既有技能不同源的读法（用户 2026-10-31 裁定）：

- **逐目标触发**："…其它角色成为【杀】的目标时"是 C2 的逐目标派发，多目标【杀】里每个
  在范围内的目标各触发一次（受每轮 2 次封顶）；
- **刘备自己用【杀】也算**："其它角色"约束的是**成为目标的那一个**，不约束使用者 ⇒
  此时"交给【杀】的使用者"= 交给自己（账本 L791「交给一名角色含自己」的同向口径）。
"""

CONGTAO_COUNT = 2
"""【从讨】观看的张数（牌面"牌堆顶2张牌"）。"""

CONGTAO_PER_ROUND = 2
"""【从讨】每轮次数上限（牌面"每轮限2次"；「轮」读账本 L754 的引擎状态口径）。"""

_CONGTAO_OPTION_DISCARD = "congtao_discard"
_CONGTAO_OPTION_DECK_TOP = "congtao_deck_top"
_CONGTAO_OPTIONS: tuple[str, ...] = (_CONGTAO_OPTION_DISCARD, _CONGTAO_OPTION_DECK_TOP)
"""另一张的去处：置入弃牌堆 / 放回牌堆顶（两项恒可执行、**不可放弃**——牌面没有第二个
"可以"，发动之后两张牌都必须有去向）。"""

_ASK_CONGTAO_PICK = "hero.tao_liubei.congtao.pick"
"""手写询问位置：【从讨】从观看的两张里选一张交出去的那次 ``ask_selection``。"""


def _congtao_keys(trigger: int) -> tuple[str, str, str, str]:
    """**这一次触发**（这一次成为目标的那名角色）的四条帧私有键。

    逐目标派发意味着同一个 USE 帧里可以有多次【从讨】（多目标【杀】）：观看、已交出的牌、
    另一张的去处这三个游标都必须带上这次触发的座位，否则第二次触发会读到第一次的缓存
    （不重看牌堆、不重给牌）。与【遗计】按伤害点号分桶同一个理由。
    """
    return (
        f"congtao:{trigger}:seen",
        f"congtao:{trigger}:given",
        f"congtao:{trigger}:leftover",
        f"congtao:{trigger}:done",
    )


def _congtao_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**这一次成为目标的别人**在我的攻击范围内、这一刀是【杀】、本轮还没用满。

    两个"这一次"分别来自派发环写的两个帧私有槽：:data:`MATCH_OWNER_KEY`（我是谁）与
    :data:`MATCH_ACTOR_KEY`（这一次落在谁身上）。缺任一条，条件就会对每个存活玩家成立
    ——与【流离】票 13 的报障同一错型。

    "付不起"的三档都在这里挡掉（``edge-cases.md`` G1）：不在攻击范围 / 本轮已满 2 次 /
    牌堆与弃牌堆一张都没有（没有可观看的牌）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    trigger = frame.data.get(MATCH_ACTOR_KEY)
    if owner is None or trigger is None or trigger == owner:
        return False
    card = frame.data.get("card")
    # 【杀】按**家族**判据（火 / 雷 / 转化 / 虚拟同族），与【流离】同一条。
    if card is None or not is_slash(behavior_for(card)):
        return False
    state = host.state
    if trigger not in targets_within_attack_range(state, owner):
        return False
    if round_skill_uses(state, owner, CONGTAO) >= CONGTAO_PER_ROUND:
        return False
    return bool(state.deck or state.discard)


def _congtao_settle(state: GameState, owner: int, frame: Frame, done_key: str) -> None:
    """【从讨】一次触发的收尾：落"本次触发已完成"位并记一次**每轮已发动**。

    计数**必须落在收尾**、不能落在"观看那一刻"：条件在每次派发前都会重跑，而挂起续跑是
    "重跑同一个步骤"——若观看时就把本轮次数 +1，续跑时条件读到``本轮已满``，这一钩子会
    不再匹配、把已经问过一半的那次触发**静默丢掉**（实机：多目标【杀】的第二张牌交不出去）。
    与限定技/觉醒技"执行完才落位"是同一条纪律。
    """
    frame.data[done_key] = True
    mark_round_skill_used(state, owner, CONGTAO)


def _congtao(host: Any, frame: Frame) -> Control:
    """【从讨】提交点：观看牌堆顶两张（仅自己可见）→ 选一张交给使用者 → 另一张二选一。

    两段搬牌各带帧内游标（``given`` / ``done``）：搬牌会内联派发"失去牌 / 获得牌"一类钩子，
    挂起后的续跑既不重做也不漏做。``deck_top`` 档**没有搬牌**——那一张本来就在牌堆顶上
    （另一张被取走之后它就是顶），与【涯角】"放回牌堆顶"同一个落点。
    """
    state = host.state
    owner = int(frame.data["skill_owner"])
    trigger = int(frame.data[MATCH_ACTOR_KEY])
    user = frame.data.get("source")
    if user is None:
        return None
    seen_key, given_key, leftover_key, done_key = _congtao_keys(trigger)
    if frame.data.get(done_key):
        return None
    seen: tuple[CardRef, ...] | None = frame.data.get(seen_key)
    if seen is None:
        seen = host.peek_deck(frame, CONGTAO_COUNT)
        frame.data[seen_key] = seen
    if not seen:
        return None
    picked_id = frame.data.get(given_key)
    if picked_id is None:
        picked = host.ask_selection(
            frame,
            owner=_ASK_CONGTAO_PICK,
            key=f"congtao:pick:{trigger}",
            actor=owner,
            timing=Timing.USE_BECOME_TARGET,
            spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1), optional=False),
            build_face=lambda: (
                tuple(Slot(ref=ZoneRef(Zone.HAND, owner), card=item) for item in seen),
                tuple(item.card_id for item in seen),
            ),
            revealed=seen,
        )
        if host.suspended(picked):
            return "stay"
        if not picked:
            return None
        picked_id = int(picked[0])
        frame.data[given_key] = picked_id
        control = host.move_in_frame(
            frame,
            (state.card(picked_id),),
            to_zone=ZoneRef(Zone.HAND, int(user)),
            reason=MoveReason.PUT,
            owner=int(user),
            actor=owner,
        )
        if control == "stay":
            return "stay"
    leftover = tuple(card for card in seen if card.card_id != int(picked_id))
    if not leftover:
        # 没有"另一张"：这一段就是收尾（计数在收尾处落位，见下）。
        _congtao_settle(state, owner, frame, done_key)
        return None
    rest = leftover[0]
    choice = frame.data.get(leftover_key)
    if choice is None:
        answer = host.ask_option(
            frame,
            owner,
            Timing.USE_BECOME_TARGET,
            skill_id=f"congtao:leftover:{trigger}",
            options=_CONGTAO_OPTIONS,
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        choice = str(answer.choice)
        frame.data[leftover_key] = choice
    if choice == _CONGTAO_OPTION_DISCARD:
        control = host.move_in_frame(
            frame,
            (rest,),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    _congtao_settle(state, owner, frame, done_key)
    return None


def use_after_skill_definitions() -> tuple[SkillDef, ...]:
    """U 族**四条**技能的 :class:`~sgs.skills.catalog.SkillDef`（非锁定；【涯角】挂使用 / 打出两条钩子）。

    标准 / 界**各自一条触发条件**，因为两版牌面不同：§九 L997 的标准版只排除延时类
    （转化锦囊照常触发），§十九 L2104–L2105 的界版只排除转化牌（延时类照常触发）：
    :func:`_jizhi_eligible` 的两个开关各传一个。提交点仍各一条零步流水线：界版只多
    "基本牌弃置换 +1"一段。

    【从讨】是 U 族的第四条（自制包韬·刘备）：挂 ``USE_BECOME_TARGET``，``per_round_limit``
    置真让轮次变化进记录流（账本 L754 的"轮"口径只给带每轮限次的技能用）。
    """
    return (
        SkillDef(
            skill_id=CONGTAO,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.USE_BECOME_TARGET,
                    condition=_congtao_condition,
                    handler=_congtao,
                ),
            ),
            per_round_limit=True,
            requirement=(
                "每轮限2次：当你攻击范围内的其他角色成为【杀】的目标时，可以观看牌堆顶"
                "两张牌，将其中一张交给【杀】的使用者，另一张置入弃牌堆或放回牌堆顶"
            ),
        ),
        SkillDef(
            skill_id=JIZHI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.USE_ON_USE, condition=_jizhi_condition, flow=JIZHI_FLOW),),
            requirement="使用非延时类锦囊牌时，摸一张牌（转化锦囊也算）",
        ),
        SkillDef(
            skill_id=JIE_JIZHI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.USE_ON_USE,
                    condition=_jie_jizhi_condition,
                    flow=JIE_JIZHI_FLOW,
                ),
            ),
            requirement=(
                "使用非转化的锦囊牌时摸一张牌（延时类也算）；若此牌为基本牌，你可以弃置"
                "此牌，令你本回合的手牌上限 +1"
            ),
        ),
        SkillDef(
            skill_id=YAJIAO,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=_yajiao_hooks(),
            requirement=(
                "当你于回合外使用或打出手牌时，可以亮出牌堆顶一张牌：类别相同则交给一名"
                "角色或放回牌堆顶；否则可以弃置一名攻击范围内包含你的其他角色区域里的一张"
                "牌，并将亮出的牌放回牌堆顶"
            ),
        ),
    )