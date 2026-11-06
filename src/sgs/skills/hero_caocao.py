"""自制包·韬·曹操（``tao-caocao`` 票 01）：【鸿志】【倡义】。

牌面权威 = 用户录入的 ``D:\\SGS_HEROS\\自制包\\韬·曹操\\技能.txt``（逐字，见下）。两条技能
都是**帧外时机**钩子，族级原语一律复用既有实现，不新增机制（框架侧的两处加法已由本 effort
的票 `02` / `03` 单独落地：``PHASE_PLAY_END`` 的派发点与拼点"扣置后、亮出前"的时机）。

    韬·曹操 4/4 群
    【鸿志】(锁)：若你回合开始时无手牌或回合结束时场上无【倡义】，将手牌数调整为等同于
    体力上限(至多为5)
    【倡义】：每轮限1次，一名其他角色的出牌阶段结束时，你可以与其进行1次拼点，若其没赢，
    你可以令其获得【倡义】；若你没赢，须选择：1.弃置至少X张牌并对一名没有【倡义】的角色
    造成1点伤害；2.摸X张牌，然后你失去此技能（X为场上没有【倡义】的角色数且最多为3）
    当你拼点时，你可以令点数+X

用户 2026-11-06 签的裁定（``.scratch/tao-caocao/issues/01-tao-caocao.md`` §用户裁定）：

- **Q1**：「出牌阶段结束时」补派发点（票 `02`）；
- **Q2**：补"扣置后、亮出拼点牌前"的时机，技能可在此时机调整点数（票 `03`）；
- **Q3**：「令其获得【倡义】」= **复制**（给予者保留，走 ``grant_skill``）；
- **Q4**：「每轮限1次」= **按实例**（每个持有者各算各的）；
- **Q5**：「场上没有【倡义】的角色数」的「场上」= **存活角色**（规则 §4.16 L525）；
- **Q6**：平点时两段都结算，**先授予、后二选一**；X **每次取值时重算**；
- **Q7**：【鸿志】「至多为5」= **手牌终值上限**（账本 L888 的口径）；
- **Q8**：【鸿志】回合末**双向**调整，弃哪几张由**曹操自己**选。

**两条技能是同一个机制的两半**：【鸿志】的条件②读的是"【倡义】还在不在场上"，而【倡义】
的两条出口（`1` 交给别人 / `2` 自己失去）各自改变那个事实。故它们同住一个模块、同一条
批次票，谁都不能单独解释。

**状态位**：本模块**不往武将牌上放牌、不用标记**——"谁持有【倡义】"由技能实例本身回答
（``Player.granted_skills`` 是第四种来源，账本 L762），"手牌数"读 ``Player.hand``。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, DamageNature
from sgs.state import (
    FrameKind,
    GameState,
    Pass,
    SelectionSpec,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    Hook,
    SkillDef,
    _move_by_source,
    mark_round_skill_used,
    resolve_instances,
    round_skill_uses,
)
from .flow import SEAT_PREFIX, seat_choice

__all__ = [
    "CAOCAO",
    "CHANGYI",
    "CHANGYI_CAP",
    "HONGZHI",
    "HONGZHI_CAP",
    "caocao_skill_definitions",
    "changyi_holders",
    "changyi_x",
]

CAOCAO = "tao_caocao"
"""自制包·韬·曹操的武将 id（``heroes.homebrew.json`` 的 ``id``）。"""

HONGZHI = hero_skill_id(CAOCAO, "hongzhi")
"""【鸿志】的全限定技能 id。"""

CHANGYI = hero_skill_id(CAOCAO, "changyi")
"""【倡义】的全限定技能 id。"""

HONGZHI_CAP = 5
"""【鸿志】「至多为5」的**手牌终值**上限（用户 2026-11-06 裁定 Q7，同账本 L888）。"""

CHANGYI_CAP = 3
"""【倡义】「X…且最多为3」的上限（钳在**最终值**上，同【博图】的 X，账本 L755）。"""

CHANGYI_TITLE = "倡义"
"""拼点弹窗的标题（发动拼点的技能名）。"""

_ASK_HONGZHI_DISCARD = "hero.tao_caocao.hongzhi.discard"
"""【鸿志】弃置段的询问位置（``ask_selection`` 的 ``owner``）。"""

_ASK_CHANGYI_CARDS = "hero.tao_caocao.changyi.cards"
"""【倡义】选项 1 弃牌段的询问位置。"""

_HONGZHI_DREW = "hongzhi_drew"
"""帧私有：本时点的摸牌段已经跑过（重入不重摸）。"""

_HONGZHI_PICKED = "hongzhi_picked"
"""帧私有：弃置段选出的牌（重入不重问）。"""

_HONGZHI_SETTLED = "hongzhi_settled"
"""帧私有：本次时点的调整已收尾（重入直接返回 ``None``）。"""

_CHANGYI_BOOST_DONE = "changyi_boost_done"
"""帧私有（拼点帧）：段 2「点数 +X」的那一问已经答过（重入不重问、不重复加点）。"""

_CHANGYI_GRANTED = "changyi_granted"
"""帧私有：段 3（其没赢 ⇒ 可令其获得【倡义】）已经问过 / 做过。"""

_CHANGYI_CHOICE = "changyi_choice"
"""帧私有：段 4 的二选一答案。"""

_CHANGYI_PAID = "changyi_paid"
"""帧私有：段 4-1 的弃置已经搬完（重入不重搬）。"""

_CHANGYI_CARDS = "changyi_cards"
"""帧私有：段 4-1 选出的弃置牌。"""

_CHANGYI_TARGET = "changyi_target"
"""帧私有：段 4-2 的伤害目标（``-1`` = 池在飞期间变空，伤害落空）。"""

_CHANGYI_PUNISHED = "changyi_punished"
"""帧私有：段 4-2 的伤害子帧已经压过（重入不打第二遍）。"""

_CHANGYI_DRAWN = "changyi_drawn"
"""帧私有：段 4-3 的摸牌已经跑过。"""

_CHANGYI_LOST = "changyi_lost"
"""帧私有：段 4-4 的"失去此技能"已经做过。"""

_CHANGYI_SETTLED = "changyi_settled"
"""帧私有：整次发动已收尾（重入直接返回 ``None``，不再落每轮计数）。"""

_CHANGYI_OPTION_PUNISH = "changyi:punish"
"""段 4 选项 1 的 id（「弃置至少X张牌并对一名没有【倡义】的角色造成1点伤害」）。"""

_CHANGYI_OPTION_DRAW = "changyi:draw"
"""段 4 选项 2 的 id（「摸X张牌，然后你失去此技能」）。"""

_CHANGYI_OPTIONS = (_CHANGYI_OPTION_PUNISH, _CHANGYI_OPTION_DRAW)
"""段 4 的选项闭集（牌面「须选择」⇒ ``optional=False``，没有 Pass）。"""

_CHANGYI_GRANT_OPTION = "changyi:grant"
"""段 3「令其获得【倡义】」的选项 id（「可以」⇒ ``optional=True``，有 Pass）。"""

_CHANGYI_BOOST_OPTION = "changyi:boost"
"""段 2「点数 +X」的选项 id（同上，"可以" ⇒ 有 Pass）。"""


# ---------------------------------------------------------------------------
# 共用查询：谁持有【倡义】/ X 是多少
# ---------------------------------------------------------------------------


def changyi_holders(state: GameState) -> frozenset[int]:
    """**存活**且持有【倡义】实例的座位（用户 2026-11-06 裁定 Q5 = 存活角色）。

    读 ``resolve_instances(include_suppressed=True)``：**暂时失效也算持有**——失效是"实例
    还在、被挡在解析之外"（账本 L762），牌面上的"场上无【倡义】"因此不成立。死者即使
    ``granted_skills`` 里还留着那条记录也不再"在场上"（规则 §4.16 L525）。
    """
    return frozenset(
        instance.owner
        for instance in resolve_instances(state, include_suppressed=True)
        if instance.skill_id == CHANGYI and state.player(instance.owner).alive
    )


def changyi_x(state: GameState) -> int:
    """X = 场上没有【倡义】的角色数，**上限 3**；每次取值按当下重算（裁定 Q6 / 账本 L776⑤）。

    **不是快照**：授予段（段 3）会改变"谁持有"，故二选一段（段 4）的读点各自现算一次
    —— 这是用户 2026-11-06 签的 Q6 后半句。
    """
    holders = changyi_holders(state)
    lacking = [
        player.seat for player in state.players if player.alive and player.seat not in holders
    ]
    return min(len(lacking), CHANGYI_CAP)


def _changyi_targets(state: GameState) -> tuple[int, ...]:
    """段 4-2 的池：存活且**不持有**【倡义】的角色（含自己，默认读法（二）①）。"""
    holders = changyi_holders(state)
    return tuple(
        player.seat for player in state.players if player.alive and player.seat not in holders
    )


def _owner_of(frame: Any) -> int | None:
    """派发环写进来的"我是谁"；缺 ⇒ 不是钩子的正常上下文。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    return None if owner is None else int(owner)


def _pending_pindian_participant(frame: Any, owner: int) -> bool:
    """``owner`` 是不是**这一次拼点**的参与者（发起者或被拼点者）。

    「当你拼点时」写的是"你拼点"，不是"你发起拼点"（票面 §2.5 的读法 1）⇒ 两种身份都算。
    """
    participants = (frame.data.get("initiator"), *tuple(frame.data.get("targets") or ()))
    return any(seat is not None and int(seat) == owner for seat in participants)


# ---------------------------------------------------------------------------
# §1 【鸿志】（锁定技）
# ---------------------------------------------------------------------------


def _hongzhi_condition_turn_start(host: Any, frame: Any) -> bool:
    """【鸿志】①：**我的**回合开始时，且我没有手牌。

    ``TURN_START`` 是每个座位都派发的帧外时机，故必须自校"这一回合是我的"
    （``frame.data["source"]`` = 当前回合角色）。锁定技 ⇒ 不问"是否发动"，条件假时连回调
    都不跑（`edge-cases.md` 总纪律 1）。
    """
    owner = _owner_of(frame)
    if owner is None or frame.data.get("source") != owner:
        return False
    return not host.state.player(owner).hand


def _hongzhi_condition_turn_end(host: Any, frame: Any) -> bool:
    """【鸿志】②：**我的**回合结束时，且场上没有【倡义】。

    「场上无【倡义】」= 没有任何**存活**角色持有【倡义】实例（见 :func:`changyi_holders`）。
    """
    owner = _owner_of(frame)
    if owner is None or frame.data.get("source") != owner:
        return False
    return not changyi_holders(host.state)


def _hongzhi_adjust(host: Any, frame: Any, timing: Timing) -> Any:
    """把**自己**的手牌数调整到 ``min(体力上限, 5)``（摸到 / 弃到，双向）。

    两只手各有帧内游标（``_HONGZHI_DREW`` / ``_HONGZHI_PICKED``）：摸牌会内联派发
    "获得牌后"、弃置会内联派发"失去牌后"一类钩子，挂起后的续跑既不重做也不漏做（G7）。
    张数**每次现算**：挂起期间手牌可能被外部效果改变，候选不足时按新值收口（G9）。
    """
    owner = _owner_of(frame)
    if owner is None:
        return None
    state = host.state
    if frame.data.get(_HONGZHI_SETTLED):
        return None
    target = min(int(state.player(owner).hp_max), HONGZHI_CAP)
    hand = len(state.player(owner).hand)
    if hand < target:
        if not frame.data.get(_HONGZHI_DREW):
            frame.data[_HONGZHI_DREW] = True
            if host.draw_for_hand(frame, owner, target - hand) == "stay":
                return "stay"
        frame.data[_HONGZHI_SETTLED] = True
        return None
    if hand > target:
        picked = frame.data.get(_HONGZHI_PICKED)
        if picked is None:
            need = hand - target
            if need <= 0:
                frame.data[_HONGZHI_SETTLED] = True
                return None
            answer = host.ask_selection(
                frame,
                owner=_ASK_HONGZHI_DISCARD,
                key=f"hongzhi:discard:{target}",
                actor=owner,
                timing=timing,
                spec=SelectionSpec(
                    zones=(Zone.HAND,),
                    total=(need, need),
                    optional=False,
                ),
                # 应答的是**手牌持有者本人** ⇒ 可见槽（与【镇骨】弃置支路同一口径）。
                build_face=lambda: host.selection_face(owner, (Zone.HAND,), reveal_hand=True),
            )
            if host.suspended(answer):
                return "stay"
            picked = tuple(int(card_id) for card_id in answer)
            frame.data[_HONGZHI_PICKED] = picked
        if picked:
            # 一次 ``move_card`` 只能有一个来源区，且搬牌会内联派发"失去牌后"而挂起
            # ⇒ 走共享的分组搬牌（plan / done 游标只有那一份实现）。
            if _move_by_source(
                host,
                frame,
                picked,
                to_zone=ZoneRef(Zone.DISCARD),
                owner=owner,
                key="hongzhi:cost",
            ) == "stay":
                return "stay"
        frame.data[_HONGZHI_PICKED] = ()
    frame.data[_HONGZHI_SETTLED] = True
    return None


def _hongzhi_turn_start(host: Any, frame: Any) -> Any:
    """① 的入口：调整（时点 = ``TURN_START``，只影响询问的语境）。"""
    return _hongzhi_adjust(host, frame, Timing.TURN_START)


def _hongzhi_turn_end(host: Any, frame: Any) -> Any:
    """② 的入口：调整（时点 = ``TURN_END``）。"""
    return _hongzhi_adjust(host, frame, Timing.TURN_END)


# ---------------------------------------------------------------------------
# §2 【倡义】（非锁定；段 2「点数 +X」是同一技能的第二条钩子）
# ---------------------------------------------------------------------------


def _changyi_opponent(frame: Any, owner: int) -> int | None:
    """「其」= 刚刚结束出牌阶段的那个角色（``frame.data["source"]``，唯一先行词）。"""
    turn = frame.data.get("source")
    if turn is None or int(turn) == owner:
        return None
    return int(turn)


def _changyi_condition(host: Any, frame: Any) -> bool:
    """【倡义】的触发条件：别人的出牌阶段结束时，双方都有手牌，且本轮还没发动过。

    四条缺一不可：

    1. **别的角色**的回合（「一名其他角色」；自己回合结束的出牌阶段不算）；
    2. 本轮**按实例**还没用过（裁定 Q4；``round_skill_uses`` 读的是 (座位, 实例)）；
    3. **双方都有手牌**——拼点的前置（规则 12.1 L1316）；
    4. 双方都还活着（防御性：死者的回合不存在）。

    被【乐不思蜀】跳过的出牌阶段**不经过这个时点**（``_phase_play_start`` 的 ``_skip_to``）
    ⇒ 这一问根本不会出现。
    """
    owner = _owner_of(frame)
    if owner is None:
        return False
    opponent = _changyi_opponent(frame, owner)
    if opponent is None:
        return False
    state = host.state
    if not state.player(owner).alive or not state.player(opponent).alive:
        return False
    if round_skill_uses(state, owner, CHANGYI) >= 1:
        return False
    return bool(state.player(owner).hand) and bool(state.player(opponent).hand)


def _changyi_boost_condition(host: Any, frame: Any) -> bool:
    """段 2 的条件：这一次拼点里**有我**（发起者或被拼点者）。

    它挂在 ``RANK_COMPARE_PLACED``（牌扣置入处理区后、亮出前）——规则 12.3 L1337–L1340
    的"拼点期间可以发动技能"窗口。这一条钩子是 ``forced``（不问"是否发动"）：牌面的
    「你可以令点数 +X」就是这一问本身，handler 里带 Pass 问。
    """
    owner = _owner_of(frame)
    if owner is None:
        return False
    if not host.state.player(owner).alive:
        return False
    return _pending_pindian_participant(frame, owner)


def _changyi_boost(host: Any, frame: Any) -> Any:
    """段 2：把本次拼点里**我的**点数加 X（可放弃）。

    X 与段 4 同一个算式（场上没有【倡义】的角色数、上限 3），在这一刻现算。加点落在拼点帧的
    生效点数上（``Engine.boost_pindian``），牌面不动；``pindian`` delta 的 ``ranks`` 把它
    公开出去。点数已到 K 时加点没有效果——按 G2 仍允许（牌面写"可以"、没有收益也能点）。
    """
    owner = _owner_of(frame)
    if owner is None or frame.data.get(_CHANGYI_BOOST_DONE):
        return None
    x = changyi_x(host.state)
    answer = host.ask_option(
        frame,
        owner,
        Timing.RANK_COMPARE_PLACED,
        skill_id=f"{CHANGYI}:boost",
        options=(_CHANGYI_BOOST_OPTION,),
        optional=True,
        option_texts={_CHANGYI_BOOST_OPTION: f"令本次拼点的点数+{x}"},
    )
    if host.suspended(answer):
        return "stay"
    frame.data[_CHANGYI_BOOST_DONE] = True
    if not isinstance(answer, Pass):
        host.boost_pindian(frame, owner, x)
    return None


def _changyi(host: Any, frame: Any) -> Any:
    """【倡义】的结算主体：拼点 → 段 3（其没赢）→ 段 4（你没赢）。

    段机按**牌面书写顺序**（用户 2026-11-06 裁定 Q6：先授予、后二选一），每段都带帧内
    游标：

    - 拼点：``Engine.rank_compare`` 压 ``RANK_COMPARE`` 子帧，等结果回写到本帧
      （``pindian_result`` / ``pindian_won`` / ``pindian_tied``）；
    - 段 3（其没赢 = 对方没赢）：问"令其获得【倡义】"（「可以」⇒ 有 Pass）；
    - 段 4（你没赢）：二选一，**须选**；选项 1 付不起 / 没有目标 ⇒ 置灰（只剩选项 2 时
      界面走直提态）；
    - 收尾：落一次**每轮已发动**（计数落在收尾，续跑不重复计数——与【从讨】同一条纪律）。

    平点的读法：``won`` 假且 ``tied`` 真 ⇒ **段 3 与段 4 都跑**（规则 12.1 L1318–L1319
    「平点 ⇒ 双方都没赢」）。
    """
    state = host.state
    owner = _owner_of(frame)
    if owner is None or frame.data.get(_CHANGYI_SETTLED):
        return None
    opponent = _changyi_opponent(frame, owner)
    if opponent is None:
        return None
    if "pindian_result" not in frame.data:
        # 发起拼点（对手恒为「其」，不另选人）。
        host.rank_compare(
            frame,
            initiator=owner,
            targets=(opponent,),
            skill_id=CHANGYI,
            title=CHANGYI_TITLE,
        )
        return "stay"
    results = tuple(frame.data.get("pindian_result") or ())
    won = bool((frame.data.get("pindian_won") or (False,))[0])
    tied = bool((frame.data.get("pindian_tied") or (False,))[0])
    opponent_did_not_win = bool(results) and (won or tied)
    owner_did_not_win = bool(results) and not won
    # 段 3：其没赢 ⇒ 可以令其获得【倡义】（复制；已持有则不发这一问，G2）。
    if opponent_did_not_win and not frame.data.get(_CHANGYI_GRANTED):
        if opponent in changyi_holders(state):
            frame.data[_CHANGYI_GRANTED] = True
        else:
            answer = host.ask_option(
                frame,
                owner,
                Timing.PHASE_PLAY_END,
                skill_id=f"{CHANGYI}:grant",
                options=(_CHANGYI_GRANT_OPTION,),
                optional=True,
                option_texts={_CHANGYI_GRANT_OPTION: "令其获得【倡义】"},
            )
            if host.suspended(answer):
                return "stay"
            if not isinstance(answer, Pass):
                host.grant_skill(opponent, CHANGYI, frame=frame)
            frame.data[_CHANGYI_GRANTED] = True
    # 段 4：你没赢 ⇒ 须二选一。
    if owner_did_not_win:
        control = _changyi_choice(host, frame, owner)
        if control == "stay":
            return "stay"
    frame.data[_CHANGYI_SETTLED] = True
    mark_round_skill_used(state, owner, CHANGYI)
    return None


def _changyi_choice(host: Any, frame: Any, owner: int) -> Any:
    """段 4：问一次二选一（须选），再跑选中的那一支。

    X 在这一刻现算（授予段可能已经改变持有面，裁定 Q6）。选项 1 在此刻**付不起**（可弃的
    牌不足 X）或**没有目标**（全场都有【倡义】）⇒ 置灰不可选；两项都不可执行 ⇒ 整条取消
    （零副作用，`edge-cases.md` 总规则 1 / 3.1）。
    """
    state = host.state
    choice = frame.data.get(_CHANGYI_CHOICE)
    if choice is None:
        x = changyi_x(state)
        targets = _changyi_targets(state)
        payable = len(state.player(owner).hand) + len(state.player(owner).equip) >= x
        disabled: tuple[str, ...] = ()
        if not payable or not targets:
            disabled = (_CHANGYI_OPTION_PUNISH,)
        if len(disabled) == len(_CHANGYI_OPTIONS):
            # 防御分支：只有"两项都不可执行"才会走到这里——今天不可能（选项 2 恒可执行）。
            frame.data[_CHANGYI_CHOICE] = ""
            return None
        answer = host.ask_option(
            frame,
            owner,
            Timing.PHASE_PLAY_END,
            skill_id=f"{CHANGYI}:choice",
            options=_CHANGYI_OPTIONS,
            optional=False,
            disabled=disabled,
            option_texts={
                _CHANGYI_OPTION_PUNISH: f"弃置至少{x}张牌，并对一名没有【倡义】的角色造成1点伤害",
                _CHANGYI_OPTION_DRAW: f"摸{x}张牌，然后你失去此技能",
            },
        )
        if host.suspended(answer):
            return "stay"
        choice = str(answer.choice)
        frame.data[_CHANGYI_CHOICE] = choice
    if choice == _CHANGYI_OPTION_PUNISH:
        return _changyi_punish(host, frame, owner)
    if choice == _CHANGYI_OPTION_DRAW:
        return _changyi_draw(host, frame, owner)
    return None


def _changyi_punish(host: Any, frame: Any, owner: int) -> Any:
    """段 4-1 + 4-2：「弃置至少 X 张牌」→「对一名没有【倡义】的角色造成 1 点伤害」。

    X **此刻现算**（授予段可能已经改变持有面）。弃置池 = 手牌 + 装备区（牌前没有区域限定，
    默认读法（一）②）；弃哪些牌由**曹操自己**选（与他自己的弃牌阶段同形）。
    """
    state = host.state
    if frame.data.get(_CHANGYI_PUNISHED):
        return None
    x = changyi_x(state)
    cards = frame.data.get(_CHANGYI_CARDS)
    if cards is None:
        answer = host.ask_selection(
            frame,
            owner=_ASK_CHANGYI_CARDS,
            key=f"changyi:cards:{x}",
            actor=owner,
            timing=Timing.PHASE_PLAY_END,
            spec=SelectionSpec(
                zones=(Zone.HAND, Zone.EQUIP),
                # 「至少 X 张」= 下限 X、上界 = 此刻能弃的全部（``SelectionSpec`` 的
                # ``total`` 是闭区间，没有"无上限"这个值）。
                total=(x, len(state.player(owner).hand) + len(state.player(owner).equip)),
                optional=False,
            ),
            # 自己的牌 ⇒ 可见槽（与【制衡】/【镇骨】的自选口径一致）。
            build_face=lambda: host.selection_face(
                owner, (Zone.HAND, Zone.EQUIP), reveal_hand=True
            ),
        )
        if host.suspended(answer):
            return "stay"
        cards = tuple(int(card_id) for card_id in answer)
        frame.data[_CHANGYI_CARDS] = cards
    if cards and not frame.data.get(_CHANGYI_PAID):
        # 搬牌可能内联派发"失去牌后"而挂起 ⇒ 分组搬 + "已付"游标（重入不重搬）。
        if _move_by_source(
            host,
            frame,
            tuple(int(card_id) for card_id in cards),
            to_zone=ZoneRef(Zone.DISCARD),
            owner=owner,
            key="changyi:cost",
        ) == "stay":
            return "stay"
        frame.data[_CHANGYI_PAID] = True
    target = frame.data.get(_CHANGYI_TARGET)
    if target is None:
        pool = _changyi_targets(state)
        if not pool:
            # 池在飞期间变空（被授予了 / 死了）⇒ 伤害落空，但弃牌已经付过。
            frame.data[_CHANGYI_TARGET] = -1
            frame.data[_CHANGYI_PUNISHED] = True
            return None
        answer = host.ask_option(
            frame,
            owner,
            Timing.PHASE_PLAY_END,
            skill_id=f"{CHANGYI}:target",
            options=tuple(seat_choice(seat) for seat in pool),
            optional=False,
            option_texts={seat_choice(seat): f"对{seat}号位造成1点伤害" for seat in pool},
        )
        if host.suspended(answer):
            return "stay"
        target = int(str(answer.choice).removeprefix(SEAT_PREFIX))
        frame.data[_CHANGYI_TARGET] = target
    if int(target) < 0 or not state.player(int(target)).alive:
        frame.data[_CHANGYI_PUNISHED] = True
        return None
    frame.data[_CHANGYI_PUNISHED] = True
    _push_changyi_damage(host, frame, source=owner, target=int(target))
    return "stay"


def _changyi_draw(host: Any, frame: Any, owner: int) -> Any:
    """段 4-3 + 4-4：「摸 X 张牌」→「然后你失去此技能」。

    X **此刻现算**；短摸 ⇒ 平局（既有 ``draw_for_hand`` 语义）。失去是**不可恢复**的
    （账本 L762）——这也是【鸿志】条件②之后能成立的那条出口。
    """
    state = host.state
    if not frame.data.get(_CHANGYI_DRAWN):
        if host.draw_for_hand(frame, owner, changyi_x(state)) == "stay":
            return "stay"
        frame.data[_CHANGYI_DRAWN] = True
    if not frame.data.get(_CHANGYI_LOST):
        host.lose_skill(owner, CHANGYI, frame=frame)
        frame.data[_CHANGYI_LOST] = True
    return None


def _push_changyi_damage(host: Any, frame: Any, *, source: int, target: int) -> None:
    """压一个**无渠道牌**的 ``DAMAGE`` 子帧（「造成 1 点伤害」= 普通伤害、一个伤害事件）。"""
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
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )


# ---------------------------------------------------------------------------
# 定义
# ---------------------------------------------------------------------------

_HONGZHI = SkillDef(
    skill_id=HONGZHI,
    locked=True,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        # ① 回合开始时：无手牌 ⇒ 摸到 min(体力上限, 5)。
        Hook(
            Timing.TURN_START,
            handler=_hongzhi_turn_start,
            condition=_hongzhi_condition_turn_start,
        ),
        # ② 回合结束时：场上无【倡义】⇒ 手牌数调整为 min(体力上限, 5)（双向）。
        Hook(
            Timing.TURN_END,
            handler=_hongzhi_turn_end,
            condition=_hongzhi_condition_turn_end,
        ),
    ),
    requirement=(
        "锁定技，若你回合开始时无手牌或回合结束时场上无【倡义】，"
        "将手牌数调整为等同于体力上限（至多为5）"
    ),
    step_requirements={
        "card": "弃置若干手牌，使手牌数等于体力上限（至多为5）",
    },
)
"""【鸿志】：一个 SkillDef、两条 Hook（与【勤学】同形，账本 L742）；锁定技 ⇒ 不问发动。"""

_CHANGYI = SkillDef(
    skill_id=CHANGYI,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(Timing.PHASE_PLAY_END, handler=_changyi, condition=_changyi_condition),
        # 段 2「当你拼点时，你可以令点数+X」：同一个技能的第二条钩子，挂在"扣置后、亮出前"。
        # ``forced=True`` ⇒ 不问"是否发动"（那一问就是"点数 +X 吗"，由 handler 带 Pass 问）。
        Hook(
            Timing.RANK_COMPARE_PLACED,
            handler=_changyi_boost,
            condition=_changyi_boost_condition,
            forced=True,
        ),
    ),
    requirement=(
        "每轮限1次，一名其他角色的出牌阶段结束时，你可以与其进行1次拼点，"
        "若其没赢，你可以令其获得【倡义】；若你没赢，须选择：1.弃置至少X张牌并对一名"
        "没有【倡义】的角色造成1点伤害；2.摸X张牌，然后你失去此技能"
        "（X为场上没有【倡义】的角色数且最多为3）。当你拼点时，你可以令点数+X"
    ),
    step_requirements={
        "grant": "令其获得【倡义】",
        "choice": "选择一项：弃牌并伤害 / 摸牌并失去此技能",
        "card": "弃置至少X张牌（手牌 + 装备区）",
        "target": "选择一名没有【倡义】的角色",
        "boost": "是否令本次拼点的点数+X",
    },
    per_round_limit=True,
)
"""【倡义】：`PHASE_PLAY_END` 钩子 + 手写段机；`per_round_limit` ⇒ 每轮按实例 1 次。"""


def caocao_skill_definitions() -> tuple[SkillDef, ...]:
    """自制包·韬·曹操的两条技能定义（挂进 :data:`~sgs.skills.catalog.CATALOG`）。"""
    return (_HONGZHI, _CHANGYI)
