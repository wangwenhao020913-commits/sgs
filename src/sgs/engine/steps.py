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
  亮明成一条 ``identity_reveal``（在时机 dispatch 之后、胜负检测之前）。`DEATH_ON` 先
  执行 Ⅰ.a（弃置死者区域里的牌）再执行 Ⅱ 奖惩；检测点在 `DEATH_REVEAL_IDENTITY_AFTER`，
  只记暂定胜负、不在那里终止（批次关闭由引擎的 DEATH 出栈逻辑宣布）。
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
    NULLIFY_RESPONSE_CARD,
    DamageNature,
    EffectStep,
    FinalZone,
    ResponseWindow,
    behavior_for,
    damage_amount,
    is_slash,
    recover_amount,
    response_count,
    target_is_effective,
)
from sgs.resolution import Control, Frame, Goto, Step, StepHost, StepTable, Tag, has_tag
from sgs.skills import (
    DAMAGE_DEAL_POINT_KEY,
    DAMAGE_DEAL_RECORDED_KEY,
    DAMAGE_POINT_KEY,
    DAMAGE_RECORDED_KEY,
    QIANXUN_PLACED_KEY,
    SeamKind,
    activate_skill,
    rejudge_capable_seats,
    target_is_ineffective,
    virtual_dodge,
)
from sgs.state import (
    ActivateSkill,
    CardRef,
    DodgeContext,
    FrameKind,
    GameState,
    MoveReason,
    PlayCard,
    QueryKind,
    RespondMode,
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

    def move_used_card_to_equip_zone(self, frame: Frame) -> None: ...

    def move_processing_card_to_discard(self, frame: Frame) -> None: ...

    def draw_judge_card(self, frame: Frame) -> bool: ...

    def move_judge_card_to_processing(self, frame: Frame) -> None: ...

    def discard_judge_card(self, frame: Frame) -> None: ...

    def substitute_judge_card(self, frame: Frame, *, seat: int, new_card: CardRef) -> Control: ...

    def resume_judge_substitution(self, frame: Frame) -> Control: ...

    def apply_judge_after(self, frame: Frame) -> None: ...

    def skip_play_phase(self, seat: int) -> None: ...

    def consume_delayed_card(self, frame: Frame) -> None: ...

    def enter_death(self, frame: Frame, seat: int) -> Frame: ...

    def discard_dead_cards(self, frame: Frame) -> None: ...

    def reveal_dead_identity(self, frame: Frame) -> None: ...

    def apply_death_rewards(self, frame: Frame) -> None: ...

    def record_death_fact(self, frame: Frame) -> None: ...

    def check_game_end(self) -> Any: ...

    def push_frame(self, kind: FrameKind, data: dict[str, Any], *, timing: Timing) -> Frame: ...

    def dispatch_seam(self, frame: Frame, kind: SeamKind) -> Any: ...

    def provider_registered(self, skill_id: str) -> bool: ...

    def advance_provider(self, frame: Frame, timing: Timing) -> Any: ...

    def start_provider(self, frame: Frame, skill_id: str, actor: int, timing: Timing) -> Any: ...

    def open_skill_judgment(self, frame: Frame, subject: int, skill_id: str) -> None: ...

    def ask_query(
        self,
        kind: QueryKind,
        actor: int,
        timing: Timing,
        *,
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
    ) -> Any: ...

    def suspended(self, value: Any) -> bool: ...

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
        self, frame: Frame, action: UseCard, *, asker: int, dying: int
    ) -> None: ...

    def apply_effects(
        self, frame: Frame, effects: Sequence[EffectStep], *, timing: Timing
    ) -> Any: ...

    def apply_hp_change(self, frame: Frame, seat: int, value: int) -> Any: ...

    def move_in_frame(
        self,
        frame: Frame | None,
        cards: tuple[CardRef, ...],
        *,
        to_zone: ZoneRef,
        reason: MoveReason,
        owner: int | None,
        actor: int | None = None,
    ) -> Control: ...

    def draw_for_hand(self, frame: Frame, seat: int, n: int) -> bool: ...

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
    """
    behavior = behavior_for(frame.data["card"])
    if behavior.is_delayed:
        host.move_used_card_to_judge_zone(frame)
        return None
    if not frame.data.get("after_end_done"):
        if behavior.final_zone is FinalZone.EQUIP:
            host.move_used_card_to_equip_zone(frame)
        else:
            host.move_processing_card_to_discard(frame)
        control = _emit(
            host,
            frame,
            Timing.RESOLVE_AFTER_END,
            actor=frame.data["source"],
            cards=(frame.data["card"],),
        )
        frame.data["after_end_done"] = True
        if control is not None:
            return control
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
    """
    required = response_count(
        host.state, source=frame.data["source"], card=frame.data["card"], target=target
    )
    done = frame.data.get("dodges_done", 0)
    control, satisfied = host.advance_provider(frame, Timing.RESOLVE_BEFORE_EFFECT)
    if control is not None:
        return control
    if satisfied is not None:
        if satisfied:
            done += 1
            frame.data["dodges_done"] = done
            if done >= required:
                frame.data["canceled"] = True
        return "stay"
    if "skill_result" in frame.data:
        success = frame.data.pop("skill_result")
        if success:
            # "视为使用【闪】"仍是**一次**使用：走与真牌同一条使用链（票 08 的一手读法）。
            done += 1
            frame.data["dodges_done"] = done
            if done >= required:
                frame.data["canceled"] = True
            host.start_use(
                virtual_dodge(host.state),
                source=target,
                targets=(),
                use_timing=Timing.RESOLVE_BEFORE_EFFECT,
            )
            return "stay"
    providers_used = tuple(frame.data.get("providers_used", ()))
    action = host.ask_query(
        QueryKind.DODGE,
        target,
        Timing.RESOLVE_BEFORE_EFFECT,
        payload=DodgeContext(providers_used=providers_used),
    )
    if host.suspended(action):
        return "stay"
    if isinstance(action, ActivateSkill):
        frame.data.setdefault("providers_used", []).append(action.skill_id)
        if not host.provider_registered(action.skill_id):
            host.open_skill_judgment(frame, target, action.skill_id)
            return "stay"
        control, satisfied = host.start_provider(
            frame, action.skill_id, target, Timing.RESOLVE_BEFORE_EFFECT
        )
        if control is not None:
            return control
        if satisfied:
            done += 1
            frame.data["dodges_done"] = done
            if done >= required:
                frame.data["canceled"] = True
        return "stay"
    if isinstance(action, UseCard) and action.card.type == DODGE_RESPONSE_CARD:
        done += 1
        frame.data["dodges_done"] = done
        if done >= required:
            frame.data["canceled"] = True
        # 抵消是"被响应帧"的标志；【闪】自己作为一次**使用**照走 L4 / L5（票 07 §3）。
        host.start_use(action.card, source=target, targets=())
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

    声明**按询问序追加**，故首个声明者就是优先级最高者（应答者区间 =
    当前回合角色起逆时针、按座位序，票 15 §2）；输家的声明不耗牌、不压任何帧。
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
        asker = askers[frame.data["response_next"]]
        action = host.ask_respond(
            frame,
            asker,
            Timing.RESOLVE_BEFORE_EFFECT,
            cause=WindowCause.NULLIFY,
            required=NULLIFY_RESPONSE_CARD,
            target=target,
            window_frame=frame.frame_id,
            mode=RespondMode.USE,
        )
        if host.suspended(action):
            return "stay"
        frame.data["response_next"] += 1
        if isinstance(action, UseCard):
            # **声明**：行动被接受，但此刻不耗牌、不压 USE 帧（票 19 §9.4）。
            frame.data["response_declared"].append((asker, action.card))
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
    "skill_result",
    "dodges_done",
    "use_tags",
    "axe_used",
    "gdb_used",
    "provider_skill",
    "provider_actor",
    "provider_satisfied",
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

票 08 追加两个按目标分槽的键：``dodges_done``（本次响应窗口已攒够几张）与 ``use_tags``
（帧标签袋的**逐目标档**——登在这里使"逐目标档随游标推进自动清除"是结构性的、不靠手工
``del``）。**全场档**住 :data:`sgs.resolution.WHOLE_FIELD_TAGS_KEY`，**刻意不登在这里**：
它作用于整次使用的每一个目标，多目标【杀】推进游标时必须存活（R4-Q1 只要求登记逐目标档；
code-review C3 把两档拆成两个帧私有键，见 ``tests/test_hero_response_window.py``）。
票 09 再追加提供者展开的四个键（``provider_skill`` / ``provider_actor`` /
``provider_satisfied`` / ``lord_request``）：它们是**一次求助窗口**的进度，多目标【杀】里
必须逐目标重来，否则后一个目标会捡到前一个目标的应答者区间。
票 05 追加逐点触发的两个键（``damage_point_index`` / ``damage_recorded``）：DAMAGE 帧今天
恒单目标（多目标由行为表逐目标各压一个子帧），登在这里是姿态上的一致——真出现"一帧多目标"
时它们同样必须逐目标重来。
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
    """**扣减体力后**：照常空实现收尾。

    无人救的死亡路径上这一步**不会被走到**——濒死帧在"濒死结算结束后"直接压入 DEATH
    子帧并留在栈上，整条伤害链（含本帧）由终局清栈统一丢弃（票 22）；救回时才回到这里。
    """
    return _emit(host, frame, Timing.LIFE_DECREASE_AFTER, actor=frame.data["target"])


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
        action = host.ask_query(QueryKind.PEACH, asker, Timing.NEAR_DEATH_AT, dying=target)
        if host.suspended(action):
            return "stay"
        if isinstance(action, UseCard):
            frame.data["pending_response"] = (idx, host.state.player(target).hp)
            host.recover_from_peach(frame, action, asker=asker, dying=target)
            return "stay"  # 响应链出栈后重入本步：此处判回退
        frame.data["next"] = idx + 1
    return None


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
#   - ``DEATH_ON`` = 死亡时技能 → Ⅰ.a 系统处理牌 → Ⅱ 奖惩（票 14 §4 的段序）；
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


def death_on(host: FrameHost, frame: Frame) -> Control:
    """死亡时：先 dispatch（M2 无死亡时技能），再 Ⅰ.a 系统处理牌、最后 Ⅱ 奖惩。"""
    control = _emit_death(host, frame, Timing.DEATH_ON)
    if control is not None:
        return control
    host.discard_dead_cards(frame)
    host.apply_death_rewards(frame)
    return None


def death_after(host: FrameHost, frame: Frame) -> Control:
    """空实现：死亡结算收尾空时点（票 13 §4 的 C 类）。"""
    return _emit_death(host, frame, Timing.DEATH_AFTER)


DEATH_STEPS = (
    Step(death_before_start, Timing.DEATH_BEFORE_START),
    Step(death_reveal_identity_after, Timing.DEATH_REVEAL_IDENTITY_AFTER),
    Step(death_on, Timing.DEATH_ON),
    Step(death_after, Timing.DEATH_AFTER),
)
"""无身份局（1v1）的死亡段：M1 的四段，与既有一字不差。"""

IDENTITY_DEATH_STEPS = (
    Step(death_before_start, Timing.DEATH_BEFORE_START),
    Step(death_reveal_identity_before, Timing.DEATH_REVEAL_IDENTITY_BEFORE),
    Step(death_reveal_identity_after, Timing.DEATH_REVEAL_IDENTITY_AFTER),
    Step(death_on, Timing.DEATH_ON),
    Step(death_after, Timing.DEATH_AFTER),
)
"""身份局的死亡段：多一段"亮出身份牌前"（票 25 / 决策回票 14 §4）。"""


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
    2. 先把上一次替代的**在途进度**收尾（``resume_judge_substitution``；code-review C2：
       替代的第一段移动可能因内联钩子提问而挂起，而 ``_rejudge_window`` 已把这个座位
       记进"已问过"、不会再次进来），再在 :func:`_rejudge_window` 里逐个问**有改判能力**
       的座位（``WindowCause.REJUDGE``、``required=None``、``mode=PLAY`` = 打出一张**手牌**
       代替之）；每次替代后窗口重开，直到无人再替代。
    """
    if not frame.data.get(REJUDGE_EMITTED):
        control = _emit_judge(host, frame, Timing.JUDGE_CARD_BEFORE_EFFECT)
        if control is not None:
            return control
        frame.data[REJUDGE_EMITTED] = True
    control = host.resume_judge_substitution(frame)
    if control is not None:
        return control
    return _rejudge_window(host, frame)


def _rejudge_window(host: FrameHost, frame: Frame) -> Control:
    """改判循环窗口：每次替代后重开，应答者区间**逐轮重滤**（票 11 §② / 规则 11.3）。

    循环口径（三处都必要）：

    - **重开**：选中 ``PlayCard`` ⇒ :meth:`~sgs.engine.steps.FrameHost.substitute_judge_card`
      把新牌变成判定牌（新的"生效前"），下一轮按新的局面重扫应答者；
    - **每座位一次**：``REJUDGE_ASKED`` 记录已问过的座位，故同一座位不因循环被问第二次；
    - **绑帧与重滤**：每轮都从 ``rejudge_askers(存活座位, 有能力座位)`` 现算——窗口内
      有人死亡时，死者自动从区间里消失（"冻结元组"是 M2 濒死窗口的口径，不搬到这里）。

    ``ask_respond`` 的帧内应答缓存让挂起重入续跑同一次询问；``asked`` 在**应答到达后**
    才追加，故重入时仍选中同一座位、消费那条应答。
    """
    while True:
        askers = rejudge_askers(host.state, rejudge_capable_seats(host.state))
        asked = frame.data.setdefault(REJUDGE_ASKED, [])
        seat = next((candidate for candidate in askers if candidate not in asked), None)
        if seat is None:
            return None
        action = host.ask_respond(
            frame,
            seat,
            Timing.JUDGE_CARD_BEFORE_EFFECT,
            cause=WindowCause.REJUDGE,
            required=None,
            window_frame=frame.frame_id,
            mode=RespondMode.PLAY,
        )
        if host.suspended(action):
            return "stay"
        asked.append(seat)
        if isinstance(action, PlayCard):
            control = host.substitute_judge_card(frame, seat=seat, new_card=action.card)
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


# ---- RECOVER 帧：L10 两个时机 ----------------------------------------------


def recover_value(host: FrameHost, frame: Frame) -> Control:
    """**确定回复数值**：先跑时机，再把体力写高（夹在体力上限内）。

    官方 4.13："回复体力"是数值变化四类事件之一，回复值受**体力上限**限制；
    目标值由本帧算，原语只负责写（票 07 §1）。回复量本身先过
    :func:`~sgs.effects.recover_amount` 链（票 09 的 ``RECOVER_AMOUNT``，缺省 1）——
    【救援】在此把"其他吴势力角色对孙权用【桃】"的回复量改成 2。
    """
    target = frame.data["targets"][0]
    control = _emit(host, frame, Timing.RECOVER_VALUE, actor=target)
    if control is not None:
        return control
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
    """重铸第二步：摸一张牌（与其它摸牌同一 ``shuffle`` 子流、同一短摸口径）。"""
    host.draw_for_hand(frame, frame.data["source"], 1)
    return None


MOVE_STEPS = (Step(reforge_lose_card, None), Step(reforge_draw, None))
"""重铸帧：两个非 dispatch 的步骤（``None`` = 不对应任何时机枚举位）。"""


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
        FrameKind.JUDGE: JUDGE_STEPS,
    }
