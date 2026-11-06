"""各帧的步骤表：把官方流程写成**数据**（票 04 §1，票 07 §2–§3）。

- **USE 帧**（使用一张牌）：L4 六个时机 → 压入 RESOLVE 子帧 → L5.6 把处理区里的
  实体牌置入弃牌堆。实体牌在 L4.1 / L4.2 之间从手牌移入处理区（票 07 §3）。
- **RESOLVE 帧**（逐目标结算）：L5 五个时机；"生效前"按**行为表**的 ``response_window``
  分派响应窗口（票 28）——【杀】给目标开【闪】窗口、锦囊开**收集式无懈窗口**
  （全体存活每人每层一次、收齐后只有优先级最高者实际使用）。被抵消则
  ``Goto(RESOLVE_END_PC)`` 跳过"生效时 / 生效后"，直达"使用结算结束时"。
  无懈的成败由**子帧出栈时**向挂起帧传播（``Engine._propagate_cancel``），
  故嵌套就是"帧上叠帧"、不新增帧类型。
- **DAMAGE 帧**（票 20）：L7 五个时机；"受到伤害时"之后压入 **LIFE_DECREASE** 子帧，
  在"造成伤害后"之前把体力真正扣掉。
- **LIFE_DECREASE 帧**（票 20 / 21）：L8 三个时机；"扣减体力时"是唯一的体力写入点。
  体力降到 0 及以下就在那里插入 **NEAR_DEATH** 子帧（票 21）；救回则照常走完"扣减体力后"。
- **NEAR_DEATH 帧**（票 21 / 票 31）：L12 三个时机；"处于濒死状态时"按官方顺序逐个求桃
  （当前回合角色起、逆时针、一人一次），有人出桃则压入 **RECOVER** 子帧、在其中把
  体力恢复 1 点，然后回到被中断的结算。**新濒死回退**（票 20 / 票 31）＝同一帧的
  时机级回退：某次响应**严格回复过体力但仍 < 1** ⇒ ``Goto`` 跳回"处于濒死状态时"
  开出一个新窗口，新区间 = 旧应答者区间的**后缀**（起点 = 刚响应完的座位）；``ENTER`` /
  ``AFTER`` 全程各恰一次，终局清栈形状零变化。无人救时在"濒死结算结束后"压入
  **DEATH** 子帧（票 22）——死亡结算作为原子单元跑完，濒死帧与整条伤害链留给终局清栈。
- **DEATH 帧**（票 22 / 票 25）：L13 裁剪后的死亡段，**按模式分叉**。无身份局四段
  （`DEATH_REVEAL_IDENTITY_BEFORE` 不入表、不 dispatch）；身份局五段——入表并把身份
  亮明成一条 ``identity_reveal``（在时机 dispatch 之后、胜负检测之前）。`DEATH_ON` 只
  dispatch 死亡时技能；Ⅰ.a（弃置死者区域里的牌）与 Ⅱ 奖惩在紧随其后的**非 dispatch 步**
  （`death_settle_cards`，票 33 拆开：奖惩里的搬牌会挂起，续跑不许重发 `DEATH_ON` 记录）。
  检测点在 `DEATH_REVEAL_IDENTITY_AFTER`，只记暂定胜负、不在那里终止（批次关闭由引擎的
  DEATH 出栈逻辑宣布）。
- **RECOVER 帧**（票 21）：L10 两个时机；"确定回复数值"之后把体力写高（夹在上限内）。
- **JUDGE 帧**（票 26）：L14 三个时机；四步 = 取牌入处理区（复用摸牌原语、沿用 ``shuffle``
  子流、不算摸牌）→ 改判循环窗口（``RESPOND`` + ``WindowCause.REJUDGE``：每次替代后重开、
  每座位一次、按存活重滤；无能力者 ⇒ 队列恒空）→ 结果 → 判定牌入弃牌堆后调后果 op
  （被【天妒】等获得则跳过入弃牌堆）。帧私有 ``subject`` / ``judge_card`` 与渠道牌
  ``card`` 分列（票 10 §1）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from sgs.data import Identity, Mode
from sgs.effects import (
    DODGE_RESPONSE_CARD,
    NEED_CARD_FLOOR_KEY,
    NULLIFY_RESPONSE_CARD,
    PEACH_RESPONSE_CARD,
    DamageNature,
    EffectStep,
    FinalZone,
    ResponseWindow,
    behavior_for,
    damage_amount,
    is_slash,
    ModifierRole,
    need_card_floor,
    recover_amount,
    reset_need_card_floor,
    response_count,
    target_is_effective,
)
from sgs.resolution import Control, Frame, Goto, Step, StepHost, StepTable, Tag, has_tag
from sgs.skills import (
    DAMAGE_DEAL_POINT_KEY,
    DAMAGE_DEAL_RECORDED_KEY,
    DAMAGE_POINT_KEY,
    DAMAGE_RECORDED_KEY,
    JIE_JIUYUAN_REPLACED_KEY,
    QIANXUN_PLACED_KEY,
    RejudgeOriginal,
    SeamKind,
    activate_skill,
    rejudge_capable_seats,
    rejudge_offers,
    target_is_ineffective,
    virtual_dodge,
)
from sgs.state import (
    LIFE_DECREASE_DAMAGE_KEY,
    ActivateSkill,
    CardRef,
    PindianDelta,
    DodgeContext,
    FrameKind,
    GameState,
    MoveReason,
    Pass,
    PeachContext,
    QueryKind,
    RespondMode,
    RejudgeOffer,
    SelectionSpec,
    SubstituteJudgeCard,
    UseCard,
    WindowCause,
    Zone,
    ZoneRef,
    rejudge_askers,
    seats_from,
)
from sgs.timing import Timing

__all__ = ["RESOLVE_END_PC", "build_steps"]


class FrameHost(StepHost, Protocol):
    """步骤函数看到的**引擎面**：在票 04 的最小 ``StepHost`` 上补出使用流程要的操作。

    引擎（:class:`~sgs.engine.Engine`）结构上满足它；步骤表只经这些方法改状态，
    不直接摸结算栈，也不 import 引擎类型（避免 ``engine.steps`` ↔ ``engine.game`` 循环）。
    """

    state: GameState

    def emit_frame_timing(
        self, frame: Frame, timing: Timing, *, actor: int | None = None, cards: Sequence[CardRef] = ()
    ) -> None: ...

    def current_target(self, frame: Frame) -> int: ...

    def apply_timing_ops(self, frame: Frame, timing: Timing) -> Any: ...

    def note_damage_dealt(self, seat: int) -> bool: ...

    def move_used_card_to_processing(self, frame: Frame) -> Control: ...

    def move_used_card_to_judge_zone(self, frame: Frame) -> None: ...

    def move_used_card_to_equip_zone(self, frame: Frame) -> Control: ...

    def move_processing_card_to_discard(self, frame: Frame) -> None: ...

    def draw_judge_card(self, frame: Frame) -> bool: ...

    def move_judge_card_to_processing(self, frame: Frame) -> None: ...

    def discard_judge_card(self, frame: Frame) -> None: ...

    def substitute_judge_card(
        self, frame: Frame, *, seat: int, new_card: CardRef, skill_id: str
    ) -> Control: ...

    def apply_judge_after(self, frame: Frame) -> None: ...

    def skip_play_phase(self, seat: int) -> None: ...

    def end_play_phase(self, seat: int) -> None:
        """立刻结束**进行中**的出牌阶段（与玩家回 ``EndPhase`` 同一收尾；关 92 票）。"""
        ...

    def consume_delayed_card(self, frame: Frame) -> None: ...

    def enter_death(self, frame: Frame, seat: int) -> Frame: ...

    def pass_away(self, frame: Frame, seat: int) -> Frame: ...

    def outcome_decided(self) -> bool: ...

    def discard_dead_cards(self, frame: Frame) -> None: ...

    def reveal_dead_identity(self, frame: Frame) -> None: ...

    def apply_death_rewards(self, frame: Frame) -> Control: ...

    def record_death_fact(self, frame: Frame) -> None: ...

    def check_game_end(self) -> Any: ...

    def push_frame(self, kind: FrameKind, data: dict[str, Any], *, timing: Timing) -> Frame: ...

    def dispatch_seam(self, frame: Frame, kind: SeamKind) -> Any: ...

    def provider_registered(self, skill_id: str) -> bool: ...

    def advance_provider(self, frame: Frame, timing: Timing) -> Any:
        """``(control, satisfied, card)``：重入当前在展开的提供者（见 ``sgs.skills``）。"""
        ...

    def start_provider(
        self, frame: Frame, skill_id: str, actor: int, timing: Timing, *, required: str
    ) -> Any:
        """``(control, satisfied, card)``：开始一次提供者展开；``required`` = 要哪张牌。"""
        ...

    def open_skill_judgment(self, frame: Frame, subject: int, skill_id: str) -> None: ...

    def ask_selection(
        self,
        frame: Frame,
        *,
        owner: Any,
        key: str,
        actor: int,
        timing: Timing,
        spec: Any,
        face: Any = None,
        revealed: Sequence[CardRef] = (),
        provide_type: str | None = None,
        ask_when_empty: bool = False,
    ) -> Any: ...

    def suspended(self, value: Any) -> bool: ...

    def selection_face(
        self, seat: int, zones: tuple[Any, ...], *, reveal_hand: bool = False
    ) -> tuple[tuple[Any, ...], tuple[int, ...]]: ...

    def move_in_frame(
        self,
        frame: Frame,
        cards: tuple[CardRef, ...],
        *,
        to_zone: ZoneRef,
        reason: MoveReason,
        owner: int | None,
        actor: int | None = None,
        to_top: bool = False,
    ) -> Control: ...

    def emit_frame_delta(self, frame: Frame, delta: Any) -> None: ...

    def note_pindian_result(self, frame: Frame) -> None: ...

    def boost_pindian(self, frame: Frame, seat: int, amount: int) -> None:
        """把 ``seat`` 的**本次拼点点数**加 ``amount``（tao-caocao 票 03）。

        调用点 = 挂在 ``RANK_COMPARE_PLACED`` 上的技能（"牌扣置入处理区后、亮出前"）：
        规则 12.2 L1326–L1327 的点数比较读**生效点数**，故技能改的是这里，不是牌面。
        写入的是帧私有 ``RANK_COMPARE_BOOST``（``{座位: 增量}``），**不进记录流**；
        亮出比点时按 ``min(13, max(1, 牌面 + 增量))`` 参与比较（规则 12.2 第 4 步的
        上下限处理）。
        """
        ...

    def ask_query(
        self,
        kind: QueryKind,
        actor: int,
        timing: Timing,
        *,
        frame: Frame | None = None,
        dying: int | None = None,
        payload: Any = None,
    ) -> Any: ...

    def ask_respond(
        self,
        frame: Frame,
        actor: int,
        timing: Timing,
        *,
        cause: WindowCause,
        required: str | None = None,
        target: int | None = None,
        window_frame: int = 0,
        mode: RespondMode = RespondMode.PLAY,
        use_targets: tuple[int, ...] = (),
        zones: tuple[Zone, ...] = (Zone.HAND,),
        rejudge_offers: tuple[RejudgeOffer, ...] = (),
    ) -> Any: ...

    def respond_candidates(
        self,
        actor: int,
        *,
        cause: WindowCause,
        mode: RespondMode = RespondMode.PLAY,
        rejudge_offers: tuple[RejudgeOffer, ...] = (),
    ) -> tuple[Any, ...]: ...

    def suspended(self, value: Any) -> bool: ...

    def selection_face(
        self, seat: int, zones: tuple[Any, ...], *, reveal_hand: bool = False
    ) -> tuple[tuple[Any, ...], tuple[int, ...]]: ...

    def accepted_selection(
        self, frame: Frame, *, owner: Any, key: str
    ) -> tuple[int, ...] | None: ...

    def accepted_option(self, frame: Frame, *, skill_id: str, actor: int) -> Any | None: ...

    def start_use(
        self,
        card: CardRef,
        *,
        source: int,
        targets: tuple[int, ...],
        use_timing: Timing | None = None,
        cancel_target: tuple[int, int] | None = None,
    ) -> Frame: ...

    def recover_from_peach(
        self, frame: Frame, card: CardRef, *, asker: int, dying: int
    ) -> None: ...

    def apply_effects(
        self, frame: Frame, effects: Sequence[EffectStep], *, timing: Timing
    ) -> Any: ...

    def apply_hp_change(self, frame: Frame, seat: int, value: int) -> Any: ...

    def apply_hp_max_change(self, frame: Frame, seat: int, value: int) -> Any:
        """把体力上限写成 ``value`` 并落一条 ``hp_max_change``（``MAXLIFE`` 帧的写入口）。"""
        ...

    def move_in_frame(
        self,
        frame: Frame | None,
        cards: tuple[CardRef, ...],
        *,
        to_zone: ZoneRef,
        reason: MoveReason,
        owner: int | None,
        actor: int | None = None,
        to_top: bool = False,
    ) -> Control:
        """``to_top``（票 02）：落点即牌堆顶的同一次移动，不进牌堆尾（见 ``Engine``）。"""
        ...

    def draw_for_hand(self, frame: Frame, seat: int, n: int) -> bool | str: ...

    def set_draw_phase_count(self, seat: int, count: int) -> None: ...

    def draw_phase_count(self) -> int: ...

    def set_chained(self, frame: Frame | None, seat: int, chained: bool) -> bool: ...


def _emit(
    host: FrameHost,
    frame: Frame,
    timing: Timing,
    *,
    actor: int | None = None,
    cards: Sequence[CardRef] = (),
    targets: tuple[int, ...] | None = None,
    event: Any = None,
    emit_record: bool = True,
) -> Control:
    """以 ``frame`` 为主体 dispatch 一个时机；``targets`` 缺省取帧的目标列表。

    C 段的时机记录按票 11 §3.3 取**当前目标**（``targets[cursor]``），故调用点显式给
    ``targets``；空目标的响应牌（【闪】/【无懈】）沿用空元组，与 M1 记录流一字不差。

    ``event`` 是**帧私有时机载荷**（票 07 的 ``MOVE_AFTER`` 用它带移动事件）；
    ``emit_record=False`` 表示这一轮只派发、不再落记录（票 05 的逐点触发）。

    返回 ``Control``（票 19 §8 / 票 30）：装备钩子可以在这里暂停去提问，步骤函数必须
    把返回值**原样透传**——``"stay"`` = 本次时机没派完，重入时从这里接着跑。
    """
    return host.emit_frame_timing(
        frame,
        timing,
        actor=actor,
        cards=cards,
        targets=targets,
        event=event,
        emit_record=emit_record,
    )


def _current_target(host: FrameHost, frame: Frame) -> int:
    """C 段的**当前目标**：``targets[cursor]``，空目标时退回使用者（票 11 §3.3）。"""
    return host.current_target(frame)


def _resolve_emit(host: FrameHost, frame: Frame, timing: Timing) -> Control:
    """C 段时机的统一发射口：actor = 当前目标、``targets`` = 当前目标（空目标时空元组）。"""
    targets = tuple(frame.data.get("targets", ()))
    current = _current_target(host, frame)
    return _emit(
        host,
        frame,
        timing,
        actor=current,
        targets=(current,) if targets else (),
    )


# ---- USE 帧：L4 六个时机 + 压入 RESOLVE + L5.6 ----------------------------


def use_choose_target(host: FrameHost, frame: Frame) -> Control:
    control = _emit(host, frame, Timing.USE_CHOOSE_TARGET, actor=frame.data["source"])
    if control is not None:
        return control
    # 实体牌离开手牌、进入处理区：发生在 L4.1 与 L4.2 之间（票 07 §3）。
    # 返回值必须透传：这次移动的"失去后"钩子可以在这里提问。
    return host.move_used_card_to_processing(frame)


def use_on_use(host: FrameHost, frame: Frame) -> Control:
    return _emit(host, frame, Timing.USE_ON_USE, actor=frame.data["source"])


def use_designate_target(host: FrameHost, frame: Frame) -> Control:
    return _emit(host, frame, Timing.USE_DESIGNATE_TARGET, actor=frame.data["source"])


def use_become_target(host: FrameHost, frame: Frame) -> Control:
    """"成为目标时"：**时机外层、目标内层**（`A/flow_use.txt:46`）。

    多目标时逐个目标各发一条；空目标的响应牌（【闪】/【无懈】）沿用 M1 的
    ``actor = None`` 单条记录，故 1v1 六条基准逐字节不变。

    **目标列表在派发中可能变**（票 12 的【流离】：把目标转移给另一名角色，见规则
    17.4/17.5 的"目标确定阶段"）：故这里**不预先快照**，而是每轮记下"哪些座位已经
    派发过"（``become_target_done``，帧私有），重入时跳过它们——转移后的新目标因此
    拿到自己的"成为目标时"，被转移掉的那一个不再被派发，且两边都恰好一次。
    帧私有数据不进记录流，故无武将的对局逐字节不变（34 条基准一行不改）。
    """
    targets = tuple(frame.data["targets"])
    if not targets:
        return _emit(host, frame, Timing.USE_BECOME_TARGET, actor=None)
    done: list[int] = frame.data.setdefault("become_target_done", [])
    for target in targets:
        if target in done:
            continue
        control = _emit(host, frame, Timing.USE_BECOME_TARGET, actor=target)
        if control is not None:
            return control
        done.append(target)
    return None


def use_after_designate_target(host: FrameHost, frame: Frame) -> Control:
    """"指定目标后"：目标已定、不再改变（`A/flow_use.txt:39`）。

    票 27 在这里跑本牌的 ``timing_ops``（【五谷丰登】的"亮出 X 张"）；票 30 在这里派发
    装备钩子（【雌雄双股剑】）。若那次动作没完整执行（牌不够），**终止这次使用结算**、
    直奔"使用结算结束后"（不判平局）。

    时机记录的幂等由 ``emit_frame_timing`` 的帧内键保证（钩子可能提问暂停），故这里
    不再自设 "emitted" 守卫——否则重入会跳过派发、钩子永远接不上。
    """
    control = _emit(host, frame, Timing.USE_AFTER_DESIGNATE_TARGET, actor=frame.data["source"])
    if control is not None:
        return control
    control = host.apply_timing_ops(frame, Timing.USE_AFTER_DESIGNATE_TARGET)
    if control is not None:
        return control
    if frame.data.get("use_terminated"):
        return Goto(USE_AFTER_END_PC)
    return None


def use_after_become_target(host: FrameHost, frame: Frame) -> Control:
    """"成为目标后"：同 B4，时机外层、目标内层；空目标沿用单条 ``actor = None``。"""
    targets = tuple(frame.data["targets"])
    if not targets:
        return _emit(host, frame, Timing.USE_AFTER_BECOME_TARGET, actor=None)
    for target in targets:
        control = _emit(host, frame, Timing.USE_AFTER_BECOME_TARGET, actor=target)
        if control is not None:
            return control
    return None


def use_push_resolve(host: FrameHost, frame: Frame) -> Control:
    """压入逐目标结算帧；**延时锦囊例外**——它的 L5 推迟到目标角色的判定阶段。

    延时锦囊在**使用当时**只走完 L4 六个时机（票 10 §3.1 / §7）：实体牌置入目标的
    判定区，L5 的 RESOLVE 帧由判定阶段的回合步骤压出（``_phase_judge``）。故本步只
    对非延时锦囊压栈，延时锦囊直接落到 ``use_after_end`` 的置入判定区。

    **目标减为 0 ⇒ 终止使用流程**（票 11 §5）：目标为空且这张牌的规则本来要目标时，
    不压 RESOLVE、直达"使用结算结束后"（实体牌入弃牌堆）。响应牌（额定目标数为 0 的
    【闪】/【无懈】）不在此列——它们的空目标 RESOLVE 必须照跑。
    """
    behavior = behavior_for(frame.data["card"])
    if behavior.is_delayed:
        return None
    rule = behavior.target_rule
    if not frame.data["targets"] and rule.target_count(1) > 0:
        return Goto(USE_AFTER_END_PC)
    host.push_frame(FrameKind.RESOLVE, dict(frame.data), timing=Timing.RESOLVE_START)
    return None


def use_after_end(host: FrameHost, frame: Frame) -> Control:
    """实体牌的最终去向 + "使用结算结束后"。

    默认进弃牌堆（M1 语义）；延时锦囊置入目标的**判定区**（票 10 §7），并且**不**在这里
    发 ``RESOLVE_AFTER_END``——那一步归判定阶段循环（票 10 §3.6）；装备牌**留装备区**
    （``final_zone = EQUIP``，票 12 §2）：同槽已有牌则先被"顶替"出去（``LOSE``，不算弃置）。
    票 27 在这里补跑本牌的 ``timing_ops``（【五谷丰登】把亮牌中剩余的牌置入弃牌堆）。

    **顶替的返回值必须透传**（票 06，``CODING_STANDARDS.md`` §3.1）：搬旧装备会内联派发
    "失去装备后"（白银狮子压 ``RECOVER`` 帧）并让出。吞掉这个 ``"stay"`` 就会在同一趟里
    接着把新装备 ``PUT`` 入槽——记录流次序变成"新装备入场 → 回复"，而 `hp` 数值与帧数都
    不变，旧回归看不见。重入时 ``after_end_done`` 仍是假（它在这段**之后**才落位），故本步
    原地重跑；"这次同槽扫描已经做过"由引擎侧帧私有游标 ``_EQUIP_SLOT_SCANNED`` 守住，不会把
    刚入槽的新装备当旧装备再搬一遍。
    """
    behavior = behavior_for(frame.data["card"])
    if behavior.is_delayed:
        host.move_used_card_to_judge_zone(frame)
        return None
    if not frame.data.get("after_end_done"):
        if behavior.final_zone is FinalZone.EQUIP:
            equip_control = host.move_used_card_to_equip_zone(frame)
            if equip_control is not None:
                return equip_control
        else:
            host.move_processing_card_to_discard(frame)
        emit_control = _emit(
            host,
            frame,
            Timing.RESOLVE_AFTER_END,
            actor=frame.data["source"],
            cards=(frame.data["card"],),
        )
        frame.data["after_end_done"] = True
        if emit_control is not None:
            return emit_control
    return host.apply_timing_ops(frame, Timing.RESOLVE_AFTER_END)


USE_STEPS = (
    Step(use_choose_target, Timing.USE_CHOOSE_TARGET),
    Step(use_on_use, Timing.USE_ON_USE),
    Step(use_designate_target, Timing.USE_DESIGNATE_TARGET),
    Step(use_become_target, Timing.USE_BECOME_TARGET),
    Step(use_after_designate_target, Timing.USE_AFTER_DESIGNATE_TARGET),
    Step(use_after_become_target, Timing.USE_AFTER_BECOME_TARGET),
    # 压栈那一步的 `timing` 只作子帧 `return_to` 的可读标签（票 04 §4）：
    # RESOLVE_START 由 RESOLVE 帧自己 dispatch，不该记在 USE 帧的 id 上。
    Step(use_push_resolve, Timing.RESOLVE_START),
    Step(use_after_end, Timing.RESOLVE_AFTER_END),
)
"""USE 帧：L4 六个时机（B 段用"时机外层、目标内层"）+ 压栈 + L5.6。"""


# ---- RESOLVE 帧：L5 五个时机（**逐目标**）+ 一个非 dispatch 游标步骤 ----------


def resolve_start(host: FrameHost, frame: Frame) -> Control:
    """"使用结算开始时"：**先检测这张牌对目标的有效性**（票 15 §5 的第 2 层）。

    无效 ⇒ **终止对该目标的结算**、直接推进游标（**不进 C2，故没有无懈窗口**）；
    有效 ⇒ 照常走 C2–C5。时机本身照记——它就是这个检测点。

    判据有两个来源、同一个终止点：既有的牌面 / 修饰器链
    （:func:`~sgs.effects.target_is_effective`），以及 M6 票 07 的**目标级否定**
    （:func:`~sgs.skills.target_is_ineffective`，【奋威】在 B5 写进帧私有键）。前者
    回答"这张牌本来对谁无效"，后者回答"这张牌这一次对谁被技能判无效"——两层语义在
    :mod:`GLOSSARY` 里同属「无效」，故共用同一个出口。

    C1 的 ``actor`` 保持 M1 的**使用者**（C3–C5 才是当前目标），故 1v1 基准逐字节不变。
    """
    targets = tuple(frame.data["targets"])
    current = _current_target(host, frame)
    control = _emit(
        host,
        frame,
        Timing.RESOLVE_START,
        actor=frame.data["source"],
        targets=(current,) if targets else (),
    )
    if control is not None:
        return control
    if targets and (
        # **目标级无效**（M6 票 07 的否定分类第 2 层）：【奋威】在 B5 把"本次使用对其中
        # 任意名目标无效"写进帧私有键；这里与既有的"牌面 / 修饰器判无效"共用同一个
        # 终止点——终止对该目标的结算，故**不开** C2 的无懈窗口。
        target_is_ineffective(frame, current)
        or not target_is_effective(
            host.state, frame.data["card"], frame.data["source"], current
        )
    ):
        return Goto(CURSOR_PC)
    return None


def resolve_before_effect(host: FrameHost, frame: Frame) -> Control:
    """**生效前**（C2）：按行为表的 ``response_window`` 分派响应窗口（票 11 §7 / 票 28）。

    - ``DODGE``：【杀】的【闪】窗口——M1 的询问与记录形状一字不动，票 30 在其候选里
      追加【八卦阵】的 "是否发动"；
    - ``NULLIFY``：锦囊的**收集式**无懈窗口——全体存活每人每层一次，收齐后只有
      优先级最高者实际使用；被抵消与否由子帧**出栈时**传播回来（票 11 §2）；
    - ``None``：不开窗口，照记时机后继续。

    重入次序是本步的关键：窗口期间本帧一直**挂起**在 C2，故子帧出栈时传播来的
    ``canceled`` 一定在下次重入可见（票 11 §2 的"出栈序即自底向上序"）。被【闪】抵消
    之后，票 30 的**帧内接缝**（【青龙偃月刀】/【贯石斧】）在这里派发。
    """
    if frame.data.get("use_timing") is Timing.NEAR_DEATH_AT:
        # 濒死求桃建立的使用：回复结算已由求桃窗口显式压好（票 21），故跳过
        # "生效时 / 生效后"（`Goto` 而不是 `None`——`None` 只表示"这一步做完了"，
        # 会顺着 pc 走进效果步骤，回复就被写第二遍）。
        control = _emit(host, frame, Timing.RESOLVE_BEFORE_EFFECT, actor=frame.data["source"])
        if control is not None:
            return control
        frame.data["canceled"] = False
        return Goto(RESOLVE_END_PC)
    if "canceled" in frame.data:
        # 本目标的响应窗口已经处理过：**按 canceled 标志**决定去向（票 07 §3）。
        # 无懈的成功与否由出栈传播写进来，故这条分支也是无懈的出口。
        if frame.data["canceled"]:
            control = _slash_canceled_seam(host, frame)
            if control is not None:
                return control
            if has_tag(frame, Tag.SAVED):
                # 【贯石斧】"依然造成伤害"：跳过"生效前 / 生效时"，直进"生效后"
                # （票 12 Q16 / 票 30；标志自票 08 起住帧标签袋）。
                return Goto(RESOLVE_AFTER_EFFECT_PC)
        return Goto(RESOLVE_END_PC) if frame.data["canceled"] else None
    behavior = behavior_for(frame.data["card"])
    window = behavior.response_window
    if window is None or (window is ResponseWindow.DODGE and not frame.data["targets"]):
        control = _emit(host, frame, Timing.RESOLVE_BEFORE_EFFECT, actor=frame.data["source"])
        if control is not None:
            return control
        frame.data["canceled"] = False
        return None
    target = _current_target(host, frame)
    control = _open_response_window(host, frame)
    if control is not None:
        return control
    if has_tag(frame, Tag.UNRESPONSIVE, target=target):
        # 帧标签只负责"跳过本窗口"（票 08 §要做什么 6）：窗口该不该存在仍由行为表决定，
        # 被【铁骑】判红的目标这里**不发任何询问**（【八卦阵】一类的"视为使用【闪】"也一并封）。
        frame.data["canceled"] = False
        return None
    if window is ResponseWindow.DODGE:
        return _dodge_window(host, frame, target)
    return _collected_response_window(host, frame, target)


def _slash_canceled_seam(host: FrameHost, frame: Frame) -> Control:
    """【杀】被抵消时的**帧内接缝**（票 12 §Q12 / 票 30）。

    ``Timing`` 里没有"杀被抵消时"节点，故键是 :class:`~sgs.skills.SeamKind`；接缝只在
    **被【闪】抵消的【杀】**上开，且提供者（青龙偃月刀 / 贯石斧）互斥（同为武器槽）。
    """
    card = frame.data.get("card")
    if card is None or not is_slash(behavior_for(card)):
        # M5-05b：按【杀】族判据（共享 ``count_key``）而不是产物牌型相等——火【杀】/
        # 雷【杀】被【闪】抵消时与普通【杀】同形地进接缝。引擎仍不认识任何具体牌名。
        return None
    return host.dispatch_seam(frame, SeamKind.SLASH_CANCELED)


def _open_response_window(host: FrameHost, frame: Frame) -> Control:
    """在"生效前"发**一次**时机记录：响应窗口的开窗点（两种窗口共用）。"""
    if frame.data.get("window_open"):
        return None
    control = _resolve_emit(host, frame, Timing.RESOLVE_BEFORE_EFFECT)
    if control is not None:
        return control
    frame.data["window_open"] = True
    return None


def _dodge_window(host: FrameHost, frame: Frame, target: int) -> Control:
    """【闪】响应窗口：真牌窗口 + "需要牌"提供者序列（票 10 §5 / 票 30 / 票 08 / 票 09）。

    - 提供者（【八卦阵】/【护驾】）在候选里以 ``ActivateSkill`` 出现；选中即**惰性展开**：
      【八卦阵】压判定帧（红 ⇒ 需要被满足）、【护驾】开**势力求助窗口**（同势力角色打出
      【闪】⇒ 需要被满足）。两条路都由技能层的提供者注册表分派，引擎不认具体技能 id。
    - 真【闪】⇒ 抵消，【闪】自己照走 L4 / L5（M1 语义）。**只有能当【闪】的
      ``UseCard`` 算响应**：候选生成已按 ``required`` 牌名收口（B1），这里按同一牌名
      再判一次，避免把【无懈可击】这类牌当成【闪】抵消掉【杀】。

    **本次窗口需要几张**由 :func:`~sgs.effects.response_count` 给出（缺省 1；【无双】改成 2）：
    每成功一次（真【闪】或提供者）``dodges_done`` 加一，**攒够所需张数才算抵消**；
    没攒够就重开真牌窗口（已问过的提供者由 ``providers_used`` 过滤掉，故"每提供者只问一次"
    在张数为 2 时也成立）。没攒够而放弃 ⇒ ``canceled = False``、照常结算。

    **提供者可能跨多次重入**（求助窗口逐个问人）：引擎在窗口顶部先 :meth:`advance_provider`
    重入它，收尾后再按它写回的"是否满足"记一次成功（票 09）。

    **产物由本窗口交付**（票 03）：提供者交回的那张虚拟【闪】与真【闪】走**同一条**使用链
    （``start_use``），收尾共用 :func:`_accept`；没有产物 = 主公技式（别人已打出过一张），
    只记一次成功、不压帧。

    **优先级档**（票 03 §4-2 / §4-3）：本窗口读 :func:`~sgs.effects.need_card_floor` 写进
    ``DodgeContext``——选中一条之后档由 :meth:`~sgs.engine.Engine.ask_query` 记进承载帧，
    更高优先（更小档）的候选下一次不再进面。曹操先发动【八卦阵】（装备档）⇒ 判定黑 ⇒
    下一次询问里没有〖护驾〗（武将档），而真【闪】（使用牌档）照旧可点；floor 在
    :func:`_accept` 里清回最高档，故【无双】的第二张【闪】仍可发动【八卦阵】。
    """
    # 这一问的响应者恒是这张【杀】给定的目标（``_dodge_window`` 逐目标开窗）⇒ ``role``
    # 标 ``TARGET``：与【决斗】用同一句"谁在响应"的事实（票 23）。【无双】①只看使用者
    # 那一侧，读不读这一位都不变，但契约要写全。
    required = response_count(
        host.state,
        source=frame.data["source"],
        card=frame.data["card"],
        target=target,
        role=ModifierRole.TARGET,
    )
    done = frame.data.get("dodges_done", 0)

    def _accept(card: CardRef | None, *, use_timing: Timing | None = None) -> None:
        """一次【闪】响应成立：攒够所需张数即把本层标成被抵消。

        产物牌（提供者交回的那张虚拟【闪】）走与真【闪】**同一条**使用链；``None`` = 提供者
        没有交付产物（主公技式：别人已经打出过一张，牌已经付清，窗口不必再压帧）。

        **这里是唯一一处"这一次需要牌事件成立"的落点**（票 03 §4-3）：``dodges_done`` 每加
        一即把不升档的 floor 清回最高档——【无双】的两张【闪】是**两次**需要牌事件，第二张
        仍可从任意档起手（第一张用真牌，第二张照样能发动【八卦阵】）。
        """
        nonlocal done
        done += 1
        frame.data["dodges_done"] = done
        reset_need_card_floor(frame, target)
        if done >= required:
            frame.data["canceled"] = True
        if card is not None:
            host.start_use(
                card, source=target, targets=(), use_timing=use_timing
            )

    control, satisfied, produced = host.advance_provider(
        frame, Timing.RESOLVE_BEFORE_EFFECT
    )
    if control is not None:
        return control
    if satisfied is not None:
        if satisfied:
            _accept(produced)
        return "stay"
    if "skill_result" in frame.data:
        success = frame.data.pop("skill_result")
        if success:
            # "视为使用【闪】"仍是**一次**使用：走与真牌同一条使用链（票 08 的一手读法）。
            _accept(
                virtual_dodge(host.state),
                use_timing=Timing.RESOLVE_BEFORE_EFFECT,
            )
            return "stay"
    providers_used = tuple(frame.data.get("providers_used", ()))
    action = host.ask_query(
        QueryKind.DODGE,
        target,
        Timing.RESOLVE_BEFORE_EFFECT,
        frame=frame,
        payload=DodgeContext(
            providers_used=providers_used,
            floor=need_card_floor(frame, target),
        ),
    )
    if host.suspended(action):
        return "stay"
    if isinstance(action, ActivateSkill):
        frame.data.setdefault("providers_used", []).append(action.skill_id)
        if not host.provider_registered(action.skill_id):
            host.open_skill_judgment(frame, target, action.skill_id)
            return "stay"
        control, satisfied, produced = host.start_provider(
            frame,
            action.skill_id,
            target,
            Timing.RESOLVE_BEFORE_EFFECT,
            required=DODGE_RESPONSE_CARD,
        )
        if control is not None:
            return control
        if satisfied:
            _accept(produced)
        return "stay"
    if isinstance(action, UseCard) and action.card.type == DODGE_RESPONSE_CARD:
        # 抵消是"被响应帧"的标志；【闪】自己作为一次**使用**照走 L4 / L5（票 07 §3）。
        # 与另两条成功分支共用 ``_accept``：计一次成功 + 清 floor 只写在那里。
        _accept(action.card)
    else:
        # ``Pass``，或一张**不是【闪】**的 ``UseCard``（B1 的第二道闸）：候选已按
        # ``required`` 牌名收口（``actions._required_hand``），这里不再 fail-open——
        # 不把别的牌记成响应，也不替它压使用帧；【杀】照常结算。
        frame.data["canceled"] = False
    # 重入本步：等【闪】的 USE 帧出栈后，由上一条 ``canceled`` 分支决定去向；
    # 张数没攒够则继续问下一次响应。
    return "stay"


def _collected_response_window(host: FrameHost, frame: Frame, target: int) -> Control:
    """**收集式**响应窗口（票 11 §2 / 票 19 §9）：收齐全部声明，只有最高优先级者打出。

    窗口的"层"由**挂起帧**区分（``window_frame`` = 本帧 id）；响应牌自身那次使用也走
    ``RESOLVE`` 帧，于是嵌套就是"帧上叠帧"——**不新增帧类型**。赢家那张牌记住它指向
    "本帧 + 当前目标"（``cancel_target``），出栈时由引擎把那一层标成被抵消。

    声明**按询问序追加**，故首个声明者就是最高优先级者（应答者区间 =
    当前回合角色起逆时针、按座位序，票 15 §2）；输家的声明不耗牌、不压任何帧。

    **提供者（票 03 的 C1 缺口第三段）**：候选里的 ``ActivateSkill`` 走技能层通用派发，
    它的产物（【蛊惑】扣置的牌当成的虚拟【无懈可击】）**只算一条声明**——与真牌同一姿态
    进 ``response_declared``，此刻不耗牌、不压帧；真正的使用由下面的赢家分支统一发起。
    这是六扇窗口里**唯一**"产物先记账、后交付"的一扇：收集式是"先声明、收齐后由优先者
    使用"，赢家未定时谁都不该把牌用掉。

    声明已存在时**不再派发提供者**（``declared`` 随询问下传，候选面据此收口）：赢家已定，
    再扣置一张牌只会让那张牌白白离开手牌——质疑段已经答过，可它不会进结算。
    """
    if frame.data.get("response_used"):
        # 赢家的响应牌使用已完整结算：出栈传播（若成功）会先被上面的 ``canceled``
        # 分支接住；走到这里就是**没被抵消**，照常继续 C3–C5。
        return None
    if "response_askers" not in frame.data:
        frame.data["response_askers"] = seats_from(
            host.state, host.state.turn_seat, len(host.state.players)
        )
        frame.data["response_next"] = 0
        frame.data["response_declared"] = []
    askers: tuple[int, ...] = frame.data["response_askers"]
    while frame.data["response_next"] < len(askers):
        index = frame.data["response_next"]
        asker = askers[index]
        # 提供者可能跨多次重入（扣置搬牌 / 逐座位质疑 / 扣体力压子帧）：先接着走。
        control, satisfied, produced = host.advance_provider(
            frame, Timing.RESOLVE_BEFORE_EFFECT
        )
        if control is not None:
            return control
        if satisfied is None:
            action = host.ask_respond(
                frame,
                asker,
                Timing.RESOLVE_BEFORE_EFFECT,
                cause=WindowCause.NULLIFY,
                required=NULLIFY_RESPONSE_CARD,
                target=target,
                window_frame=frame.frame_id,
                mode=RespondMode.USE,
                declared=bool(frame.data["response_declared"]),
            )
            if host.suspended(action):
                return "stay"
            if isinstance(action, ActivateSkill):
                if not host.provider_registered(action.skill_id):
                    # 与【决斗】同一道闸：没有惰性判定式的提供者 ⇒ fail-closed。
                    raise NotImplementedError(
                        f"无懈窗口没有判定式提供者：{action.skill_id!r}（票 03）"
                    )
                control, satisfied, produced = host.start_provider(
                    frame,
                    action.skill_id,
                    asker,
                    Timing.RESOLVE_BEFORE_EFFECT,
                    required=NULLIFY_RESPONSE_CARD,
                )
                if control is not None:
                    return control
            elif isinstance(action, UseCard):
                # **声明**：行动被接受，但此刻不耗牌、不压 USE 帧（票 19 §9.4）。
                frame.data["response_declared"].append((asker, action.card))
        if satisfied and produced is not None:
            # 提供者的产物同样是**声明**（不是使用）：由下面的赢家分支统一交付。
            frame.data["response_declared"].append((asker, produced))
        frame.data["response_next"] = index + 1
    declared: list[tuple[int, CardRef]] = frame.data["response_declared"]
    if not declared:
        frame.data["canceled"] = False
        return None
    winner, card = declared[0]
    frame.data["response_used"] = True
    host.start_use(
        card,
        source=winner,
        targets=(),
        use_timing=Timing.RESOLVE_BEFORE_EFFECT,
        cancel_target=(frame.frame_id, target),
    )
    return "stay"


def resolve_effect(host: FrameHost, frame: Frame) -> Control:
    return _resolve_emit(host, frame, Timing.RESOLVE_EFFECT)


def resolve_after_effect(host: FrameHost, frame: Frame) -> Control:
    """**生效后**：执行本牌的效果列表（票 27 起按帧内游标重入）。

    ``after_effect_emitted`` 保证时机只记一次；``effect_pc`` 由引擎的效果执行器维护，
    使"提问让出控制权"与"压子帧"都能从这里原地恢复（票 19 §8）。
    """
    if not frame.data.get("after_effect_emitted"):
        control = _resolve_emit(host, frame, Timing.RESOLVE_AFTER_EFFECT)
        if control is not None:
            return control
        frame.data["after_effect_emitted"] = True
    return host.apply_effects(
        frame, behavior_for(frame.data["card"]).effects, timing=Timing.RESOLVE_AFTER_EFFECT
    )


def resolve_end(host: FrameHost, frame: Frame) -> Control:
    return _resolve_emit(host, frame, Timing.RESOLVE_END)


def resolve_advance(host: FrameHost, frame: Frame) -> Control:
    """**非 dispatch 的游标步骤**（票 11 §3.1）：一个目标跑完 C1–C5 后推进游标。

    还有目标就 ``Goto(C1)``；否则 pc 前进 ⇒ 帧出栈。窗口状态与效果游标都**按当前目标
    分槽**，故推进时统一清掉（票 11 §3.2）——新增按目标分槽的帧私有键时，
    **必须加进 :data:`_PER_TARGET_FRAME_KEYS`**，否则它会跨目标串味。
    """
    frame.data["cursor"] = frame.data.get("cursor", 0) + 1
    for key in _PER_TARGET_FRAME_KEYS:
        frame.data.pop(key, None)
    if frame.data["cursor"] < len(frame.data["targets"]):
        return Goto(RESOLVE_START_PC)
    return None


_PER_TARGET_FRAME_KEYS: tuple[str, ...] = (
    "canceled",
    "window_open",
    "after_effect_emitted",
    "effect_pc",
    "asks",
    "ask_answers",
    "op",
    "response_askers",
    "response_next",
    "response_declared",
    "response_used",
    "providers_used",
    NEED_CARD_FLOOR_KEY,
    "skill_result",
    "dodges_done",
    "axe_used",
    "gdb_used",
    "provider_skill",
    "provider_actor",
    "provider_satisfied",
    "provider_required",
    "provider_card",
    "lord_request",
    "_pending_timing",
    DAMAGE_POINT_KEY,
    DAMAGE_RECORDED_KEY,
    DAMAGE_DEAL_POINT_KEY,
    DAMAGE_DEAL_RECORDED_KEY,
    QIANXUN_PLACED_KEY,
)
"""**逐目标分槽**的帧私有键：游标推进时必须整体清掉（票 11 §3.2）。

``canceled`` / ``window_open`` / ``after_effect_emitted`` / ``effect_pc`` 在 :mod:`steps`
维护，``response_*``（收集式响应窗口的进度）同样在 :mod:`steps` 维护，``asks``（选择面记忆）
在 :class:`~sgs.engine.Engine` 维护、``op`` 在效果回调里维护；
集中列在这里，使"新加一个按目标分槽的键"只需改这一处。

票 08 追加 ``dodges_done``（本次响应窗口已攒够几张）。``use_tags``（帧标签袋的**逐目标
档**）**刻意不登在这里**（票 03 修）：``UseTags.target_tags`` 是 ``(目标座位, 标签)`` 的
键值对，``has_tag(..., target=...)`` 只命中本目标的条目，**标签本身不串目标**——所以不需要
（也不能）在游标推进时整袋清掉。早先登在这里时，一条在"指定目标后"一次性打完标签的技能
（【铁骑】/【烈弓】）会给第 2 个目标起的响应窗口丢失标签；`开发文档/03` §5「效果原语」与账本
「帧标签」行写的一直是"随**本次使用**结束即结构性消失"，故这是实现缺陷修复。
**全场档**住 :data:`sgs.resolution.WHOLE_FIELD_TAGS_KEY`，同样不登在这里（它作用于整次使用
的每一个目标）。见 ``tests/test_hero_response_window.py``。
票 09 再追加提供者展开的四个键（``provider_skill`` / ``provider_actor`` /
``provider_satisfied`` / ``lord_request``）：它们是**一次求助窗口**的进度，多目标【杀】里
必须逐目标重来，否则后一个目标会捡到前一个目标的应答者区间。
票 05 追加逐点触发的两个键（``damage_point_index`` / ``damage_recorded``）：DAMAGE 帧今天
恒单目标（多目标由行为表逐目标各压一个子帧），登在这里是姿态上的一致——真出现"一帧多目标"
时它们同样必须逐目标重来。
票 03（需要牌窗口的档位闸门）追加 :data:`sgs.effects.NEED_CARD_FLOOR_KEY`：它是**逐座位**
分槽的"不升档"游标，但每扇窗口都挂在本帧的**某个目标**上（【闪】/ 求桃 / 响应窗口逐个
目标开），故同样必须随游标推进清掉——否则第 2 个目标会继承第 1 个目标选过的档。
"""


RESOLVE_STEPS = (
    Step(resolve_start, Timing.RESOLVE_START),
    Step(resolve_before_effect, Timing.RESOLVE_BEFORE_EFFECT),
    Step(resolve_effect, Timing.RESOLVE_EFFECT),
    Step(resolve_after_effect, Timing.RESOLVE_AFTER_EFFECT),
    Step(resolve_end, Timing.RESOLVE_END),
    Step(resolve_advance, None),
)
"""C 段 = C1–C5 **逐目标** + 末尾非 dispatch 的游标步骤（票 11 §3.1）。"""


def _pc_of(steps: tuple[Step, ...], timing: Timing) -> int:
    """按**时机**（而不是硬编码下标）定位步骤，重排步骤表不会再让跳转错位。"""
    return next(index for index, step in enumerate(steps) if step.timing is timing)


RESOLVE_START_PC = _pc_of(RESOLVE_STEPS, Timing.RESOLVE_START)
"""C 段首步（C1）：游标推进后跳回这里。"""

RESOLVE_END_PC = _pc_of(RESOLVE_STEPS, Timing.RESOLVE_END)
"""C5"使用结算结束时"：被抵消时 Goto 到这里（跳过生效时 / 生效后）。"""

RESOLVE_AFTER_EFFECT_PC = _pc_of(RESOLVE_STEPS, Timing.RESOLVE_AFTER_EFFECT)
"""C4"生效后"：【贯石斧】"依然造成伤害"时跳过 C3 直进这里（票 12 Q16）。"""

CURSOR_PC = len(RESOLVE_STEPS) - 1
"""非 dispatch 的游标步骤：无效目标从这里直接推进，跳过 C2–C5。"""

USE_AFTER_END_PC = _pc_of(USE_STEPS, Timing.RESOLVE_AFTER_END)
"""USE 帧的"使用结算结束后"：目标减为 0 / 执行动作未完成时直接跳到这里。"""


# ---- DAMAGE 帧：L7 五个时机 + 在"受到伤害时"后插入 LIFE_DECREASE -----------


def _damage_channel(frame: Frame) -> tuple[CardRef, ...]:
    """官方六要素里的**渠道**：造成这次伤害的实体牌；无牌来源时为空元组。"""
    channel = frame.data.get("channel")
    return (channel,) if channel is not None else ()


def _emit_damage(
    host: FrameHost,
    frame: Frame,
    timing: Timing,
    actor: int,
    *,
    emit_record: bool = True,
) -> Control:
    # 渠道牌进时机记录，使"谁用什么牌造成/受到了这次伤害"在记录流里可读。
    return _emit(
        host,
        frame,
        timing,
        actor=actor,
        cards=_damage_channel(frame),
        emit_record=emit_record,
    )


def damage_deal(host: FrameHost, frame: Frame) -> Control:
    """**造成伤害时**：本帧逐点触发的四个帧私有键在这里重置（票 05 / M6 票 15）。

    "本帧第几点 / 记过没有"是**帧级**事实，故在帧的第一步显式清零：同一份载荷字典被复用、
    或某个调用点先写过这些键时，后一次伤害不会接着前一次的轮次跑（帧私有数据不进记录流，
    这一步对 M2 逐字节无影响）。票 15 追加**造成伤害后**一侧的两个键（与受到侧分列），
    否则两次伤害之间的 ``DAMAGE_AFTER_DEAL`` 会误读上一帧的游标。
    """
    frame.data[DAMAGE_POINT_KEY] = 0
    frame.data.pop(DAMAGE_RECORDED_KEY, None)
    frame.data.pop(DAMAGE_DEAL_POINT_KEY, None)
    frame.data.pop(DAMAGE_DEAL_RECORDED_KEY, None)
    return _emit_damage(host, frame, Timing.DAMAGE_DEAL, frame.data["source"])


def damage_take(host: FrameHost, frame: Frame) -> Control:
    return _emit_damage(host, frame, Timing.DAMAGE_TAKE, frame.data["target"])


CHAIN_TRIGGER_KEY = "chain_trigger"
"""帧私有槽名：这次伤害是**源头属性伤害**且命中了横置角色（M5-03 的传导触发判据）。

写在 DAMAGE 帧上、进 ``_after_pop`` 的引擎规则读它。**帧私有数据不进记录流**，故标准 108
（无属性牌型、无人横置）逐字节不变。
"""

CHAIN_RESET_KEY = "chain_reset"
"""帧私有槽名：这次伤害的**受伤害者**是横置的，伤害值落地后要立即重置（M5-03）。

由 DAMAGE 帧在数值定稿时算出、随载荷交给 LIFE_DECREASE 帧：重置因此发生在**同一伤害
事件内**、先于 ``DAMAGE_AFTER_TAKE`` / 濒死 / 死亡，不另开事件帧。
"""

_ATTRIBUTE_NATURES = (DamageNature.FIRE, DamageNature.THUNDER)
"""会触发连环传导的伤害属性（普通伤害不触发，spec §5.1.1）。"""


def damage_finalize_amount(host: FrameHost, frame: Frame) -> None:
    """**伤害数值定稿**（M5-03）：过伤害修饰器链，并判定横置重置 / 传导触发。

    时机在"受到伤害时"整步之后（含它的钩子与追问）、扣减体力之前：这正是官方"确定伤害
    数值"的位置，``frame.data["amount"]`` 从此是**实际要扣的**数值。三件事：

    1. 数值 = :func:`~sgs.effects.damage_amount`（来源侧只在 ``chain=False`` 的源头帧算，
       目标侧每帧都算；M5-05a 起目标侧链在加法之后还有一段封顶 ``DAMAGE_CAP``）；
    2. ``chain_reset`` = 受伤害者此刻横置 ∧ 属性伤害（火 / 雷）∧ 数值 > 0 ∧ 未防止；
    3. ``chain_trigger`` = ``chain_reset`` ∧ **不是传导伤害**（``chain=True`` 不再传导）。

    这一步**不 dispatch 任何时机**（``Step(damage_finalize_amount, None)``）：它是数值结算，
    不是新时机；对标准 108 是零动作（没有伤害数值修饰器、无人横置）。
    """
    amount = frame.data["amount"]
    reset = False
    if not has_tag(frame, Tag.PREVENTED):
        amount = damage_amount(
            host.state,
            source=frame.data.get("source"),
            target=frame.data["target"],
            card=frame.data.get("channel"),
            base=amount,
            chain=bool(frame.data.get("chain")),
            deal_bonus=int(frame.data.get("deal_bonus", 0)),
            nature=frame.data.get("nature"),
        )
        reset = (
            amount > 0
            and frame.data.get("nature") in _ATTRIBUTE_NATURES
            and host.state.player(frame.data["target"]).chained
        )
    frame.data["amount"] = amount
    frame.data[CHAIN_RESET_KEY] = reset
    frame.data[CHAIN_TRIGGER_KEY] = reset and not bool(frame.data.get("chain"))
    source = frame.data.get("source")
    if amount > 0 and not has_tag(frame, Tag.PREVENTED) and source is not None:
        # M6 票 06【诛害】的判据：**造成过伤害**（数值定稿且 > 0、未被防止）。
        # 在"受到伤害时"整步之后记，故被【寒冰剑】一类效果防止的伤害不算。
        host.note_damage_dealt(int(source))
    return None


def damage_push_life_decrease(host: FrameHost, frame: Frame) -> None:
    """"受到伤害时"之后进入扣减体力结算；**帧标签 ``PREVENTED`` 为真时跳过**。

    票 12 §Q15 / 票 30：【寒冰剑】防止此伤害 ⇒ 跳体力扣减、**不新增 delta / 时机**。
    该标志自票 08 起住帧标签袋（"加一条禁令 / 标志 ⇒ 帧标签"，不再占帧私有裸键）。
    """
    if has_tag(frame, Tag.PREVENTED):
        return
    host.push_frame(
        FrameKind.LIFE_DECREASE,
        {
            "target": frame.data["target"],
            "source": frame.data["source"],
            "amount": frame.data["amount"],
            CHAIN_RESET_KEY: bool(frame.data.get(CHAIN_RESET_KEY)),
            # 实机报障 2026-10-31：伤害带来的扣减 ≠「失去体力」。帧照压、L8 三个时机照发
            # （记录形态不变），但挂在 LIFE_DECREASE_AFTER 的技能要能把它排除掉（【诈降】）。
            LIFE_DECREASE_DAMAGE_KEY: True,
        },
        timing=Timing.LIFE_DECREASE_BEFORE,
    )


def damage_after_deal(host: FrameHost, frame: Frame) -> Control:
    """**造成伤害后**：按伤害值**逐点**派发（M6 票 15，与 ``DAMAGE_AFTER_TAKE`` 同范式）。

    票 15 之前这里每次伤害事件只派发一轮——当时挂在此时机上的只有觉醒技【潜心】，牌面是
    "当你造成伤害后"（每次事件一次）。【忘隙】的牌面是"造成 **1 点**伤害后"，逐点因此是
    **技能定义上的事实**（``03`` §4），把 ``Hook.per_point`` 的既有判据扩展到此时机：为真
    的钩子每一轮都有机会，为假的只在第一轮（读取 :func:`~sgs.skills.damage_after.
    damage_point_index` 的同一处过滤）。

    循环沿用 ``DAMAGE_AFTER_TAKE`` 的形状：

    - **时机记录恰好一条**（与既有基准逐字节一致）：第 1 轮落记录，第 2 轮起
      ``emit_record=False``；
    - 轮次写进 ``DAMAGE_POINT_KEY``（技能回调与派发答案键都读它），**跑完交还 0**——
      ``DAMAGE_AFTER_TAKE`` 在同一帧里紧接着从第 0 点起跑，不接造成侧的尾数；
    - 造成侧自己的续跑游标另存 :data:`~sgs.skills.DAMAGE_DEAL_POINT_KEY`：某轮提问让出
      控制权（返回 ``"stay"``）后重入从这一轮接着跑，不重记也不跳点；
    - 伤害值 ≤ 0 或被防止时与旧行为逐字相同：只落一条记录、一轮都不派发（【潜心】的
      既有语义不变）。
    """
    amount = frame.data["amount"]
    if amount <= 0 or has_tag(frame, Tag.PREVENTED):
        return _emit_damage(host, frame, Timing.DAMAGE_AFTER_DEAL, frame.data["source"])
    point = frame.data.get(DAMAGE_DEAL_POINT_KEY, 0)
    while point < amount:
        frame.data[DAMAGE_DEAL_POINT_KEY] = point
        # ``damage_point_index`` / 答案缓存键 / ``Hook.per_point`` 都读这一个点号。
        frame.data[DAMAGE_POINT_KEY] = point
        control = _emit_damage(
            host,
            frame,
            Timing.DAMAGE_AFTER_DEAL,
            frame.data["source"],
            emit_record=not frame.data.get(DAMAGE_DEAL_RECORDED_KEY),
        )
        if control is not None:
            # 让出控制权（钩子提问）：本轮尚未标成已派发，重入时同一轮继续。
            return control
        frame.data[DAMAGE_DEAL_RECORDED_KEY] = True
        point += 1
        frame.data[DAMAGE_DEAL_POINT_KEY] = point
    # 交还给 ``damage_after_take``：它按同一个点号从 0 起逐点派发。
    frame.data[DAMAGE_POINT_KEY] = 0
    return None


def damage_after_take(host: FrameHost, frame: Frame) -> Control:
    """**受到伤害后**：按伤害值**逐点**派发（票 05 §要做什么 1 / `spec.md` §结算层）。

    "触发次数是技能定义的一部分"（``03`` §4）在这里兑现：``Hook.per_point`` 为真的钩子
    每一轮都有机会，为假的只在**第一轮**有机会（"每次伤害事件一次"）。轮次写进帧私有
    ``damage_point_index``，供技能回调判读，也进非锁定技"是否发动"的答案缓存键——故逐点
    技能每一轮各问一次，非逐点技能只问第一轮。

    **记录形态与 M2 逐字节一致**：一次伤害事件恰好一条 ``DAMAGE_AFTER_TAKE`` 记录
    （``amount=3`` 的【闪电】也只有一条），逐点只体现为"这一条记录内多轮派发"；某一轮里
    钩子提问会让出控制权（返回 ``"stay"``），重入时由 :data:`DAMAGE_RECORDED_KEY` 分辨
    "只派发、不再记录"，一轮派完即推进 :data:`DAMAGE_POINT_KEY`。

    **伤害被减为 0 ⇒ 不触发**（沿用"防止伤害"的既有语义）：帧标签 ``PREVENTED`` 为真时
    只落一条时机记录、一轮都不派发（与"没有受到伤害"的读法一致，也保住 M2【寒冰剑】
    路径的记录流形态）；伤害值本身 ≤ 0 时同样一轮不派发。
    """
    amount = frame.data["amount"]
    if amount <= 0 or has_tag(frame, Tag.PREVENTED):
        # **伤害被减为 0 ⇒ B 族一次都不触发**（票 05 §验收 2）：沿用"防止伤害"的既有
        # 语义，时机记录照落一条（M2 的【寒冰剑】路径就是这样），但一个钩子都不派发。
        return _emit_damage(
            host, frame, Timing.DAMAGE_AFTER_TAKE, frame.data["target"]
        )
    point = frame.data.get(DAMAGE_POINT_KEY, 0)
    while point < amount:
        frame.data[DAMAGE_POINT_KEY] = point
        # **时机记录恰好一条**（与 M2 逐字节一致）：逐点只是"这一条记录内派发多轮"，
        # 不把伤害事件拆成多条记录——既有基准里 amount=3 的【闪电】也只有一条。
        # 重入（某轮提问让出控制权后回来）时本点还没标记，故照旧只派发、不再记。
        control = _emit_damage(
            host,
            frame,
            Timing.DAMAGE_AFTER_TAKE,
            frame.data["target"],
            emit_record=not frame.data.get(DAMAGE_RECORDED_KEY),
        )
        if control is not None:
            # 让出控制权（钩子提问）：本轮尚未标成已派发，重入时同一轮继续。
            return control
        frame.data[DAMAGE_RECORDED_KEY] = True
        point += 1
        frame.data[DAMAGE_POINT_KEY] = point
    return None


def damage_end(host: FrameHost, frame: Frame) -> Control:
    return _emit_damage(host, frame, Timing.DAMAGE_END, frame.data["target"])


DAMAGE_STEPS = (
    Step(damage_deal, Timing.DAMAGE_DEAL),
    Step(damage_take, Timing.DAMAGE_TAKE),
    # 数值定稿：非 dispatch（不对应任何时机枚举位）；横置重置 / 传导触发的判据在这里算。
    Step(damage_finalize_amount, None),
    # 压栈步骤的 `timing` 只作子帧 `return_to` 的可读标签：LIFE_DECREASE_BEFORE
    # 由 LIFE_DECREASE 帧自己 dispatch。
    Step(damage_push_life_decrease, Timing.LIFE_DECREASE_BEFORE),
    Step(damage_after_deal, Timing.DAMAGE_AFTER_DEAL),
    Step(damage_after_take, Timing.DAMAGE_AFTER_TAKE),
    Step(damage_end, Timing.DAMAGE_END),
)


# ---- LIFE_DECREASE 帧：L8 三个时机 + 在"扣减体力时"插入濒死 ------------------


def life_decrease_before(host: FrameHost, frame: Frame) -> Control:
    return _emit(host, frame, Timing.LIFE_DECREASE_BEFORE, actor=frame.data["target"])


def life_decrease_on(host: FrameHost, frame: Frame) -> Control:
    """**扣减体力时** = 唯一的体力写入点；先 dispatch 时机，再把体力扣到目标值。

    目标值由本帧算（``hp - amount``），原语只负责写（票 07 §1）。结算完**立刻**把
    结果记进帧：濒死与否是这次扣减的结果，"扣减体力时"之后据此决定是否压入濒死帧
    （票 21）；无人救时死亡结算再从濒死帧压入（票 22）。
    """
    target = frame.data["target"]
    control = _emit(host, frame, Timing.LIFE_DECREASE_ON, actor=target)
    if control is not None:
        return control
    host.apply_hp_change(frame, target, host.state.player(target).hp - frame.data["amount"])
    frame.data["dying"] = host.state.player(target).hp <= 0
    return None


def life_decrease_reset_chain(host: FrameHost, frame: Frame) -> None:
    """**属性伤害命中横置者 ⇒ 伤害值落地后立即重置**（M5-03 / spec §5.1.1）。

    插入点是"扣减体力时"整步之后、濒死帧之前：官方顺序"伤害值落地 → 重置横置 →
    受到伤害后 / 濒死 / 死亡"因此由步骤表直接表达，钩子在 ``DAMAGE_AFTER_TAKE``、
    濒死、死亡里读到的都是**已重置**。重置是**同一伤害事件内**的一步（本 LIFE_DECREASE
    帧），不压新帧、不 dispatch 新时机；状态写入走引擎的 ``set_chained``（唯一写入口），
    故记录流里 ``chained_change`` 与那条 ``hp_change`` 同帧、且排在 ``DAMAGE_AFTER_TAKE`` 之前。

    判据（``chain_reset``）由 DAMAGE 帧在数值定稿时算好并随载荷传来：横置 ∧ 属性伤害
    （火 / 雷）∧ 实际伤害 > 0 ∧ 未被防止。普通伤害与未被横置的受伤害者都是零动作。
    """
    if not frame.data.get(CHAIN_RESET_KEY):
        return
    host.set_chained(frame, frame.data["target"], False)


def life_decrease_trigger_near_death(host: FrameHost, frame: Frame) -> None:
    """体力 ≤ 0 ⇒ 插入濒死结算（官方 4.17 第 1 条）。

    插入点选在"扣减体力时"整步做完之后：时机本身的记录形态因此仍是
    ``BEFORE → ON → AFTER``，濒死帧压在这三条之上——官方"濒死结算优先于
    造成伤害后 / 受到伤害后类技能"（4.17）于是由栈结构直接表达。
    """
    target = frame.data["target"]
    if not frame.data["dying"]:
        return
    host.push_frame(
        FrameKind.NEAR_DEATH,
        {"target": target, "dying": True, "damage_source": frame.data.get("source")},
        timing=Timing.NEAR_DEATH_ENTER,
    )


def life_decrease_after(host: FrameHost, frame: Frame) -> Control:
    """**扣减体力后**：按失去的数值**逐点**派发（huo-shenzhouyu-mobile 票 04）。

    账本 `06`（§5「程序化约定与偏离一手」）的「界【诈降】『失去 1 点体力后』按逐点触发」一行逐字写着
    "等出现一次失去 ≥2 点的载体时必须改成逐点"——【业炎】的代价「失去 3 点体力」就是
    那个载体。范式与 :func:`damage_after_take` 逐字同形：

    - 一条 ``LIFE_DECREASE`` 帧仍然只落**一条**时机记录（逐点只是"这条记录内派发多轮"）；
    - 轮次写进 ``DAMAGE_POINT_KEY``（技能回调与派发答案键都读它），
      ``DAMAGE_RECORDED_KEY`` 保证重入不重复记录；
    - ``Hook.per_point`` 为真的钩子每轮都有机会，为假的只在第 0 轮（既有语义不变）；
    - 某一轮里钩子提问让出控制权（返回 ``"stay"``）⇒ 本轮未标已派发，重入继续本轮。

    **伤害来源的扣减**（``damage_push_life_decrease`` 压的那条）走同一个循环：它没有
    ``per_point`` 钩子（【诈降】按牌面只认真正的失去体力），每轮派发都是空集合 ⇒
    行为与记录形态都不变。

    无人救的死亡路径上这一步**不会被走到**——濒死帧在"濒死结算结束后"直接压入 DEATH
    子帧并留在栈上，整条伤害链（含本帧）由终局清栈统一丢弃（票 22）；救回时才回到这里。
    """
    amount = int(frame.data.get("amount", 1))
    if amount <= 0:
        return _emit(host, frame, Timing.LIFE_DECREASE_AFTER, actor=frame.data["target"])
    point = int(frame.data.get(DAMAGE_POINT_KEY, 0))
    while point < amount:
        frame.data[DAMAGE_POINT_KEY] = point
        control = _emit(
            host,
            frame,
            Timing.LIFE_DECREASE_AFTER,
            actor=frame.data["target"],
            emit_record=not frame.data.get(DAMAGE_RECORDED_KEY),
        )
        if control is not None:
            # 让出控制权（钩子提问）：本轮尚未标成已派发，重入时同一轮继续。
            return control
        frame.data[DAMAGE_RECORDED_KEY] = True
        point += 1
        frame.data[DAMAGE_POINT_KEY] = point
    return None


LIFE_DECREASE_STEPS = (
    Step(life_decrease_before, Timing.LIFE_DECREASE_BEFORE),
    Step(life_decrease_on, Timing.LIFE_DECREASE_ON),
    # 横置重置：非 dispatch（不对应任何时机枚举位），在濒死 / after-take 之前（M5-03）。
    Step(life_decrease_reset_chain, None),
    # 压栈步骤的 `timing` 只作子帧 `return_to` 的可读标签：NEAR_DEATH_ENTER
    # 由 NEAR_DEATH 帧自己 dispatch。
    Step(life_decrease_trigger_near_death, Timing.NEAR_DEATH_ENTER),
    Step(life_decrease_after, Timing.LIFE_DECREASE_AFTER),
)


# ---- NEAR_DEATH 帧：L12 三个时机 + 官方求桃顺序 -----------------------------
#
# 应答者区间是票 05 §7 的"**从当前回合角色起、逆时针、每人一次**"（官方 L18.2，
# ``flow/neardeath.txt``）；L18.8 的起点切换与 L12.2 的"新的濒死状态时"回退按票 20 / 票 31
# 落成**同一帧内的窗口回退**（``Goto`` 跳回本步骤）：每个窗口的区间是上一窗的座位后缀，
# 起点 = 刚响应完的响应者、终点仍是"当前回合角色的上家"（继承不重算）。
#
# **M3 交接**（票 20 §4）：``NEAR_DEATH_AT`` 逐窗口重发，而 ``R:475`` 的"同一时机发动一次后
# 本次濒死结算结束前无效"其去重作用域 = **整个濒死结算帧**——M3 的技能去重键必须绑帧，
# **不得**绑单次派发。帧上的 ``_pending_timing``（``game.py``）只是**记录幂等**机制，
# 不是技能去重，两件事不要混用。


def _emit_near_death(host: FrameHost, frame: Frame, timing: Timing) -> Control:
    return _emit(host, frame, timing, actor=frame.data["target"])


def near_death_enter(host: FrameHost, frame: Frame) -> Control:
    return _emit_near_death(host, frame, Timing.NEAR_DEATH_ENTER)


def near_death_at(host: FrameHost, frame: Frame) -> Control:
    """"处于濒死状态时"：打开求桃窗口（首窗，或回退后开出的新窗），定下应答者区间。

    区间在窗口打开时**定格**（``askers`` 进帧私有载荷）而不是每轮重算。首窗按官方
    "当前回合角色起逆时针"；**回退窗**（票 20 / 票 31）取旧元组的**后缀**——``window_start``
    由 :func:`near_death_ask` 在判定回退时写入（= 刚响应完的座位），故"先前已问过的人
    不再问"与"终点不变"都是结构事实，且 ``turn_seat`` 为死者时也不会重算错。
    """
    control = _emit_near_death(host, frame, Timing.NEAR_DEATH_AT)
    if control is not None:
        return control
    prior = frame.data.get("askers")
    start = frame.data.get("window_start")
    if start is None or prior is None:
        askers = _askers_from(host.state, host.state.turn_seat, len(host.state.players))
    else:
        askers = prior[prior.index(start) :]
    frame.data["askers"] = askers
    frame.data["next"] = 0
    return None


def near_death_ask(host: FrameHost, frame: Frame) -> Control:
    """逐个应答者求桃；体力恢复过但仍 < 1 时**在同一帧内开出新窗口**。

    "一个询问一个应答者"（票 05 §7）：每轮只发一个 ``PEACH`` 询问，``next`` 记进度，
    故暂停、恢复、拒绝都落在同一个槽位上。体力恢复到 1 点以上就**立即**脱离濒死
    （官方 4.17 第 4 条"每回复 1 点体力即检查"），不再问剩下的人。

    **回退判据**（票 20 §2 / 票 31）：刚推入的那次响应结算全部出栈后，若体力**严格
    上升**且仍 < 1 ⇒ 记 ``window_start`` 为那名响应者并 ``Goto`` 回 :func:`near_death_at`
    开新窗。检测点在响应结算之后，故体力写入本来就已经发生、判据零新增钩子；
    ``pending_response`` 在每次重入时先取走，因而"结算未回复"的分支天然干净。

    **提供者（票 03 的 C1 缺口第一段）**：候选里的 ``ActivateSkill`` 与真牌占**同一个槽位**
    ——真牌由本窗口自己压 ``recover_from_peach``（回复结算插在濒死之下），提供者交回的
    产物牌也走同一条收尾（:func:`_peach_use`）。作废（``satisfied=False``：产物从未被使用）
    与「放弃」同路 ⇒ ``next`` 前进、窗口问下一个座位；提供者说不出产物（主公技式的
    "别人已经付过了"）在求桃窗口没有可交付的【桃】，同样按放弃处理。官方规则集的濒死例
    （``glossary_flow.txt:67``）逐字写着于吉可以在濒死结算里【蛊惑】用【桃】——这扇窗口是
    这条技能的**必需**窗口。

    重入次序：提供者会跨多次重入（扣置搬牌内联「失去牌后」、逐座位质疑、扣体力压子帧），
    故每轮先 :meth:`advance_provider` 接着上次的地方走，只有"当前没有在展开的提供者"时
    才发新询问。
    """
    target = frame.data["target"]
    pending = frame.data.pop("pending_response", None)
    if host.state.player(target).hp >= 1:
        return None
    if pending is not None:
        idx, hp_before = pending
        if host.state.player(target).hp > hp_before:
            frame.data["window_start"] = frame.data["askers"][idx]
            return Goto(NEAR_DEATH_AT_PC)
        frame.data["next"] = idx + 1
    while frame.data["next"] < len(frame.data["askers"]):
        idx = frame.data["next"]
        asker = frame.data["askers"][idx]
        control, satisfied, produced = host.advance_provider(
            frame, Timing.NEAR_DEATH_AT
        )
        if control is not None:
            return control
        if satisfied is None:
            action = host.ask_query(
                QueryKind.PEACH,
                asker,
                Timing.NEAR_DEATH_AT,
                frame=frame,
                dying=target,
                payload=PeachContext(floor=need_card_floor(frame, asker)),
            )
            if host.suspended(action):
                return "stay"
            if isinstance(action, ActivateSkill):
                if not host.provider_registered(action.skill_id):
                    # 求桃窗口没有"选中才惰性判定"的提供者（【八卦阵】那类只服务【闪】），
                    # 撞到即编程错误，不静默当成放弃。
                    raise NotImplementedError(
                        f"求桃窗口没有判定式提供者：{action.skill_id!r}（票 03）"
                    )
                control, satisfied, produced = host.start_provider(
                    frame,
                    action.skill_id,
                    asker,
                    Timing.NEAR_DEATH_AT,
                    required=PEACH_RESPONSE_CARD,
                )
                if control is not None:
                    return control
            elif isinstance(action, UseCard):
                return _peach_use(host, frame, asker=asker, target=target, card=action.card)
            else:
                frame.data["next"] = idx + 1
                continue
        if satisfied and produced is not None:
            return _peach_use(host, frame, asker=asker, target=target, card=produced)
        frame.data["next"] = idx + 1
    return None


def _peach_use(host: FrameHost, frame: Frame, *, asker: int, target: int, card: CardRef) -> Control:
    """求桃窗口**消费一张【桃】**：真牌与提供者产物共用这一份收尾。

    ``pending_response`` 记的是"哪个座位 + 回复前的体力"：响应链出栈后重入本步，由它决定
    "继续问下一个人"还是"开新窗口"（体力严格上升但仍 < 1）。故这里必须记在压帧**之前**。

    这也是求桃窗口"**这一次需要牌事件成立**"的落点（票 03 §4-3）：那张【桃】开始回复，
    本次需要牌事件结束，不升档的 floor 清回最高档（回退开的新窗是**新**事件）。
    """
    frame.data["pending_response"] = (int(frame.data["next"]), host.state.player(target).hp)
    reset_need_card_floor(frame)
    host.recover_from_peach(frame, card, asker=asker, dying=target)
    return "stay"  # 响应链出栈后重入本步：此处判回退


def near_death_after(host: FrameHost, frame: Frame) -> Control:
    """"濒死结算结束后"：抢救结果直接读体力（官方 4.17 第 4 条）。

    本时机是**空实现**（官方此处只列技能效果，M1 无技能）：流程经过并照记，不产生状态
    变化（票 13 的三分类：dispatch 但无效果）。体力仍 ≤ 0 就是**死亡入口**：在此压入
    DEATH 子帧（票 22）。死亡结算挂在濒死帧**之上**，濒死帧因此留在栈上不弹出——
    它连同整条伤害链在终局清栈时逐帧记 `discard`（票 04 §5 的 5 帧）。
    """
    control = _emit_near_death(host, frame, Timing.NEAR_DEATH_AFTER)
    if control is not None:
        return control
    target = frame.data["target"]
    if host.state.player(target).hp <= 0:
        host.enter_death(frame, target)
    return None


NEAR_DEATH_STEPS = (
    Step(near_death_enter, Timing.NEAR_DEATH_ENTER),
    Step(near_death_at, Timing.NEAR_DEATH_AT),
    Step(near_death_ask, Timing.NEAR_DEATH_AT),
    Step(near_death_after, Timing.NEAR_DEATH_AFTER),
)

NEAR_DEATH_AT_PC = _pc_of(NEAR_DEATH_STEPS, Timing.NEAR_DEATH_AT)
"""窗口步骤的下标：新濒死回退 = 在同一帧内 ``Goto`` 回这里（票 20 §1 / 票 31）。"""


def _askers_from(state: GameState, start: int | None, count: int) -> tuple[int, ...]:
    """应答者区间：从 ``start`` 起逆时针（座位号 +1）、每人一次、按座位序。

    官方把阵亡角色排除在询问之外（``flow_neardeath`` 的"每名角色"读作在场角色）；
    M1 的濒死者此刻仍存活，故它自己也在区间里（官方"濒死角色自己也可以使用【桃】自救"）。
    ``start`` 为 ``None``（回合外）时退化为座位 0 起——M1 走不到，保留结构。

    读取走**只读座位环真源**（票 23）：``start`` = 当前回合角色、``count`` = 座位总数时，
    区间恰是"绕整圈、终点为其上家"；位置与签名保持不变（票 15 §7 / 票 20）。
    """
    return seats_from(state, start, count)


# ---- DEATH 帧：L13 裁剪后的死亡段（票 13 §4 / 票 22 / 票 25）---------------
#
# 官方流程四时机 = 亮出身份牌前 / 亮出身份牌后 / 死亡时 / 死亡后，**按模式分叉**（票 25）：
#   - 无身份局（1v1）四段：``DEATH_REVEAL_IDENTITY_BEFORE`` **不入表**（无身份牌）；
#   - 身份局五段：``DEATH_REVEAL_IDENTITY_BEFORE`` 入表并亮明身份（主公也照发）；
#   - ``DEATH_REVEAL_IDENTITY_AFTER`` 是唯一胜负检测点；
#   - ``DEATH_ON`` = 死亡时技能（只 dispatch）；Ⅰ.a 系统处理牌 → Ⅱ 奖惩在下一格
#     ``death_settle_cards``（非 dispatch 步，票 33 的段序落点 / 挂起续跑边界）；
#   - ``DEATH_BEFORE_START`` 定格 killer、主公死亡优先于回合结束；
#   - ``DEATH_AFTER`` 空实现。
# 检测点只置 ``pending_outcome``、**不**终止：同批最后一次 ``DEATH_AFTER`` 之后才宣布
# 胜负（终止点在引擎的 DEATH 出栈逻辑里，票 14 §3）。


def _emit_death(host: FrameHost, frame: Frame, timing: Timing) -> Control:
    return _emit(host, frame, timing, actor=frame.data["target"])


def death_before_start(host: FrameHost, frame: Frame) -> Control:
    """死亡结算开始前：①主公死亡不做回合结束；②否则定格 killer、自己的回合则回合结束。

    身份局才做这两件事（票 14 §4，官方 `flow_death` 首段）；无身份局保持 M1 的空实现。
    ``killer`` 只在**有来源的伤害且来源 ≠ 死者**时定格：失去体力 / 无来源伤害（闪电）/
    自杀 ⇒ 未定格 ⇒ 无奖惩。来源由死亡帧的 ``damage_source`` 带下来（帧私有载荷，
    不进 ``timing`` 记录的 ``source``，故记录面不变）。
    """
    control = _emit_death(host, frame, Timing.DEATH_BEFORE_START)
    if control is not None:
        return control
    if host.state.rule_config.mode is not Mode.IDENTITY:
        return None
    seat = frame.data["target"]
    if host.state.player(seat).identity is Identity.LORD:
        # 官方"死亡结算开始前：若死亡的是主公，则游戏结束"——回合结束分支让位（票 14 §4）。
        return None
    source = frame.data.get("damage_source")
    if source is not None and source != seat:
        frame.data["killer"] = source
    if host.state.turn_seat == seat:
        return _emit(host, frame, Timing.TURN_END, actor=seat)
    return None


def death_reveal_identity_before(host: FrameHost, frame: Frame) -> Control:
    """"亮出身份牌前"：先 dispatch 时机，再发一条 ``identity_reveal``（票 14 §5）。

    只进身份局的步骤表；主公也照发（开局已亮明，再发一次只是形状统一）。
    """
    control = _emit_death(host, frame, Timing.DEATH_REVEAL_IDENTITY_BEFORE)
    if control is not None:
        return control
    host.reveal_dead_identity(frame)
    return None


def death_reveal_identity_after(host: FrameHost, frame: Frame) -> Control:
    """胜负检测点：**先照记时机与死亡事实，再调唯一终局入口**。

    顺序使 ``check_seq`` 恰为这条 ``DEATH_REVEAL_IDENTITY_AFTER`` 记录的序号
    （票 08 §6 的"检测点那条记录"口径，票 22 第 6 条）。检测只置 ``pending_outcome``，
    不清栈、不终止——死亡结算剩余两段照跑（票 13 §3）。
    """
    control = _emit_death(host, frame, Timing.DEATH_REVEAL_IDENTITY_AFTER)
    if control is not None:
        return control
    host.record_death_fact(frame)
    host.check_game_end()
    return None


def death_time_window_open(host: FrameHost, frame: Frame) -> Control:
    """「阵亡时」空节点（票 10）：把**当前死者本人**的技能放回解析窗口，不 dispatch。

    ``alive`` 在死亡入口已经是 ``False``（胜负 / 距离 / 人数正确）；本步只把
    ``death_time_skills_active`` 置真，让 ``resolve_instances`` 在窗口内（DEATH_ON /
    官方弃牌奖惩 / DEATH_AFTER）还能产出这名死者的实例。窗口不是按时机塞实例——它是
    一个显式状态位；窗口外（TURN_END / 亮身份 / 死亡结算之后）死者仍静默。
    """
    host.state.player(frame.data["target"]).death_time_skills_active = True
    return None


def death_time_window_close(host: FrameHost, frame: Frame) -> Control:
    """「失去技能」收尾（票 10）：DEATH_AFTER 之后关窗；死者本人此后不再产出实例。"""
    host.state.player(frame.data["target"]).death_time_skills_active = False
    return None


def death_on(host: FrameHost, frame: Frame) -> Control:
    """死亡时：dispatch 死亡时技能（窗口内；首个载体是神关羽【武魂】②）。"""
    return _emit_death(host, frame, Timing.DEATH_ON)


def death_settle_cards(host: FrameHost, frame: Frame) -> Control:
    """死亡时的收尾：Ⅰ.a 系统处理牌 → Ⅱ 死亡奖惩（**非 dispatch 步**，票 33）。

    与 :func:`death_on` 分成两步是**结构性**的：主公杀忠臣的弃置自票 33 起经移动入口
    派发 ``MOVE_AFTER``，钩子提问会把这一步挂起、等应答到达后**重入同一个步骤**。
    若 Ⅰ.a / Ⅱ 还挂在 ``DEATH_ON`` 那一步里，重入会把 `DEATH_ON` 的时机记录再发一遍
    ——``_pending_timing`` 的幂等只覆盖"派发本身挂起"这一种，而这里挂起发生在派发
    **已经完成之后**。时机记录因此必须留在前一步（派发一次、只落一条）。

    ``apply_death_rewards`` 的返回值原样透传：奖惩里的搬牌挂起时，驱动循环必须带着
    待答询问停在这里，而不是继续把死亡结算跑完。
    """
    host.discard_dead_cards(frame)
    return host.apply_death_rewards(frame)


def death_after(host: FrameHost, frame: Frame) -> Control:
    """空实现：死亡结算收尾空时点（票 13 §4 的 C 类）。"""
    return _emit_death(host, frame, Timing.DEATH_AFTER)


DEATH_STEPS = (
    Step(death_before_start, Timing.DEATH_BEFORE_START),
    Step(death_reveal_identity_after, Timing.DEATH_REVEAL_IDENTITY_AFTER),
    Step(death_time_window_open),
    Step(death_on, Timing.DEATH_ON),
    Step(death_settle_cards),
    Step(death_after, Timing.DEATH_AFTER),
    Step(death_time_window_close),
)
"""无身份局（1v1）的死亡段：M1 的四个时机，记录流与既有一字不差。

``death_time_window_open`` / ``death_time_window_close`` 不对应任何时机枚举位（票 10）：
它们只开关"死者本人的技能窗口"，不产记录；`death_settle_cards` 同样是非 dispatch 步
（票 33 的Ⅰ.a / Ⅱ 挂起续跑边界）。
"""

IDENTITY_DEATH_STEPS = (
    Step(death_before_start, Timing.DEATH_BEFORE_START),
    Step(death_reveal_identity_before, Timing.DEATH_REVEAL_IDENTITY_BEFORE),
    Step(death_reveal_identity_after, Timing.DEATH_REVEAL_IDENTITY_AFTER),
    Step(death_time_window_open),
    Step(death_on, Timing.DEATH_ON),
    Step(death_settle_cards),
    Step(death_after, Timing.DEATH_AFTER),
    Step(death_time_window_close),
)
"""身份局的死亡段：多一段"亮出身份牌前"（票 25 / 决策回票 14 §4）+ 奖惩收尾步（票 33）。"""


# ---- JUDGE 帧：L14 三个时机 + 取牌 / 后果（票 26 / 决策回票 10）------------
#
# 判定是**真帧**，四步（票 10 §1）：
#   1. ``judge_on``：取牌堆顶一张（复用摸牌原语、沿用 ``shuffle`` 子流、不算摸牌）→
#      移入处理区（``reason=JUDGE``、归属 = 被判定者）→ **再** dispatch（先落牌后 dispatch，
#      使 ``JUDGE_ON`` 记录带得出判定牌）；
#   2. ``judge_before_effect``：改判**循环窗口**（票 11）：真 dispatch 后按
#      ``WindowCause.REJUDGE`` 逐个问有能力的座位；无能力者 ⇒ 队列恒空、无询问；
#   3. ``judge_after_effect``：结果（花色 / 点数 / 牌名）由后果 op 直接读帧私有 ``judge_card``；
#   4. ``judge_finish``：判定结算结束 ⇒ 判定牌 ``PROCESSING→DISCARD``（已被效果获得则跳过），
#      随后调后果 op。
#
# 帧私有 ``data`` 把 **``subject``（被判定者）** 与 **``judge_card``（判定牌）** 与
# ``card``（渠道牌）**分列**（票 10 §1）：三个判定时机的记录一律 ``actor = subject``，
# ``cards = (judge_card,)``，故【闪电】伤害的渠道牌永远不会错成判定牌。


def _judge_actor(frame: Frame) -> int:
    return frame.data["subject"]


def _judge_cards(frame: Frame) -> tuple[CardRef, ...]:
    judge_card = frame.data.get("judge_card")
    return (judge_card,) if judge_card is not None else ()


def _emit_judge(host: FrameHost, frame: Frame, timing: Timing) -> Control:
    return _emit(host, frame, timing, actor=_judge_actor(frame), cards=_judge_cards(frame))


def judge_on(host: FrameHost, frame: Frame) -> Control:
    """取判定牌 → 入处理区 → **再** dispatch ``JUDGE_ON``（票 10 §1 步骤 1）。

    短摸（两堆皆空）⇒ 引擎已判 ``both_decks_empty`` 平局并终止；本步不再落牌、不再 dispatch。
    """
    if not host.draw_judge_card(frame):
        return None
    host.move_judge_card_to_processing(frame)
    return _emit_judge(host, frame, Timing.JUDGE_ON)


REJUDGE_EMITTED = "rejudge_emitted"
"""帧私有键：``JUDGE_CARD_BEFORE_EFFECT`` 的时机记录已落（重入时不再重发）。"""

REJUDGE_ASKED = "rejudge_asked"
"""帧私有键：本帧改判窗口里**已问过的座位**（列表，插入序确定；不用集合，票 08 §7）。

"同一次判定的改判机会：每名可改判的角色只有一次响应机会，放弃后不能再响应"
（规则 11.3）：这个名单**跨替代重开**存活，故同一座位不会因循环被问第二次。
"""


def judge_before_effect(host: FrameHost, frame: Frame) -> Control:
    """改判窗口（票 11 §②）：``JUDGE_CARD_BEFORE_EFFECT`` 时点 + **循环**改判响应。

    1. 真 dispatch 一次 ``JUDGE_CARD_BEFORE_EFFECT``（既有语义不变；M2 无改判技能
       ⇒ 队列恒空、无询问、无额外日志节点）。时机记录**每个判定帧只落一次**
       （``REJUDGE_EMITTED`` 守卫重入；循环重开不改时点序，票 11 §验收 7）。
    2. 再在 :func:`_rejudge_window` 里逐个问**有改判能力**的座位（``WindowCause.REJUDGE``、
       ``required=None``、``mode=REJUDGE`` = **用一张牌代替 / 替换**判定牌；候选声明由
       :func:`~sgs.skills.rejudge_offers` 按该座位的技能定义现算——标准【鬼才】只有手牌、
       界【鬼才】另有装备区、张角【鬼道】只收黑色牌）；每次替代压一个 :class:`FrameKind.REJUDGE`
       改判事件帧，帧出栈后本步重入、窗口重开，直到无人再替代。
    """
    if not frame.data.get(REJUDGE_EMITTED):
        control = _emit_judge(host, frame, Timing.JUDGE_CARD_BEFORE_EFFECT)
        if control is not None:
            return control
        frame.data[REJUDGE_EMITTED] = True
    return _rejudge_window(host, frame)


def _rejudge_window(host: FrameHost, frame: Frame) -> Control:
    """改判循环窗口：每次替代后重开，应答者区间**逐轮重滤**（票 11 §② / 规则 11.3）。

    循环口径（三处都必要）：

    - **重开**：选中 ``SubstituteJudgeCard`` ⇒
      :meth:`~sgs.engine.steps.FrameHost.substitute_judge_card` 把新牌变成判定牌
      （**新的"生效前"**）并压一个 **REJUDGE 改判事件帧**，下一轮按新的局面重扫应答者；
    - **每座位一次**：``REJUDGE_ASKED`` 记录已问过的座位，故同一座位不因循环被问第二次；
    - **绑帧与重滤**：每轮都从 ``rejudge_askers(存活座位, 有能力座位)`` 现算——窗口内
      有人死亡时，死者自动从区间里消失（"冻结元组"是 M2 濒死窗口的口径，不搬到这里）；
    - **空候选不问**：某座位这一轮**没有可用来改判的牌**（没手牌 / 【鬼道】没有黑色牌）
      ⇒ 先算一遍候选，只有 ``Pass`` 就不发这一问、直接记进"已问过"
      （`edge-cases.md` 总纪律 1：绝不发一条玩家无法合法应答的询问）。

    ``ask_respond`` 的帧内应答缓存让挂起重入续跑同一次询问；``asked`` 在**应答到达后**
    才追加，故重入时仍选中同一座位、消费那条应答。
    """
    while True:
        askers = rejudge_askers(host.state, rejudge_capable_seats(host.state))
        asked = frame.data.setdefault(REJUDGE_ASKED, [])
        seat = next((candidate for candidate in askers if candidate not in asked), None)
        if seat is None:
            return None
        offers = rejudge_offers(host.state, seat)
        preview = host.respond_candidates(
            seat,
            cause=WindowCause.REJUDGE,
            mode=RespondMode.REJUDGE,
            rejudge_offers=offers,
        )
        if not any(not isinstance(candidate, Pass) for candidate in preview):
            asked.append(seat)
            continue
        action = host.ask_respond(
            frame,
            seat,
            Timing.JUDGE_CARD_BEFORE_EFFECT,
            cause=WindowCause.REJUDGE,
            required=None,
            window_frame=frame.frame_id,
            mode=RespondMode.REJUDGE,
            rejudge_offers=offers,
        )
        if host.suspended(action):
            return "stay"
        asked.append(seat)
        if isinstance(action, SubstituteJudgeCard):
            control = host.substitute_judge_card(
                frame, seat=seat, new_card=action.card, skill_id=action.skill_id
            )
            if control is not None:
                return control


def judge_after_effect(host: FrameHost, frame: Frame) -> Control:
    """判定牌生效后：结果已是帧私有 ``judge_card`` 的牌面，后果 op 在 ``judge_finish`` 读它。"""
    return _emit_judge(host, frame, Timing.JUDGE_CARD_AFTER_EFFECT)


def judge_finish(host: FrameHost, frame: Frame) -> None:
    """判定结算结束：判定牌入弃牌堆，**随后**调后果 op（票 10 §1 步骤 4）。"""
    host.discard_judge_card(frame)
    host.apply_judge_after(frame)


JUDGE_STEPS = (
    Step(judge_on, Timing.JUDGE_ON),
    Step(judge_before_effect, Timing.JUDGE_CARD_BEFORE_EFFECT),
    Step(judge_after_effect, Timing.JUDGE_CARD_AFTER_EFFECT),
    Step(judge_finish, None),
)
"""判定帧四步；``judge_finish`` **非 dispatch**（入弃牌堆是动作，没有对应时机节点）。"""


# ---- REJUDGE 帧：改判事件（feng-zhangjiao 票 02）-----------------------------
#
# 用户 2026-11-02 裁定：**代替 / 替换走特殊的改判事件帧，不要走打出**。打出只发生在
# "手牌 → 处理区 → 弃牌堆"的牌响应里（走 L6、触发「打出时」钩子），而改判可以拿**装备区**
# 的牌、也不触发任何"打出 / 使用"钩子——故它有自己的事件帧，帧内两段移动：
#   1. 新牌 → 处理区（`reason = JUDGE`，它成为新的判定牌；归属 = 被判定者）；
#   2. 原判定牌 → 弃牌堆（**代替**，缺省）或 → **改判者手牌**（**替换**，`reason = PUT`）。
# 去向由改判技能的定义字段 `rejudge_original_to` 回答，帧只读动作自带的 `skill_id`。

REJUDGE_NEW_MOVED = "rejudge_new_moved"
"""帧私有键：**新牌**那一段已经搬过（挂起重入时不重搬）。

与 MOVER 的既有范式一致（``_advance_judge_substitution``）：标志在调搬牌**之前**落，
故 ``move_in_frame`` 因内联钩子提问返回 ``"stay"`` 后，重入直接进第二段。
"""


def rejudge_substitute(host: FrameHost, frame: Frame) -> Control:
    """改判事件帧的唯一（**非 dispatch**）步：搬两段牌。

    - 第一段：``new`` → 处理区。归属 = **被判定者**（判定牌不为主人拥有，§11.6）。
    - 第二段：``old`` → 弃牌堆 / 改判者手牌。**原牌已不在处理区**（被【天妒】一类拿走）
      ⇒ 这一段跳过，不是错误——改判既已生效，原牌的去向由那次获得决定。
    """
    subject = frame.data["subject"]
    seat = frame.data["seat"]
    if not frame.data.get(REJUDGE_NEW_MOVED):
        frame.data[REJUDGE_NEW_MOVED] = True
        control = host.move_in_frame(
            frame,
            (frame.data["new"],),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.JUDGE,
            owner=subject,
            actor=seat,
        )
        if control is not None:
            return control
    old = frame.data["old"]
    if old.card_id not in host.state.processing:
        return None
    if frame.data.get("original_to") == RejudgeOriginal.HAND.value:
        return host.move_in_frame(
            frame,
            (old,),
            to_zone=ZoneRef(Zone.HAND, seat),
            reason=MoveReason.PUT,
            owner=seat,
            actor=seat,
        )
    return host.move_in_frame(
        frame,
        (old,),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.JUDGE,
        owner=subject,
        actor=subject,
    )


REJUDGE_STEPS = (Step(rejudge_substitute, None),)
"""改判事件帧：一个非 dispatch 步（改判没有自己的时机枚举位，它是判定帧的一个子事件）。"""


# ---- RECOVER 帧：L10 两个时机 ----------------------------------------------


def recover_value(host: FrameHost, frame: Frame) -> Control:
    """**确定回复数值**：先跑时机，再把体力写高（夹在体力上限内）。

    官方 4.13："回复体力"是数值变化四类事件之一，回复值受**体力上限**限制；
    目标值由本帧算，原语只负责写（票 07 §1）。回复量本身先过
    :func:`~sgs.effects.recover_amount` 链（票 09 的 ``RECOVER_AMOUNT``，缺省 1）——
    【救援】在此把"其他吴势力角色对孙权用【桃】"的回复量改成 2。

    **回复被替换**（M6 票 17 的界孙权【救援】"改为令你回复 1 点体力"）：钩子在
    ``RECOVER_VALUE`` 里写 :data:`~sgs.skills.JIE_JIUYUAN_REPLACED_KEY` 并压好主公的
    RECOVER 子帧。子帧出栈后本步重入，这里**跳过体力写入与 ``RECOVER_AFTER``**（目标没有
    回复过体力），整帧就此收尾；"然后摸一张"由钩子自己在第二次进入时补上。
    """
    target = frame.data["targets"][0]
    control = _emit(host, frame, Timing.RECOVER_VALUE, actor=target)
    if control is not None:
        return control
    if frame.data.get(JIE_JIUYUAN_REPLACED_KEY):
        return Goto(len(RECOVER_STEPS))
    amount = recover_amount(
        host.state,
        source=frame.data["source"],
        card=frame.data.get("card"),
        target=target,
        base=frame.data["amount"],
    )
    player = host.state.player(target)
    host.apply_hp_change(frame, target, min(player.hp + amount, player.hp_max))
    return None


def recover_after(host: FrameHost, frame: Frame) -> Control:
    return _emit(host, frame, Timing.RECOVER_AFTER, actor=frame.data["targets"][0])


RECOVER_STEPS = (
    Step(recover_value, Timing.RECOVER_VALUE),
    Step(recover_after, Timing.RECOVER_AFTER),
)


# ---- MAXLIFE 帧：L11 的加 / 减体力上限（wuming 票 04）------------------------
#
# 规则 §4.15 L429–L439：**加体力上限**与**减体力上限**是**两个独立流程**，各只有
# 「前 / 后」两个时机，没有「增减体力上限时」。故帧私有带 `direction` + `amount`，
# 步骤表按方向选时机对，`build_steps` 只登记一张表：
#
#   ① 派发 `MAXLIFE_INCREASE_BEFORE` → ② 落上限 + `hp_max_change` →
#   ③ 派发 `MAXLIFE_INCREASE_AFTER`（减侧换成 `MAXLIFE_DECREASE_*`，其余同形）。
#
# **本票只接"加"**（唯一入口 `Engine.raise_hp_max`）：减侧分支写好但**暂无调用者**——
# 既有 `Engine.reduce_hp_max`（界徐庶【潜心】/ 界吕蒙【勤学】）不迁进来，
# `MAXLIFE_DECREASE_*` 也**不解封**（迁会改既有 `hp_max_change.frame_id` 并多两条时机
# 记录 ⇒ 金录像字节变化）。将来迁移时**只换调用点**，本表不必再动。

MAXLIFE_INCREASE = "increase"
MAXLIFE_DECREASE = "decrease"
"""``FrameKind.MAXLIFE`` 帧私有的 ``direction`` 取值（加 / 减是两个独立流程，§4.15）。"""


def _maxlife_timings(direction: str) -> tuple[Timing, Timing]:
    """方向 → （前, 后）两个时机；未知方向是编程错误（fail-closed，不落半套流程）。"""
    if direction == MAXLIFE_INCREASE:
        return Timing.MAXLIFE_INCREASE_BEFORE, Timing.MAXLIFE_INCREASE_AFTER
    if direction == MAXLIFE_DECREASE:
        return Timing.MAXLIFE_DECREASE_BEFORE, Timing.MAXLIFE_DECREASE_AFTER
    raise ValueError(f"未知的体力上限变化方向 {direction!r}（只认 increase / decrease）")


def maxlife_before(host: FrameHost, frame: Frame) -> Control:
    """① 派发「加 / 减体力上限前」（规则 L429–L439 的"前"）。"""
    before, _ = _maxlife_timings(frame.data["direction"])
    return _emit(host, frame, before, actor=frame.data["target"])


def maxlife_apply(host: FrameHost, frame: Frame) -> None:
    """② 落体力上限并落一条 ``hp_max_change``（``amount`` = 幅度，恒正）。

    **加上限不同步体力**：规则只有减侧的"减上限前体力值等于上限 ⇒ 同步调整到新上限"
    （L436–437；只下调、不回血）；"增加上限并回复 1 点"的回复是技能的第二段，走既有
    回复原语（``RECOVER`` 帧），本帧不替调用方回血（票 04 C5）。

    减侧分支与 :meth:`~sgs.engine.Engine.reduce_hp_max` 逐条同语义（含"满体力同步下调"
    与"上限减为 0 无载体"的 fail-closed 守卫），故将来迁移只换调用点。
    """
    seat = frame.data["target"]
    amount = int(frame.data["amount"])
    player = host.state.player(seat)
    before_max, before_hp = player.hp_max, player.hp
    after_max = (
        before_max + amount
        if frame.data["direction"] == MAXLIFE_INCREASE
        else before_max - amount
    )
    if after_max < 1:
        raise ValueError(
            f"体力上限 {before_max} 减 {amount} 后为 {after_max}："
            "上限减为 0 的直接死亡流程未落地（今天没有该档载体）"
        )
    host.apply_hp_max_change(frame, seat, after_max)
    if after_max < before_max and before_hp == before_max:
        # 规则 L436–437：减上限前满体力 ⇒ 体力值同步调整到新上限（**不回血**，只下调）。
        host.apply_hp_change(frame, seat, after_max)


def maxlife_after(host: FrameHost, frame: Frame) -> Control:
    """③ 派发「加 / 减体力上限后」（规则 L430–L439 的"后"：结算【伤逝】【威重】等技能）。"""
    _, after = _maxlife_timings(frame.data["direction"])
    return _emit(host, frame, after, actor=frame.data["target"])


MAXLIFE_STEPS = (
    Step(maxlife_before, Timing.MAXLIFE_INCREASE_BEFORE),
    Step(maxlife_apply, None),
    Step(maxlife_after, Timing.MAXLIFE_INCREASE_AFTER),
)
"""L11 事件帧：前 → 落上限 → 后三格（``timing`` 只是子帧 ``return_to`` 的标签；
真正派发哪个时机由帧私有 ``direction`` 决定，减侧两个时机今天不解封）。"""


# ---- PLAY 帧：L6 两个时机（票 19 §2：打出必须独立）---------------------------


def play_declare(host: FrameHost, frame: Frame) -> Control:
    """``PLAY_DECLARE``：打出牌进入处理区（与使用的第一段移动同姿态）。"""
    control = _emit(
        host,
        frame,
        Timing.PLAY_DECLARE,
        actor=frame.data["source"],
        cards=(frame.data["card"],),
    )
    if control is not None:
        return control
    host.move_played_card_to_processing(frame)
    return None


def play_on_play(host: FrameHost, frame: Frame) -> Control:
    """``PLAY_ON_PLAY``：打出的实体牌随即进弃牌堆（打出无使用结算）。"""
    control = _emit(
        host,
        frame,
        Timing.PLAY_ON_PLAY,
        actor=frame.data["source"],
        cards=(frame.data["card"],),
    )
    if control is not None:
        return control
    host.move_played_card_to_discard(frame)
    return None


PLAY_STEPS = (
    Step(play_declare, Timing.PLAY_DECLARE),
    Step(play_on_play, Timing.PLAY_ON_PLAY),
)
"""打出事件的步骤表：两个时机构成 L6 的全部节点（票 01 的 6 个转 dispatch 之一对）。"""


# ---- SKILL 帧：主动技的发动主体（票 04）--------------------------------------
#
# 出牌阶段空闲时间点答 ``ActivateSkill`` ⇒ 压一个 SKILL 帧；帧只有一步：按 ``skill_id``
# 查 ``sgs.skills.ACTIVATIONS`` 通用派发（引擎侧不出现任何具体技能 id，票 07 §5）。
# 回调可以提问 / 压子帧，故沿用既有的"返回 stay ⇒ 原地重入"范式（票 19 §8）。


def skill_activate(host: FrameHost, frame: Frame) -> Control:
    """SKILL 帧的唯一步骤；控制值由发动回调原样决定（``stay`` = 本次没跑完）。"""
    return activate_skill(host, frame)


SKILL_STEPS = (Step(skill_activate, None),)
"""主动技帧：非 dispatch 的单步（``None`` = 这一步本身不对应任何时机枚举位）。"""


# ---- MOVE 帧：重铸的结算主体（M5-03）----------------------------------------
#
# 重铸**不是使用**，故不能借 USE / RESOLVE 帧；但它要"弃置 → 摸一张"，其中弃置会派发
# `MOVE_AFTER`、可能提问而让出控制权。用游离步骤（回合环）做会让摸牌抢在「失去牌后」
# 之前落地；这里压一个 MOVE 帧（既有枚举位）把两件事排成帧内两步：提问暂停后从
# `pc` 续跑，摸牌自然排在「失去牌后」整条派发之后。


def reforge_lose_card(host: FrameHost, frame: Frame) -> Control:
    """重铸第一步：这张牌手牌 → 弃牌堆（``reason = REFORGE``，算失去不算弃置）。"""
    seat = frame.data["source"]
    return host.move_in_frame(
        frame,
        (frame.data["card"],),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.REFORGE,
        owner=seat,
        actor=seat,
    )


def reforge_draw(host: FrameHost, frame: Frame) -> Control:
    """重铸第二步：摸一张牌（与其它摸牌同一 ``shuffle`` 子流、同一短摸口径）。

    摸牌也算「获得牌」（票 24）之后，这一摸可能触发【清俭】并挂起；帧步**必须**把
    ``"stay"`` 原样返回，否则本步被当成"完成"、MOVE 帧出栈，待答询问失去归属。
    """
    if host.draw_for_hand(frame, frame.data["source"], 1) == "stay":
        return "stay"
    return None


MOVE_STEPS = (Step(reforge_lose_card, None), Step(reforge_draw, None))
"""重铸帧：两个非 dispatch 的步骤（``None`` = 不对应任何时机枚举位）。"""


# ---------------------------------------------------------------------------
# RANK_COMPARE 帧（huo-taishici-xunyu 票 03）：拼点
#
# 规则第十二章（L1309–L1380）：两名**有手牌**的角色各以一张**手牌**比较点数；平点 = 双方
# 都没赢；拼点不是使用 / 打出、但**属于失去牌**；拼点牌置入弃牌堆（与「弃置」不同）。
#
# **本仓的落地口径（票 03 的用户裁定 + 建模决定）**：选牌阶段牌**还留在各自手牌里**
# （这样"扣置"不需要第二个隐藏区——处理区是公开区，提前搬进去会让对手提前看到牌面），
# 全部参与者答完后**一次性**搬入处理区（``reason = PINDIAN``，即"因拼点失去一张牌"，
# 内联派发「失去牌后」⇒ 用最后一张手牌拼点照常触发【连营】）、立刻亮出并比点、派发
# ``RANK_COMPARE_RESULT_AFTER``、再把两张牌置入弃牌堆。见 `03-c1-pindian.md`。
# ---------------------------------------------------------------------------

RANK_COMPARE_ON_EMITTED = "rank_compare_on_emitted"
"""帧私有：``RANK_COMPARE_ON`` 已派发（重入不再发）。"""

RANK_COMPARE_PICKED = "rank_compare_picked"
"""帧私有：``{座位: 拼点牌 card_id}``；插入序 = 发起者、再按 ``targets`` 序。"""

RANK_COMPARE_PLACE_CURSOR = "rank_compare_place_cursor"
"""帧私有：已搬入处理区的参与者个数（搬牌可能因「失去牌后」钩子挂起）。"""

RANK_COMPARE_PLACED = "rank_compare_placed"
"""帧私有：所有拼点牌都已在处理区（下一步才是亮出）。"""

RANK_COMPARE_BOOST = "rank_compare_boost"
"""帧私有：``{座位: 增量}``——本次拼点里被技能调整过的点数（tao-caocao 票 03）。

写入面只有 :meth:`Engine.boost_pindian`（挂在 ``RANK_COMPARE_PLACED`` 上的技能调用）；
读取面只有 :func:`rank_compare_reveal` 的比点。帧私有 ⇒ **不进记录流**。
"""

RANK_COMPARE_ADJUST_EMITTED = "rank_compare_adjust_emitted"
"""帧私有：``RANK_COMPARE_PLACED`` 已派发（重入不再发）。"""

RANK_COMPARE_FLOOR = 1
"""点数的下限（规则 12.2 L1326–L1327：A = 1，小于 A 视为 A）。"""

RANK_COMPARE_CEIL = 13
"""点数的上限（同上：K = 13，大于 K 视为 K）。"""

RANK_COMPARE_RESULTS = "rank_compare_results"
"""帧私有：``((target_seat, won), ...)``，与 ``targets`` 对齐；平点 = ``won`` 假。"""

RANK_COMPARE_TIES = "rank_compare_ties"
"""帧私有：与 ``targets`` 对齐的**平点**位（tao-caocao 票 03）。

`RANK_COMPARE_RESULTS` 的 ``won`` 只有两态（平点与"没赢"都是假），而「若其没赢」这类
牌面读法必须把平点与"对方赢"分开（规则 12.1 L1318–L1319：平点双方都没赢）⇒ 另一条
同长元组随结果一起写、一起回写发起帧（``Engine.note_pindian_result`` 的 ``pindian_tied``）。
"""

RANK_COMPARE_RESULT_EMITTED = "rank_compare_result_emitted"
"""帧私有：``RANK_COMPARE_RESULT_AFTER`` 已派发。"""

RANK_COMPARE_ABORTED = "rank_compare_aborted"
"""帧私有：拼点中止（参与者没有手牌——候选阶段已挡，帧内兜底，零副作用）。"""

RANK_COMPARE_DISCARD_CURSOR = "rank_compare_discard_cursor"
"""帧私有：已置入弃牌堆的拼点牌个数。"""

_ASK_RANK_COMPARE = "pindian"
"""拼点选牌的**询问位置**（``ask_selection`` 的 ``owner``；逐参与者用不同 ``key``）。"""


def _rank_compare_order(frame: Frame) -> tuple[int, ...]:
    """参与拼点的座位序：发起者在先，被拼点者按 ``targets`` 声明序。"""
    return (int(frame.data["initiator"]), *tuple(int(seat) for seat in frame.data["targets"]))


def rank_compare_announce(host: FrameHost, frame: Frame) -> Control:
    """派发 ``RANK_COMPARE_ON``（"进行拼点时"，规则 12.3 L1340）一次。"""
    if frame.data.get(RANK_COMPARE_ON_EMITTED):
        return None
    frame.data[RANK_COMPARE_ON_EMITTED] = True
    return host.emit_frame_timing(
        frame, Timing.RANK_COMPARE_ON, actor=int(frame.data["initiator"])
    )


def rank_compare_pick(host: FrameHost, frame: Frame) -> Control:
    """逐参与者要一张**手牌**（不可放弃；超时兜底由接入层取候选第一张）。"""
    picked: dict[int, int] = frame.data.setdefault(RANK_COMPARE_PICKED, {})
    order = _rank_compare_order(frame)
    for index, seat in enumerate(order):
        if seat in picked:
            continue
        if not host.state.player(seat).hand:
            # 发起者 / 目标必须有手牌才能拼点（规则 12.1 L1317）；候选阶段已挡，
            # 帧内再兜一次：没有牌就整条中止（零副作用），后续步骤一律跳过。
            frame.data[RANK_COMPARE_ABORTED] = True
            return None
        got = host.ask_selection(
            frame,
            owner=_ASK_RANK_COMPARE,
            key=f"pindian:{index}",
            actor=seat,
            timing=Timing.RANK_COMPARE_ON,
            spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1), optional=False),
            build_face=lambda: host.selection_face(seat, (Zone.HAND,), reveal_hand=True),
        )
        if host.suspended(got):
            return "stay"
        if not got:
            return None
        picked[seat] = int(got[0])
    return None


def rank_compare_place(host: FrameHost, frame: Frame) -> Control:
    """把全部拼点牌**手牌 → 处理区**（``reason = PINDIAN``，算失去不算弃置）。"""
    if frame.data.get(RANK_COMPARE_ABORTED) or frame.data.get(RANK_COMPARE_PLACED):
        return None
    order = _rank_compare_order(frame)
    picked: dict[int, int] = frame.data[RANK_COMPARE_PICKED]
    cursor = int(frame.data.get(RANK_COMPARE_PLACE_CURSOR, 0))
    while cursor < len(order):
        seat = order[cursor]
        card_id = picked[seat]
        if card_id in host.state.processing:
            cursor += 1
            frame.data[RANK_COMPARE_PLACE_CURSOR] = cursor
            continue
        control = host.move_in_frame(
            frame,
            (host.state.card(card_id),),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.PINDIAN,
            owner=seat,
            actor=seat,
        )
        if control is not None:
            return control
        cursor += 1
        frame.data[RANK_COMPARE_PLACE_CURSOR] = cursor
    frame.data[RANK_COMPARE_PLACED] = True
    return None


def rank_compare_adjust(host: FrameHost, frame: Frame) -> Control:
    """派发 ``RANK_COMPARE_PLACED``（"牌扣置入处理区后、亮出前"）一次（tao-caocao 票 03）。

    规则 12.3 L1337–L1340：「拼点期间可以发动技能：在『牌扣置入处理区后（失去牌后）』
    以及『拼点牌亮出后』均可插入发动相关技能」。本步就是**前一个窗口**——两张牌都已在
    处理区，点数还没比。挂在这个时机上的技能可以问一句、再经
    :meth:`FrameHost.boost_pindian` 调整点数（`RANK_COMPARE_BOOST` 帧私有）。

    中止（参与者没手牌）⇒ 整段不存在：不派发，与其余后置步骤同一道闸。
    """
    if frame.data.get(RANK_COMPARE_ABORTED) or frame.data.get(RANK_COMPARE_ADJUST_EMITTED):
        return None
    if not frame.data.get(RANK_COMPARE_PLACED):
        return None
    # "已派发"位**在派发返回之后**才置：这里的钩子会提问（"点数 +X 吗"），挂起重入时
    # 本步必须**再派发一次**才能让那条钩子续跑（`emit_frame_timing` 的 `_pending_timing`
    # 负责"记录只落一条"）。
    control = host.emit_frame_timing(
        frame, Timing.RANK_COMPARE_PLACED, actor=int(frame.data["initiator"])
    )
    if control == "stay":
        return "stay"
    frame.data[RANK_COMPARE_ADJUST_EMITTED] = True
    return None


def _pindian_rank(frame: Frame, seat: int, card: Any) -> int | None:
    """``seat`` 的**生效点数** = 牌面点数 + 技能增量，再按规则钳到 [1, 13]。

    规则 12.2 L1326–L1327：「点数的上下限处理：大于 K 视为 K，小于 A 视为 A」。
    牌面点数本身为空（纯虚拟牌）时返回 ``None``——比点按"不能比"处理（与既有语义一致）。
    """
    rank = card.rank
    if rank is None:
        return None
    boost = int((frame.data.get(RANK_COMPARE_BOOST) or {}).get(seat, 0))
    return max(RANK_COMPARE_FLOOR, min(RANK_COMPARE_CEIL, int(rank) + boost))


def rank_compare_reveal(host: FrameHost, frame: Frame) -> Control:
    """亮出并比点：写结果、发 ``pindian`` delta、把结果回写发起帧。"""
    if frame.data.get(RANK_COMPARE_ABORTED):
        if RANK_COMPARE_RESULTS not in frame.data:
            frame.data[RANK_COMPARE_RESULTS] = ()
            frame.data[RANK_COMPARE_TIES] = ()
            host.note_pindian_result(frame)
        return None
    if RANK_COMPARE_RESULTS in frame.data:
        return None
    state = host.state
    picked: dict[int, int] = frame.data[RANK_COMPARE_PICKED]
    initiator = int(frame.data["initiator"])
    base = _pindian_rank(frame, initiator, state.card(picked[initiator]))
    results: list[tuple[int, bool]] = []
    ties: list[bool] = []
    for seat in frame.data["targets"]:
        seat = int(seat)
        other = _pindian_rank(frame, seat, state.card(picked[seat]))
        # 点数大者赢；点数相同 ⇒ 双方都"没赢"（规则 12.1 L1318–L1319）。
        won = base is not None and other is not None and base > other
        ties.append(base is not None and other is not None and base == other)
        results.append((seat, won))
    frame.data[RANK_COMPARE_RESULTS] = tuple(results)
    frame.data[RANK_COMPARE_TIES] = tuple(ties)
    order = _rank_compare_order(frame)
    host.emit_frame_delta(
        frame,
        PindianDelta(
            title=str(frame.data.get("title", "")),
            initiator=initiator,
            targets=tuple(int(seat) for seat in frame.data["targets"]),
            cards=tuple(state.card(picked[seat]) for seat in order),
            won=tuple(won for _seat, won in results),
            # 平点 = 双方都没赢：`won` 只有两态，展示面的「都没赢」只能靠这一位
            # （用户 2026-11-04 的 R7 口径；前端不比较点数）。
            tied=tuple(ties),
            # 生效点数（与 `cards` 对齐）：被技能调过时要画得出"5 点为什么赢 / 输"。
            # 缺省空元组 = 旧记录没有这个事实（前端退回牌面，不猜）。
            ranks=tuple(
                _pindian_rank(frame, seat, state.card(picked[seat])) for seat in order
            ),
        ),
    )
    host.note_pindian_result(frame)
    return None


def rank_compare_result(host: FrameHost, frame: Frame) -> Control:
    """派发 ``RANK_COMPARE_RESULT_AFTER``（"赢 / 没赢后"，规则 12.3 L1341）一次。"""
    if frame.data.get(RANK_COMPARE_ABORTED) or frame.data.get(RANK_COMPARE_RESULT_EMITTED):
        return None
    frame.data[RANK_COMPARE_RESULT_EMITTED] = True
    return host.emit_frame_timing(
        frame, Timing.RANK_COMPARE_RESULT_AFTER, actor=int(frame.data["initiator"])
    )


def rank_compare_discard(host: FrameHost, frame: Frame) -> Control:
    """拼点牌**处理区 → 弃牌堆**（``reason = PINDIAN``；不是"弃置"）。"""
    if frame.data.get(RANK_COMPARE_ABORTED):
        return None
    order = _rank_compare_order(frame)
    picked: dict[int, int] = frame.data[RANK_COMPARE_PICKED]
    cursor = int(frame.data.get(RANK_COMPARE_DISCARD_CURSOR, 0))
    while cursor < len(order):
        seat = order[cursor]
        card_id = picked[seat]
        if card_id not in host.state.processing:
            cursor += 1
            frame.data[RANK_COMPARE_DISCARD_CURSOR] = cursor
            continue
        control = host.move_in_frame(
            frame,
            (host.state.card(card_id),),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.PINDIAN,
            owner=seat,
            actor=seat,
        )
        if control is not None:
            return control
        cursor += 1
        frame.data[RANK_COMPARE_DISCARD_CURSOR] = cursor
    return None


RANK_COMPARE_STEPS = (
    Step(rank_compare_announce, Timing.RANK_COMPARE_ON),
    Step(rank_compare_pick, None),
    Step(rank_compare_place, None),
    Step(rank_compare_adjust, Timing.RANK_COMPARE_PLACED),
    Step(rank_compare_reveal, None),
    Step(rank_compare_result, Timing.RANK_COMPARE_RESULT_AFTER),
    Step(rank_compare_discard, None),
)
"""拼点帧七步：派发 ON → 逐参与者选牌 → 扣置（处理区）→ 派发 PLACED（扣置后、亮出前，
技能可在此调整点数）→ 亮出比点 → 派发 RESULT_AFTER → 置入弃牌堆。"""


def build_steps(mode: Mode = Mode.NO_IDENTITY) -> StepTable:
    """按模式装配步骤表：只有 DEATH 帧的形状分叉（票 25）。"""
    return {
        FrameKind.USE: USE_STEPS,
        FrameKind.RESOLVE: RESOLVE_STEPS,
        FrameKind.PLAY: PLAY_STEPS,
        FrameKind.SKILL: SKILL_STEPS,
        FrameKind.MOVE: MOVE_STEPS,
        FrameKind.DAMAGE: DAMAGE_STEPS,
        FrameKind.LIFE_DECREASE: LIFE_DECREASE_STEPS,
        FrameKind.NEAR_DEATH: NEAR_DEATH_STEPS,
        FrameKind.DEATH: IDENTITY_DEATH_STEPS if mode is Mode.IDENTITY else DEATH_STEPS,
        FrameKind.RECOVER: RECOVER_STEPS,
        FrameKind.MAXLIFE: MAXLIFE_STEPS,
        FrameKind.JUDGE: JUDGE_STEPS,
        FrameKind.REJUDGE: REJUDGE_STEPS,
        FrameKind.RANK_COMPARE: RANK_COMPARE_STEPS,
    }
