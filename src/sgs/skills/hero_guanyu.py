"""自制包·锋·关羽（``feng-guanyu`` 票 01；用户 2026-11-20 逐条签）：【单骑】【义云】【心汉】。

牌面（用户 ``D:\\SGS_HEROS\\自制包\\锋·关羽\\技能.txt`` 第 1–9 行，逐字）：

    锋•关羽 4/4 魏
    【单骑】：一名其它角色的准备阶段开始时，你可以将所有【黑/红】色手牌当作1张【决斗/无距离限制的火杀】对其使用，若你有“义云”，且本次转化牌数大于等于2，此牌只能被你“义云”记录的花色的牌响应
    【义云】：锁定技，每回合限一次，当你造成伤害时，展示所有手牌并记录这些牌的花色。若本次展示的牌：
        ①【均为红色】：你令1名角色执行【甲：摸2张牌/乙：回复1点体力】中的1项，若此时“义云”记录的花色均为红色，则你可以令另一名角色执行另一项
        ②【均为黑色】：你令1名角色执行【丙：本回合非锁定技能失效直到有黑色牌进入弃牌堆/丁：受到你造成的1点伤害】中的1项，若此时“义云”记录的花色均为黑色，则你可以令另一名角色执行另一项
    然后若此时记录的花色有红有黑或你没有手牌，你摸“义云”记录的花色数张牌，清空记录的花色
    【心汉】：觉醒技，准备阶段开始时，若你本局游戏累计造成伤害数与回复体力数之和大于等于6，你将势力修改为蜀。然后若你有“义云”，则可依次对不同角色执行“义云”甲乙丙丁中的1个选项（每个选项只能选1次）

读法 / 形状 / 边界全部见 ``.scratch/feng-guanyu/issues/01-feng-guanyu.md``（用户 2026-11-20
逐条签：记录 = **花色集合**、空手牌 ①② 都不触发、"你没有手牌"在执行完 ①② 后现算、
先选人后选项、先摸后清、空记录不画、另一种角色 = 除 A 外任一角色且只在本次发动内）。
三张框架件由同批的三张票落成，本模块只用它们：

- 票 03：来源张数无上界的**全集转化**（``skills.virtual.collective_sources`` /
  ``transformed_card(..., allocate=True)``）；
- 票 04：**本次使用收窄响应牌面**（``Engine.start_use(..., response_suits=)``）；
- 票 05：失效的**提前**到期（``SkillSuppressionScope.UNTIL_BLACK_DISCARD``）。

**记录住在技能实例里**（用户 2026-11-20 签的 R5：获得 / 失去时清空）：``Player.skill_state``
的实例键 + 一个 4 位花色位段（:data:`_YIYUN_SHIFT`）。位图天然满足"不记重复花色、最多 4 种"；
实例消失时 ``prune_skill_state`` 删键 ⇒ 失去即清；重新获得 = 新键 = 空；**失效**不清。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Color, Kingdom, SkillType, Suit, color_of_suit, hero_skill_id
from sgs.effects import CarrierKind, DamageNature, check_targets
from sgs.skills.awakening import awakening_skill_verdict
from sgs.skills.catalog import (
    MATCH_OWNER_KEY,
    BadgeKind,
    RECOVERED_AMOUNT_KEY,
    Hook,
    SkillDef,
    awakening_skill_awakened,
    instance_key,
    mark_skill_used_this_turn,
    resolve_instances,
    skill_used_this_turn,
)
from sgs.skills.flow import PickOption, SkillFlow
from sgs.skills.virtual import collective_sources, pure_virtual_card, transformed_card
from sgs.resolution import Tag, has_tag
from sgs.state import (
    FrameKind,
    GameState,
    Pass,
    SkillSuppressionScope,
    alive_seats,
)
from sgs.timing import Timing

__all__ = [
    "DANQI",
    "DANQI_FLOW",
    "FENG_GUANYU",
    "XINHAN",
    "XINHAN_FLOW",
    "YIYUN",
    "YIYUN_FLOW",
    "guanyu_skill_definitions",
    "yiyun_recorded_suits",
]

FENG_GUANYU = "feng_guanyu"
"""锋·关羽的武将 id（``heroes.homebrew.json`` 的 ``id``）。"""

DANQI = hero_skill_id(FENG_GUANYU, "danqi")
"""【单骑】的全限定技能 id。"""

YIYUN = hero_skill_id(FENG_GUANYU, "yiyun")
"""【义云】的全限定技能 id（锁定技；也是丙的失效记录的**施加者** id）。"""

XINHAN = hero_skill_id(FENG_GUANYU, "xinhan")
"""【心汉】的全限定技能 id（觉醒技）。"""

# ---------------------------------------------------------------------------
# 【义云】的记录：技能实例私有的 4 位花色位段
# ---------------------------------------------------------------------------

_YIYUN_SHIFT = 44
"""记录位段的起始位（bit 44..47 = 四种花色，**整局**位、失败失不清）。

**为什么从 44 起**：``state.skill_state`` 的同一个整数里还住着别的位段——0..22 是 A 族
阶段位 / 仁德 / 黄天（出牌阶段清）、23 是觉醒"视为满足"、24..31 是【定乱】座位段、
32..35 是【慎行】本阶段次数、36..43 是 SP孙尚香【良助】座位段。``int`` 无位宽上限。
本段落在 ``_PHASE_STATE_MASK`` / ``reset_turn_skill_state`` 的清理范围之外 ⇒ 记录自然跨回合，
只在义云自己最后一段"清空记录的花色"或**实例消失**（``prune_skill_state`` 删键）时消失。
"""

_YIYUN_SUIT_BITS = 0b1111 << _YIYUN_SHIFT
"""记录位段：bit (44 + i) = 第 i 种花色已在记录里（i 按 :data:`_SUITS` 的规范序）。"""

_SUITS: tuple[Suit, ...] = tuple(Suit)
"""花色的**规范序**（``Suit`` 的声明序：红桃 / 方块 / 黑桃 / 梅花）。

位图没有"首次出现序"，而规则只读"哪些花色"（均为红 / 均为黑 / 有红有黑 / 花色数），
不读次序 ⇒ 读口按规范序输出，界面与引擎因此不会就"哪一色先记到"说两种话。
"""


def _yiyun_instance_for(state: GameState, seat: int):
    """``seat`` 的义云实例（含失效：失效 ≠ 没用过；没有 = 技能不在 / 已失去）。"""
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.skill_id == YIYUN and instance.owner == int(seat):
            return instance
    return None


def _yiyun_mask(state: GameState, seat: int) -> int:
    """记录位段的值（0..15；没有实例 ⇒ 0）。"""
    instance = _yiyun_instance_for(state, seat)
    if instance is None:
        return 0
    flags = int(state.skill_state.get(instance_key(instance), 0))
    return (flags & _YIYUN_SUIT_BITS) >> _YIYUN_SHIFT


def yiyun_recorded_suits(state: GameState, seat: int) -> tuple[str, ...]:
    """**公开读口**：义云记录的花色（规范序；没有记录 / 没有实例 ⇒ ``()``）。

    唯一消费者 = 单骑第三段的响应牌面收窄（以及接入层的座位标签，经
    :mod:`sgs.skills` 的公开面转出）。返回值是**花色 id**（``"heart"`` 一类），
    与 ``CardRef.suit`` 同一套取值。
    """
    mask = _yiyun_mask(state, int(seat))
    return tuple(suit.value for index, suit in enumerate(_SUITS) if mask & (1 << index))


def _record_suits(state: GameState, seat: int, suits: tuple[str, ...]) -> None:
    """把这一批花色并入记录（集合语义：重复花色不占新位）。没有实例 ⇒ 零动作。"""
    instance = _yiyun_instance_for(state, seat)
    if instance is None or not suits:
        return
    key = instance_key(instance)
    flags = int(state.skill_state.get(key, 0))
    for suit in suits:
        flags |= 1 << (_SUITS.index(Suit(suit)) + _YIYUN_SHIFT)
    state.skill_state[key] = flags


def _clear_suits(state: GameState, seat: int) -> None:
    """清空记录（义云最后一段的唯一清空点）；位段清零后键也删掉（位图纪律：空键不占位）。"""
    instance = _yiyun_instance_for(state, seat)
    if instance is None:
        return
    key = instance_key(instance)
    flags = int(state.skill_state.get(key, 0)) & ~_YIYUN_SUIT_BITS
    if flags:
        state.skill_state[key] = flags
    else:
        state.skill_state.pop(key, None)


def _has_yiyun(state: GameState, seat: int) -> bool:
    """「有义云」= **拥有**（武将面有 ∧ 未失去；锁定技不吃非锁定失效）。"""
    return _yiyun_instance_for(state, seat) is not None


# ---------------------------------------------------------------------------
# 甲乙丙丁：两条技能共用的四个选项
# ---------------------------------------------------------------------------

YIYUN_DRAW_TWO = "yiyun:draw_two"
"""甲：摸 2 张牌。"""

YIYUN_RECOVER_ONE = "yiyun:recover_one"
"""乙：回复 1 点体力。"""

YIYUN_SUPPRESS = "yiyun:suppress"
"""丙：本回合非锁定技能失效直到有黑色牌进入弃牌堆。"""

YIYUN_DAMAGE_ONE = "yiyun:damage_one"
"""丁：受到你造成的 1 点伤害。"""

RED_OPTIONS: tuple[str, ...] = (YIYUN_DRAW_TWO, YIYUN_RECOVER_ONE)
"""红色分支的两项（甲 / 乙）。"""

BLACK_OPTIONS: tuple[str, ...] = (YIYUN_SUPPRESS, YIYUN_DAMAGE_ONE)
"""黑色分支的两项（丙 / 丁）。"""

ALL_OPTIONS: tuple[str, ...] = (*RED_OPTIONS, *BLACK_OPTIONS)
"""四项（心汉第二段的闭集，顺序 = 牌面 甲乙丙丁）。"""

OPTION_TEXTS: dict[str, str] = {
    YIYUN_DRAW_TWO: "甲：摸两张牌",
    YIYUN_RECOVER_ONE: "乙：回复1点体力",
    YIYUN_SUPPRESS: "丙：本回合非锁定技能失效",
    YIYUN_DAMAGE_ONE: "丁：受到你造成的1点伤害",
}
"""四个选项的**中文按钮文案**（唯一真源；用户 2026-11-20 实机点名「四个选项都改为中文的」）。

两个消费者必须逐字一致：

- 引擎侧：``ask_option(..., option_texts=...)`` 的载荷（记录流里的询问也读它）；
- 前端侧：``tools/sgs_web/static/options.js`` 的 ``OPTION_LABELS``（按钮上真正显示的字
  —— 前端"中文只住 OPTION_LABELS"是本仓的既有纪律，故两边都要有）。

``tests/test_web.py::test_the_feng_guanyu_option_labels_are_chinese_on_both_sides`` 把两份
逐条对齐：这边改了而那边没改，测试红。
"""


def _option_text(option: str) -> str:
    """选项的中文标签（:data:`OPTION_TEXTS`；引擎与前端同文，见那条常量的说明）。"""
    return OPTION_TEXTS[option]


_SUSPENDED = object()
"""``_ask_seat`` 的"挂起"哨兵（与"可选且放弃 ⇒ ``None``"区分开）。"""


def _ask_seat(
    host: Any,
    frame: Any,
    owner: int,
    *,
    timing: Timing,
    key: str,
    pool: tuple[int, ...],
    optional: bool = False,
) -> Any:
    """问**一名**角色（``ask_seats`` 的单选档：``lo = hi = 1``）；挂起时返回 :data:`_SUSPENDED`。

    本模块的全部"令一名 / 另一名 / 对不同角色"都走这一个 helper（全文件唯一一处
    ``ask_seats`` 调用）：结构审计（``tests/test_seat_multi_select.py``）要求每个文件
    恰好一处，免得又长出第二份手写座位询问。
    """
    answer = host.ask_seats(
        frame,
        owner,
        timing,
        skill_id=key,
        pool=pool,
        lo=1,
        hi=1,
        optional=optional,
    )
    if host.suspended(answer):
        return _SUSPENDED
    picked = tuple(answer)
    return int(picked[0]) if picked else None


def _push_recover(host: Any, frame: Any, *, source: int, target: int, amount: int = 1) -> None:
    """压一个 ``RECOVER`` 子帧（回复量由帧算、超过上限由帧夹住）。"""
    host.push_frame(
        FrameKind.RECOVER,
        {
            "source": source,
            "targets": (target,),
            "card": None,
            "use_timing": frame.data.get("use_timing"),
            "amount": amount,
        },
        timing=Timing.RECOVER_VALUE,
    )


def _push_damage(host: Any, frame: Any, *, source: int, target: int, amount: int = 1) -> None:
    """压一个 ``DAMAGE`` 子帧（技能伤害无渠道牌 ⇒ ``card`` / ``channel`` = 帧上的那张，通常 None）。"""
    host.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": frame.data.get("card"),
            "use_timing": frame.data.get("use_timing"),
            "target": target,
            "channel": frame.data.get("card"),
            "nature": DamageNature.NORMAL,
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )


def _run_option(
    host: Any,
    frame: Any,
    *,
    actor: int,
    target: int,
    option: str,
    done_key: str,
) -> Any:
    """执行甲乙丙丁中的一项（**幂等**：``done_key`` 已置 ⇒ 不重复执行）。

    返回 ``"stay"`` = 压了子帧 / 摸牌挂起（调用方原样上抛），``None`` = 已经结算完。
    四个选项的效果都是"一次性落座位状态"（失效记录）或"压子帧"（回复 / 伤害）或
    "摸牌"，故游标只需"这一项做没做过"一个布尔（**先置游标再动手**：
    ``draw_for_hand`` 挂起时重入不重摸，与【定品】的 ``_DINGPIN_DRAWN_KEY`` 同一条纪律）。
    """
    if frame.data.get(done_key):
        return None
    if option == YIYUN_DRAW_TWO:
        frame.data[done_key] = True
        control = host.draw_for_hand(frame, target, 2)
        return "stay" if control == "stay" else None
    if option == YIYUN_RECOVER_ONE:
        frame.data[done_key] = True
        _push_recover(host, frame, source=actor, target=target, amount=1)
        return "stay"
    if option == YIYUN_SUPPRESS:
        frame.data[done_key] = True
        if host.state.player(target).alive:
            # 作用域 = 本回合内**直到有黑色牌进入弃牌堆**（票 05 的框架件）。
            host.suppress_skills(
                target,
                YIYUN,
                frame=frame,
                scope=SkillSuppressionScope.UNTIL_BLACK_DISCARD,
            )
        return None
    if option == YIYUN_DAMAGE_ONE:
        frame.data[done_key] = True
        if host.state.player(target).alive:
            _push_damage(host, frame, source=actor, target=target, amount=1)
            return "stay"
        return None
    return None  # pragma: no cover - 闭集之外不会出现


# ---------------------------------------------------------------------------
# 【单骑】：其它角色的准备阶段 → 全部同色手牌当一张【决斗】/ 火【杀】对其使用
# ---------------------------------------------------------------------------

_DANQI_BLACK = "danqi:black"
_DANQI_RED = "danqi:red"

_DANQI_STARTED = "danqi:started"
"""帧私有：这次使用已经开出（重入不再开第二次）。"""


def _color_predicate(color: Color):
    """全集转化的取牌谓词：这张手牌是不是该颜色。"""

    def predicate(state: GameState, card_id: int) -> bool:
        return state.card(card_id).color is color

    return predicate


def _danqi_colors(state: GameState, owner: int) -> tuple[Color, ...]:
    """手里**有牌的**颜色（黑在前，与选项顺序一致；空集 ⇒ 技能不可发动）。"""
    return tuple(
        color
        for color in (Color.BLACK, Color.RED)
        if collective_sources(state, owner, _color_predicate(color))
    )


def _danqi_options(state: GameState, frame: Any, owner: int, answers: Any) -> tuple[str, ...]:
    """选项闭集恒为两项（某色没牌时**置灰**而不是删掉：玩家要看得出这条分支此刻用不了）。"""
    del frame, owner, answers
    return (_DANQI_BLACK, _DANQI_RED)


def _danqi_disabled(state: GameState, frame: Any, owner: int, answers: Any) -> tuple[str, ...]:
    """黑 / 红哪一色没有手牌 ⇒ 那一项置灰（``PickOption.disabled``）。"""
    del frame, answers
    present = _danqi_colors(state, owner)
    return tuple(
        option
        for option, color in ((_DANQI_BLACK, Color.BLACK), (_DANQI_RED, Color.RED))
        if color not in present
    )


def _danqi_target_ok(state: GameState, owner: int, target: int) -> bool:
    """目标是否合法：两种产物各判一次（火【杀】按"无距离限制"豁免距离判据）。"""
    for product in ("duel", "fire_slash"):
        card = pure_virtual_card(state, product)
        if target in check_targets(state, owner, card, distance_exempt=True):
            return True
    return False


def _danqi_condition(host: Any, frame: Any) -> bool:
    """别的角色的准备阶段 ∧ 手里有黑色或红色手牌 ∧ 目标能成为目标。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    target = frame.data.get("source")
    if owner is None or target is None or int(target) == int(owner):
        return False
    if not _danqi_colors(host.state, int(owner)):
        return False
    return _danqi_target_ok(host.state, int(owner), int(target))


def _danqi_effect(host: Any, frame: Any, answers: Any) -> Any:
    """提交点：按选的颜色取**全部**同色手牌 → 一张转化牌 → 对其使用（可带响应收窄）。"""
    owner = int(frame.data["skill_owner"])
    target = frame.data.get("source")
    choice = answers.get("choice")
    if frame.data.get(_DANQI_STARTED) or target is None:
        return None
    color = Color.BLACK if choice == _DANQI_BLACK else Color.RED
    product = "duel" if color is Color.BLACK else "fire_slash"
    state = host.state
    sources = collective_sources(state, owner, _color_predicate(color))
    if not sources:
        return None
    if not _danqi_target_ok(state, owner, int(target)):
        return None
    card = transformed_card(state, sources, product, allocate=True)
    response_suits: tuple[str, ...] | None = None
    if _has_yiyun(state, owner) and len(sources) >= 2:
        # 第三段：只能被义云记录的花色的牌响应（记录为空 ⇒ () ⇒ 无法响应）。
        response_suits = yiyun_recorded_suits(state, owner)
    frame.data[_DANQI_STARTED] = True
    host.start_use(
        card,
        source=owner,
        targets=(int(target),),
        use_timing=Timing.PHASE_PREPARE,
        response_suits=response_suits,
    )
    return "stay"


DANQI_FLOW = SkillFlow(
    name="danqi",
    steps=(
        PickOption(
            options=_danqi_options,
            disabled=_danqi_disabled,
            optional=False,
            key="choice",
        ),
    ),
    effect=_danqi_effect,
)
"""【单骑】的单步流水线：选颜色 / 产物 → 全部同色手牌当一张牌对其使用。"""


# ---------------------------------------------------------------------------
# 【义云】：锁定技，每回合一次，造成伤害时 → 展示手牌、记录花色、按颜色执行甲乙 / 丙丁
# ---------------------------------------------------------------------------

_YIYUN_STAGE = "yiyun:stage"
_YIYUN_CARDS = "yiyun:cards"
_YIYUN_BRANCH = "yiyun:branch"
_YIYUN_SEAT = "yiyun:seat"
_YIYUN_OPTION = "yiyun:option"
_YIYUN_FIRST_DONE = "yiyun:first_done"
_YIYUN_SECOND_SEAT = "yiyun:second_seat"
_YIYUN_SECOND_DONE = "yiyun:second_done"
"""帧私有：第二项已经执行完（记录型游标，仅用于重入幂等）。"""

_YIYUN_REVEAL = "reveal"
_YIYUN_FIRST = "first"
_YIYUN_SECOND = "second"
_YIYUN_DRAW = "draw"

_YIYUN_FIRST_KEY = "yiyun:first"
_YIYUN_SECOND_OPTION_KEY = "yiyun:second"


def _yiyun_condition(host: Any, frame: Any) -> bool:
    """造成伤害的是我 ∧ 本回合还没触发过（锁定技不问"是否发动"）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    return not skill_used_this_turn(host.state, int(owner), YIYUN)


def _revealed_colors(state: GameState, cards: tuple[int, ...]) -> set[Color]:
    """本次展示的牌的颜色集合（空手牌 ⇒ 空集 ⇒ ①② 都不触发）。"""
    return {state.card(card_id).color for card_id in cards}


def _branch_of(cards: tuple[int, ...], state: GameState) -> str | None:
    """本次展示的是"全红" / "全黑" / 都不是（空手牌 = 都不是，见票 02 R2）。"""
    if not cards:
        return None
    colors = _revealed_colors(state, cards)
    if colors == {Color.RED}:
        return "red"
    if colors == {Color.BLACK}:
        return "black"
    return None


def _record_all_of(suits: tuple[str, ...], color: Color) -> bool:
    """记录的花色是否**全部**属于该颜色（记录为空 ⇒ ``False``）。"""
    return bool(suits) and all(color_of_suit(suit) is color for suit in suits)


def _yiyun_record_stage(host: Any, frame: Any, owner: int) -> None:
    """展示所有手牌 + 记录这些牌的花色（帧私有快照：结算中手牌会变，判定读快照）。"""
    state = host.state
    cards = tuple(state.player(owner).hand)
    for card_id in cards:
        host.reveal_card(frame, owner, state.card(card_id))
    frame.data[_YIYUN_CARDS] = cards
    suits = tuple(
        sorted(
            {
                state.card(card_id).suit
                for card_id in cards
                if state.card(card_id).suit is not None
            }
        )
    )
    _record_suits(state, owner, suits)
    frame.data[_YIYUN_STAGE] = _YIYUN_FIRST


def _yiyun_first_stage(host: Any, frame: Any, owner: int) -> Any:
    """先选人 → 再选项 → 执行（本次全红 / 全黑才走到这里）。"""
    state = host.state
    cards = tuple(frame.data.get(_YIYUN_CARDS, ()))
    branch = frame.data.get(_YIYUN_BRANCH)
    if branch is None:
        branch = _branch_of(cards, state)
        frame.data[_YIYUN_BRANCH] = branch
    if branch is None:
        frame.data[_YIYUN_STAGE] = _YIYUN_DRAW
        return None
    options = RED_OPTIONS if branch == "red" else BLACK_OPTIONS
    if _YIYUN_SEAT not in frame.data:
        seat = _ask_seat(
            host,
            frame,
            owner,
            timing=Timing.DAMAGE_DEAL,
            key=f"{YIYUN}:seat",
            pool=tuple(alive_seats(state)),
        )
        if seat is _SUSPENDED:
            return "stay"
        frame.data[_YIYUN_SEAT] = int(seat)
    if _YIYUN_OPTION not in frame.data:
        answer = host.ask_option(
            frame,
            owner,
            Timing.DAMAGE_DEAL,
            skill_id=f"{YIYUN}:option",
            options=options,
            optional=False,
            option_texts={option: _option_text(option) for option in options},
        )
        if host.suspended(answer):
            return "stay"
        frame.data[_YIYUN_OPTION] = str(answer.choice)
    control = _run_option(
        host,
        frame,
        actor=owner,
        target=int(frame.data[_YIYUN_SEAT]),
        option=str(frame.data[_YIYUN_OPTION]),
        done_key=_YIYUN_FIRST_KEY,
    )
    if control == "stay":
        return "stay"
    frame.data[_YIYUN_STAGE] = _YIYUN_SECOND
    return None


def _yiyun_second_stage(host: Any, frame: Any, owner: int) -> Any:
    """记录全红 / 全黑时：**可以**令另一名角色执行**另一项**（只问人，不问项）。"""
    state = host.state
    branch = frame.data.get(_YIYUN_BRANCH)
    suits = yiyun_recorded_suits(state, owner)
    color = Color.RED if branch == "red" else Color.BLACK
    if _record_all_of(suits, color):
        if _YIYUN_SECOND_SEAT not in frame.data:
            first = int(frame.data.get(_YIYUN_SEAT, -1))
            pool = tuple(seat for seat in alive_seats(state) if seat != first)
            chosen: int | None = None
            if pool:
                seat = _ask_seat(
                    host,
                    frame,
                    owner,
                    timing=Timing.DAMAGE_DEAL,
                    key=f"{YIYUN}:seat2",
                    pool=pool,
                    optional=True,
                )
                if seat is _SUSPENDED:
                    return "stay"
                chosen = None if seat is None else int(seat)
            frame.data[_YIYUN_SECOND_SEAT] = chosen
        second = frame.data.get(_YIYUN_SECOND_SEAT)
        if second is not None:
            options = RED_OPTIONS if branch == "red" else BLACK_OPTIONS
            other = next(
                option for option in options if option != frame.data[_YIYUN_OPTION]
            )
            control = _run_option(
                host,
                frame,
                actor=owner,
                target=int(second),
                option=other,
                done_key=_YIYUN_SECOND_OPTION_KEY,
            )
            if control == "stay":
                return "stay"
            frame.data[_YIYUN_SECOND_DONE] = True
    frame.data[_YIYUN_STAGE] = _YIYUN_DRAW
    return None


def _yiyun_draw_stage(host: Any, frame: Any, owner: int) -> Any:
    """最后一段（**有条件的**）：记录有红有黑 ∨ 你没有手牌 ⇒ 摸「记录的花色数」张、清空记录。

    **条件不成立就什么都不做**——既**不摸**、也**不清空**（用户 2026-11-20 实机报障：
    v1 里这一段无条件清空，导致"每次发动都把记录清光"）。记录是**跨回合的并集**：
    本次展示的花色并进旧记录（:func:`_record_suits` 的位或），不清旧位。

    「你没有手牌」在**执行完 ①② 之后**这一刻现算（票 02 R4）：甲若选了你，摸完 2 张就
    不再是空手牌。先摸后清（票 02 R7）。触发与否**帧私有一份快照**（摸牌可能挂起、重入时
    手牌已经变了，重算会把"空手牌触发的"那一次错判成不触发）。
    """
    state = host.state
    suits = yiyun_recorded_suits(state, owner)
    if _YIYUN_DRAW + ":triggered" not in frame.data:
        colors = {color_of_suit(suit) for suit in suits}
        frame.data[_YIYUN_DRAW + ":triggered"] = (len(colors) > 1) or (
            not state.player(owner).hand
        )
    if not frame.data[_YIYUN_DRAW + ":triggered"]:
        return None
    if not frame.data.get(_YIYUN_DRAW + ":done"):
        frame.data[_YIYUN_DRAW + ":done"] = True
        if len(suits) > 0:
            control = host.draw_for_hand(frame, owner, len(suits))
            if control == "stay":
                return "stay"
    if not frame.data.get(_YIYUN_DRAW + ":cleared"):
        frame.data[_YIYUN_DRAW + ":cleared"] = True
        _clear_suits(state, owner)
    return None


def _yiyun_effect(host: Any, frame: Any, answers: Any) -> Any:
    """零步流水线的段机：展示 → 记录 → 第一项 → 第二项 → 摸 + 清（帧内游标逐步推进）。"""
    del answers
    owner = int(frame.data["skill_owner"])
    if frame.data.get(_YIYUN_STAGE) is None:
        mark_skill_used_this_turn(host.state, owner, YIYUN)
        _yiyun_record_stage(host, frame, owner)
    stage = frame.data.get(_YIYUN_STAGE)
    if stage == _YIYUN_FIRST:
        control = _yiyun_first_stage(host, frame, owner)
        if control == "stay":
            return "stay"
        stage = frame.data.get(_YIYUN_STAGE)
    if stage == _YIYUN_SECOND:
        control = _yiyun_second_stage(host, frame, owner)
        if control == "stay":
            return "stay"
    return _yiyun_draw_stage(host, frame, owner)


YIYUN_FLOW = SkillFlow(name="yiyun", steps=(), effect=_yiyun_effect)
"""【义云】的零步流水线（锁定技不问）：段机住在 effect 里（展示 / 记录 / 两项 / 摸清）。"""


# ---------------------------------------------------------------------------
# 【心汉】：觉醒技（准备阶段）→ 势力改蜀 → 可依次对不同角色执行四项各一次
# ---------------------------------------------------------------------------

_XINHAN_STAGE = "xinhan:stage"
_XINHAN_USED = "xinhan:used"
_XINHAN_SEATS = "xinhan:seats"
_XINHAN_PENDING_OPTION = "xinhan:pending_option"
_XINHAN_PENDING_SEAT = "xinhan:pending_seat"
_XINHAN_DONE_KEY = "xinhan:done"

_XINHAN_TARGET = 6
"""觉醒条件：累计**造成**伤害点数 + 回复体力点数 ≥ 6（牌面原文；用户 2026-11-20 澄清）。"""


def _xinhan_awakening_condition(host: Any, frame: Any) -> bool:
    """觉醒条件（时机 + 计数 + 未觉醒 + 辉逝的"视为满足"）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    verdict = awakening_skill_verdict(host, frame, XINHAN)
    if verdict is False:
        return False
    if verdict is True:
        return True
    if awakening_skill_awakened(host.state, int(owner), XINHAN):
        return False
    player = host.state.player(int(owner))
    return (
        int(player.xinhan_damage_points) + int(player.xinhan_recover_points)
        >= _XINHAN_TARGET
    )


def _xinhan_count_damage_condition(host: Any, frame: Any) -> bool:
    """**造成伤害**的计数钩子：来源是我 ∧ 数值已定稿 > 0 ∧ 未被防止（与 ``note_damage_dealt`` 同判据）。

    用户 2026-11-20 追加口径（实机报障后澄清）：数的是**造成**伤害 —— 与牌面「累计造成
    伤害数」一致；「受到伤害」是笔误。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    amount = int(frame.data.get("amount", 0))
    return amount > 0 and not has_tag(frame, Tag.PREVENTED)


def _xinhan_count_damage(host: Any, frame: Any) -> None:
    """本局累计**造成伤害点数**（技能侧计数：`06` R5 已裁，不开引擎侧整局计数）。"""
    owner = int(frame.data[MATCH_OWNER_KEY])
    host.state.player(owner).xinhan_damage_points += int(frame.data.get("amount", 0))


def _xinhan_count_recover_condition(host: Any, frame: Any) -> bool:
    """回复的计数钩子：回复的是我 ∧ 实际回复量 > 0。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    targets = tuple(frame.data.get("targets", ()))
    if owner is None or not targets or int(targets[0]) != int(owner):
        return False
    return int(frame.data.get(RECOVERED_AMOUNT_KEY, 0)) > 0


def _xinhan_count_recover(host: Any, frame: Any) -> None:
    """本局累计**回复体力点数**（实际回复量：被体力上限夹过的那一份）。"""
    owner = int(frame.data[MATCH_OWNER_KEY])
    host.state.player(owner).xinhan_recover_points += int(
        frame.data.get(RECOVERED_AMOUNT_KEY, 0)
    )


def _xinhan_effect(host: Any, frame: Any, answers: Any) -> Any:
    """觉醒：势力改为蜀 → 依次对不同角色执行甲乙丙丁（每项一次、最多 4 轮）。"""
    del answers
    state = host.state
    owner = int(frame.data["skill_owner"])
    if frame.data.get(_XINHAN_STAGE) is None:
        frame.data[_XINHAN_STAGE] = "kingdom"
        frame.data[_XINHAN_USED] = []
        frame.data[_XINHAN_SEATS] = []
    if frame.data[_XINHAN_STAGE] == "kingdom":
        host.set_kingdom(owner, Kingdom.SHU)
        frame.data[_XINHAN_STAGE] = "options"
    used: list[str] = frame.data[_XINHAN_USED]
    chosen: list[int] = frame.data[_XINHAN_SEATS]
    while True:
        remaining = tuple(option for option in ALL_OPTIONS if option not in used)
        pool = tuple(seat for seat in alive_seats(state) if seat not in chosen)
        if not remaining or not pool:
            return None
        if frame.data.get(_XINHAN_PENDING_OPTION) is None:
            answer = host.ask_option(
                frame,
                owner,
                Timing.PHASE_PREPARE,
                skill_id=f"{XINHAN}:option:{len(used)}",
                options=remaining,
                optional=True,
                option_texts={option: _option_text(option) for option in remaining},
            )
            if host.suspended(answer):
                return "stay"
            if isinstance(answer, Pass):
                return None
            frame.data[_XINHAN_PENDING_OPTION] = str(answer.choice)
        if frame.data.get(_XINHAN_PENDING_SEAT) is None:
            seat = _ask_seat(
                host,
                frame,
                owner,
                timing=Timing.PHASE_PREPARE,
                key=f"{XINHAN}:seat:{len(used)}",
                pool=pool,
            )
            if seat is _SUSPENDED:
                return "stay"
            frame.data[_XINHAN_PENDING_SEAT] = int(seat)
        option = str(frame.data[_XINHAN_PENDING_OPTION])
        seat = int(frame.data[_XINHAN_PENDING_SEAT])
        control = _run_option(
            host,
            frame,
            actor=owner,
            target=seat,
            option=option,
            done_key=f"{_XINHAN_DONE_KEY}:{len(used)}",
        )
        if control == "stay":
            return "stay"
        used.append(option)
        chosen.append(seat)
        frame.data[_XINHAN_PENDING_OPTION] = None
        frame.data[_XINHAN_PENDING_SEAT] = None


XINHAN_FLOW = SkillFlow(name="xinhan", steps=(), effect=_xinhan_effect)
"""【心汉】的零步流水线：势力 → 四选一循环（段机住在 effect 里）。"""


def guanyu_skill_definitions() -> tuple[SkillDef, ...]:
    """锋·关羽的三条技能定义（并入 ``CATALOG``；数据面住 ``heroes.homebrew.json``）。"""
    return (
        SkillDef(
            skill_id=DANQI,
            locked=False,
            carrier=CarrierKind.HERO,
            hooks=(
                Hook(
                    Timing.PHASE_PREPARE,
                    flow=DANQI_FLOW,
                    condition=_danqi_condition,
                ),
            ),
            requirement=(
                "一名其它角色的准备阶段开始时，你可以将所有黑 / 红色手牌当作一张"
                "【决斗】/ 无距离限制的火【杀】对其使用"
                "（若你有义云且转化牌数 ≥ 2，此牌只能被义云记录的花色的牌响应）"
            ),
            step_requirements={"choice": "选择一项：1. 黑色手牌当【决斗】；2. 红色手牌当火【杀】"},
        ),
        SkillDef(
            skill_id=YIYUN,
            locked=True,
            carrier=CarrierKind.HERO,
            # 座位标签（feng-guanyu 票 07）：一条技能两个显示面，spec 登记在
            # ``sgs_web.projection.BADGE_PROVIDERS``（记录花色 / 被丙失效的目标）。
            badge=BadgeKind.WINDOW,
            hooks=(
                Hook(
                    Timing.DAMAGE_DEAL,
                    flow=YIYUN_FLOW,
                    condition=_yiyun_condition,
                ),
            ),
            requirement=(
                "锁定技，每回合限一次，当你造成伤害时，展示所有手牌并记录这些牌的花色；"
                "本次全红 / 全黑时令一名角色执行甲 / 乙或丙 / 丁中的一项，记录同色时可令"
                "另一名角色执行另一项；记录混色或你没有手牌时摸记录的花色数张牌并清空记录"
            ),
        ),
        SkillDef(
            skill_id=XINHAN,
            locked=False,
            carrier=CarrierKind.HERO,
            # 觉醒技：满足条件时**强制**发动（`06:1075`：按 skill_type 判，不并进 locked）。
            skill_type=SkillType.AWAKENING,
            hooks=(
                Hook(
                    Timing.PHASE_PREPARE,
                    flow=XINHAN_FLOW,
                    condition=_xinhan_awakening_condition,
                ),
                # 簿记段（纯计数，不进"选先后"的面）：本局造成伤害点数 / 回复体力点数。
                Hook(
                    Timing.DAMAGE_AFTER_DEAL,
                    handler=_xinhan_count_damage,
                    condition=_xinhan_count_damage_condition,
                    forced=True,
                    bookkeeping=True,
                ),
                Hook(
                    Timing.RECOVER_AFTER,
                    handler=_xinhan_count_recover,
                    condition=_xinhan_count_recover_condition,
                    forced=True,
                    bookkeeping=True,
                ),
            ),
            requirement=(
                "觉醒技，准备阶段开始时，若你本局游戏累计造成伤害数与回复体力数之和"
                "大于等于 6，你将势力修改为蜀；然后若你有义云，可依次对不同角色执行"
                "义云甲乙丙丁中的 1 个选项（每个选项只能选 1 次）"
            ),
        ),
    )
