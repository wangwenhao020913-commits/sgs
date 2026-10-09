"""各帧的步骤表：把官方流程写成**数据**（票 04 §1，票 07 §2–§3）。

- **USE 帧**（使用一张牌）：L4 六个时机 → 压入 RESOLVE 子帧 → L5.6 把处理区里的
  实体牌置入弃牌堆。实体牌在 L4.1 / L4.2 之间从手牌移入处理区（票 07 §3）。
- **RESOLVE 帧**（逐目标结算）：L5 五个时机；"生效前"按**行为表**决定是否开响应窗口，
  被抵消则 ``Goto(RESOLVE_END_PC)`` 跳过"生效时 / 生效后"，直达"使用结算结束时"。
- **DAMAGE 帧**（票 20）：L7 五个时机；"受到伤害时"之后压入 **LIFE_DECREASE** 子帧，
  在"造成伤害后"之前把体力真正扣掉。
- **LIFE_DECREASE 帧**（票 20 / 21）：L8 三个时机；"扣减体力时"是唯一的体力写入点。
  体力降到 0 及以下就在那里插入 **NEAR_DEATH** 子帧（票 21）；救回则照常走完"扣减体力后"。
- **NEAR_DEATH 帧**（票 21）：L12 三个时机；"处于濒死状态时"按官方顺序逐个求桃
  （当前回合角色起、逆时针、一人一次），有人出桃则压入 **RECOVER** 子帧、在其中把
  体力恢复 1 点，然后回到被中断的结算。无人救时在"濒死结算结束后"压入 **DEATH** 子帧
  （票 22）——死亡结算作为原子单元跑完，濒死帧与整条伤害链留给终局清栈。
- **DEATH 帧**（票 22 / 票 25）：L13 裁剪后的死亡段，**按模式分叉**。无身份局四段
  （`DEATH_REVEAL_IDENTITY_BEFORE` 不入表、不 dispatch）；身份局五段——入表并把身份
  亮明成一条 ``identity_reveal``（在时机 dispatch 之后、胜负检测之前）。`DEATH_ON` 先
  执行 Ⅰ.a（弃置死者区域里的牌）再执行 Ⅱ 奖惩；检测点在 `DEATH_REVEAL_IDENTITY_AFTER`，
  只记暂定胜负、不在那里终止（批次关闭由引擎的 DEATH 出栈逻辑宣布）。
- **RECOVER 帧**（票 21）：L10 两个时机；"确定回复数值"之后把体力写高（夹在上限内）。
- **JUDGE 帧**（票 26）：L14 三个时机；四步 = 取牌入处理区（复用摸牌原语、沿用 ``shuffle``
  子流、不算摸牌）→ 改判窗口（真 dispatch、队列恒空）→ 结果 → 判定牌入弃牌堆后调后果 op。
  帧私有 ``subject`` / ``judge_card`` 与渠道牌 ``card`` 分列（票 10 §1）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from sgs.data import Identity, Mode
from sgs.effects import (
    EffectStep,
    behavior_for,
    opens_response_window,
    target_is_effective,
)
from sgs.resolution import Control, Frame, Goto, Step, StepHost, StepTable
from sgs.skills import rejudge_capable_seats
from sgs.state import (
    CardRef,
    FrameKind,
    GameState,
    QueryKind,
    UseCard,
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

    def move_used_card_to_processing(self, frame: Frame) -> None: ...

    def move_used_card_to_judge_zone(self, frame: Frame) -> None: ...

    def move_processing_card_to_discard(self, frame: Frame) -> None: ...

    def draw_judge_card(self, frame: Frame) -> bool: ...

    def move_judge_card_to_processing(self, frame: Frame) -> None: ...

    def discard_judge_card(self, frame: Frame) -> None: ...

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

    def ask_query(
        self, kind: QueryKind, actor: int, timing: Timing, *, dying: int | None = None
    ) -> Any: ...

    def suspended(self, value: Any) -> bool: ...

    def start_use(
        self,
        card: CardRef,
        *,
        source: int,
        targets: tuple[int, ...],
        use_timing: Timing | None = None,
    ) -> Frame: ...

    def recover_from_peach(
        self, frame: Frame, action: UseCard, *, asker: int, dying: int
    ) -> None: ...

    def apply_effects(
        self, frame: Frame, effects: Sequence[EffectStep], *, timing: Timing
    ) -> Any: ...

    def apply_hp_change(self, frame: Frame, seat: int, value: int) -> Any: ...


def _emit(
    host: FrameHost,
    frame: Frame,
    timing: Timing,
    *,
    actor: int | None = None,
    cards: Sequence[CardRef] = (),
    targets: tuple[int, ...] | None = None,
) -> None:
    """以 ``frame`` 为主体 dispatch 一个时机；``targets`` 缺省取帧的目标列表。

    C 段的时机记录按票 11 §3.3 取**当前目标**（``targets[cursor]``），故调用点显式给
    ``targets``；空目标的响应牌（【闪】/【无懈】）沿用空元组，与 M1 记录流一字不差。
    """
    host.emit_frame_timing(frame, timing, actor=actor, cards=cards, targets=targets)


def _current_target(host: FrameHost, frame: Frame) -> int:
    """C 段的**当前目标**：``targets[cursor]``，空目标时退回使用者（票 11 §3.3）。"""
    return host.current_target(frame)


def _resolve_emit(host: FrameHost, frame: Frame, timing: Timing) -> None:
    """C 段时机的统一发射口：actor = 当前目标、``targets`` = 当前目标（空目标时空元组）。"""
    targets = tuple(frame.data.get("targets", ()))
    current = _current_target(host, frame)
    _emit(
        host,
        frame,
        timing,
        actor=current,
        targets=(current,) if targets else (),
    )


# ---- USE 帧：L4 六个时机 + 压入 RESOLVE + L5.6 ----------------------------


def use_choose_target(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.USE_CHOOSE_TARGET, actor=frame.data["source"])
    # 实体牌离开手牌、进入处理区：发生在 L4.1 与 L4.2 之间（票 07 §3）。
    host.move_used_card_to_processing(frame)


def use_on_use(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.USE_ON_USE, actor=frame.data["source"])


def use_designate_target(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.USE_DESIGNATE_TARGET, actor=frame.data["source"])


def use_become_target(host: FrameHost, frame: Frame) -> None:
    """"成为目标时"：**时机外层、目标内层**（`A/flow_use.txt:46`）。

    多目标时逐个目标各发一条；空目标的响应牌（【闪】/【无懈】）沿用 M1 的
    ``actor = None`` 单条记录，故 1v1 六条基准逐字节不变。
    """
    targets = tuple(frame.data["targets"])
    if not targets:
        _emit(host, frame, Timing.USE_BECOME_TARGET, actor=None)
        return
    for target in targets:
        _emit(host, frame, Timing.USE_BECOME_TARGET, actor=target)


def use_after_designate_target(host: FrameHost, frame: Frame) -> Control:
    """"指定目标后"：目标已定、不再改变（`A/flow_use.txt:39`）。

    票 27 在这里跑本牌的 ``timing_ops``（【五谷丰登】的"亮出 X 张"）；若那次动作没
    完整执行（牌不够），**终止这次使用结算**、直奔"使用结算结束后"（不判平局）。
    时机记录只在首次进入时发一次——``timing_ops`` 将来若中途提问，重入不会重复记录。
    """
    if not frame.data.get("after_designate_emitted"):
        _emit(host, frame, Timing.USE_AFTER_DESIGNATE_TARGET, actor=frame.data["source"])
        frame.data["after_designate_emitted"] = True
    control = host.apply_timing_ops(frame, Timing.USE_AFTER_DESIGNATE_TARGET)
    if control is not None:
        return control
    if frame.data.get("use_terminated"):
        return Goto(USE_AFTER_END_PC)
    return None


def use_after_become_target(host: FrameHost, frame: Frame) -> None:
    """"成为目标后"：同 B4，时机外层、目标内层；空目标沿用单条 ``actor = None``。"""
    targets = tuple(frame.data["targets"])
    if not targets:
        _emit(host, frame, Timing.USE_AFTER_BECOME_TARGET, actor=None)
        return
    for target in targets:
        _emit(host, frame, Timing.USE_AFTER_BECOME_TARGET, actor=target)


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
    发 ``RESOLVE_AFTER_END``——那一步归判定阶段循环（票 10 §3.6）。票 27 在这里补跑
    本牌的 ``timing_ops``（【五谷丰登】把亮牌中剩余的牌置入弃牌堆）。
    """
    if behavior_for(frame.data["card"]).is_delayed:
        host.move_used_card_to_judge_zone(frame)
        return None
    if not frame.data.get("after_end_done"):
        host.move_processing_card_to_discard(frame)
        _emit(
            host,
            frame,
            Timing.RESOLVE_AFTER_END,
            actor=frame.data["source"],
            cards=(frame.data["card"],),
        )
        frame.data["after_end_done"] = True
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

    C1 的 ``actor`` 保持 M1 的**使用者**（C3–C5 才是当前目标），故 1v1 基准逐字节不变。
    """
    targets = tuple(frame.data["targets"])
    current = _current_target(host, frame)
    _emit(
        host,
        frame,
        Timing.RESOLVE_START,
        actor=frame.data["source"],
        targets=(current,) if targets else (),
    )
    if targets and not target_is_effective(
        host.state, frame.data["card"], frame.data["source"], current
    ):
        return Goto(CURSOR_PC)
    return None


def resolve_before_effect(host: FrameHost, frame: Frame) -> Control:
    targets = tuple(frame.data["targets"])
    if frame.data.get("use_timing") is Timing.NEAR_DEATH_AT:
        # 濒死求桃建立的使用：回复结算已由求桃窗口显式压好（票 21），故跳过
        # "生效时 / 生效后"（`Goto` 而不是 `None`——`None` 只表示"这一步做完了"，
        # 会顺着 pc 走进效果步骤，回复就被写第二遍）。
        _emit(host, frame, Timing.RESOLVE_BEFORE_EFFECT, actor=frame.data["source"])
        frame.data["canceled"] = False
        return Goto(RESOLVE_END_PC)
    if "canceled" in frame.data:
        # 本目标的响应窗口已经处理过：**按 canceled 标志**决定去向（票 07 §3）。
        return Goto(RESOLVE_END_PC) if frame.data["canceled"] else None
    behavior = behavior_for(frame.data["card"])
    if not targets or not opens_response_window(behavior):
        _emit(host, frame, Timing.RESOLVE_BEFORE_EFFECT, actor=frame.data["source"])
        frame.data["canceled"] = False
        return None
    target = _current_target(host, frame)
    if not frame.data.get("window_open"):
        _emit(host, frame, Timing.RESOLVE_BEFORE_EFFECT, actor=target)
        frame.data["window_open"] = True
    action = host.ask_query(QueryKind.DODGE, target, Timing.RESOLVE_BEFORE_EFFECT)
    if host.suspended(action):
        return "stay"
    frame.data["canceled"] = isinstance(action, UseCard)
    if frame.data["canceled"]:
        # 抵消是"被响应帧"的标志；【闪】自己作为一次**使用**照走 L4 / L5（票 07 §3）。
        host.start_use(action.card, source=target, targets=())
    # 重入本步：等【闪】的 USE 帧出栈后，由上一条 ``canceled`` 分支决定去向。
    return "stay"


def resolve_effect(host: FrameHost, frame: Frame) -> None:
    _resolve_emit(host, frame, Timing.RESOLVE_EFFECT)


def resolve_after_effect(host: FrameHost, frame: Frame) -> Control:
    """**生效后**：执行本牌的效果列表（票 27 起按帧内游标重入）。

    ``after_effect_emitted`` 保证时机只记一次；``effect_pc`` 由引擎的效果执行器维护，
    使"提问让出控制权"与"压子帧"都能从这里原地恢复（票 19 §8）。
    """
    if not frame.data.get("after_effect_emitted"):
        _resolve_emit(host, frame, Timing.RESOLVE_AFTER_EFFECT)
        frame.data["after_effect_emitted"] = True
    return host.apply_effects(
        frame, behavior_for(frame.data["card"]).effects, timing=Timing.RESOLVE_AFTER_EFFECT
    )


def resolve_end(host: FrameHost, frame: Frame) -> None:
    _resolve_emit(host, frame, Timing.RESOLVE_END)


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
    "op",
)
"""**逐目标分槽**的帧私有键：游标推进时必须整体清掉（票 11 §3.2）。

``canceled`` / ``window_open`` / ``after_effect_emitted`` / ``effect_pc`` 在 :mod:`steps`
维护，``asks``（选择面记忆）在 :class:`~sgs.engine.Engine` 维护、``op`` 在效果回调里维护；
集中列在这里，使"新加一个按目标分槽的键"只需改这一处。"""


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

CURSOR_PC = len(RESOLVE_STEPS) - 1
"""非 dispatch 的游标步骤：无效目标从这里直接推进，跳过 C2–C5。"""

USE_AFTER_END_PC = _pc_of(USE_STEPS, Timing.RESOLVE_AFTER_END)
"""USE 帧的"使用结算结束后"：目标减为 0 / 执行动作未完成时直接跳到这里。"""


# ---- DAMAGE 帧：L7 五个时机 + 在"受到伤害时"后插入 LIFE_DECREASE -----------


def _damage_channel(frame: Frame) -> tuple[CardRef, ...]:
    """官方六要素里的**渠道**：造成这次伤害的实体牌；无牌来源时为空元组。"""
    channel = frame.data.get("channel")
    return (channel,) if channel is not None else ()


def _emit_damage(host: FrameHost, frame: Frame, timing: Timing, actor: int) -> None:
    # 渠道牌进时机记录，使"谁用什么牌造成/受到了这次伤害"在记录流里可读。
    _emit(host, frame, timing, actor=actor, cards=_damage_channel(frame))


def damage_deal(host: FrameHost, frame: Frame) -> None:
    _emit_damage(host, frame, Timing.DAMAGE_DEAL, frame.data["source"])


def damage_take(host: FrameHost, frame: Frame) -> None:
    _emit_damage(host, frame, Timing.DAMAGE_TAKE, frame.data["target"])


def damage_push_life_decrease(host: FrameHost, frame: Frame) -> None:
    """M1 无"防止伤害"（票 07 §1），故"受到伤害时"之后必定进入扣减体力结算。"""
    host.push_frame(
        FrameKind.LIFE_DECREASE,
        {
            "target": frame.data["target"],
            "source": frame.data["source"],
            "amount": frame.data["amount"],
        },
        timing=Timing.LIFE_DECREASE_BEFORE,
    )


def damage_after_deal(host: FrameHost, frame: Frame) -> None:
    _emit_damage(host, frame, Timing.DAMAGE_AFTER_DEAL, frame.data["source"])


def damage_after_take(host: FrameHost, frame: Frame) -> None:
    _emit_damage(host, frame, Timing.DAMAGE_AFTER_TAKE, frame.data["target"])


def damage_end(host: FrameHost, frame: Frame) -> None:
    _emit_damage(host, frame, Timing.DAMAGE_END, frame.data["target"])


DAMAGE_STEPS = (
    Step(damage_deal, Timing.DAMAGE_DEAL),
    Step(damage_take, Timing.DAMAGE_TAKE),
    # 压栈步骤的 `timing` 只作子帧 `return_to` 的可读标签：LIFE_DECREASE_BEFORE
    # 由 LIFE_DECREASE 帧自己 dispatch。
    Step(damage_push_life_decrease, Timing.LIFE_DECREASE_BEFORE),
    Step(damage_after_deal, Timing.DAMAGE_AFTER_DEAL),
    Step(damage_after_take, Timing.DAMAGE_AFTER_TAKE),
    Step(damage_end, Timing.DAMAGE_END),
)


# ---- LIFE_DECREASE 帧：L8 三个时机 + 在"扣减体力时"插入濒死 ------------------


def life_decrease_before(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.LIFE_DECREASE_BEFORE, actor=frame.data["target"])


def life_decrease_on(host: FrameHost, frame: Frame) -> None:
    """**扣减体力时** = 唯一的体力写入点；先 dispatch 时机，再把体力扣到目标值。

    目标值由本帧算（``hp - amount``），原语只负责写（票 07 §1）。结算完**立刻**把
    结果记进帧：濒死与否是这次扣减的结果，"扣减体力时"之后据此决定是否压入濒死帧
    （票 21）；无人救时死亡结算再从濒死帧压入（票 22）。
    """
    target = frame.data["target"]
    _emit(host, frame, Timing.LIFE_DECREASE_ON, actor=target)
    host.apply_hp_change(frame, target, host.state.player(target).hp - frame.data["amount"])
    frame.data["dying"] = host.state.player(target).hp <= 0


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


def life_decrease_after(host: FrameHost, frame: Frame) -> None:
    """**扣减体力后**：照常空实现收尾。

    无人救的死亡路径上这一步**不会被走到**——濒死帧在"濒死结算结束后"直接压入 DEATH
    子帧并留在栈上，整条伤害链（含本帧）由终局清栈统一丢弃（票 22）；救回时才回到这里。
    """
    _emit(host, frame, Timing.LIFE_DECREASE_AFTER, actor=frame.data["target"])


LIFE_DECREASE_STEPS = (
    Step(life_decrease_before, Timing.LIFE_DECREASE_BEFORE),
    Step(life_decrease_on, Timing.LIFE_DECREASE_ON),
    # 压栈步骤的 `timing` 只作子帧 `return_to` 的可读标签：NEAR_DEATH_ENTER
    # 由 NEAR_DEATH 帧自己 dispatch。
    Step(life_decrease_trigger_near_death, Timing.NEAR_DEATH_ENTER),
    Step(life_decrease_after, Timing.LIFE_DECREASE_AFTER),
)


# ---- NEAR_DEATH 帧：L12 三个时机 + 官方求桃顺序 -----------------------------
#
# 应答者区间是票 05 §7 的"**从当前回合角色起、逆时针、每人一次**"（官方 L18.2，
# ``flow/neardeath.txt``）；L18.8 的起点切换与 L12.2 的"新的濒死状态时"回退收窄
# 按票 05 §7 / 票 13 §5 为"结构存在、M1 不可达"，故此处只落一个实例，不建第二套调度。


def _emit_near_death(host: FrameHost, frame: Frame, timing: Timing) -> None:
    _emit(host, frame, timing, actor=frame.data["target"])


def near_death_enter(host: FrameHost, frame: Frame) -> None:
    _emit_near_death(host, frame, Timing.NEAR_DEATH_ENTER)


def near_death_at(host: FrameHost, frame: Frame) -> None:
    """"处于濒死状态时"：打开求桃窗口，按官方顺序定下应答者区间。

    区间在窗口打开时**定格**（``askers`` 进帧私有载荷）而不是每轮重算，使"当前回合
    角色起逆时针"在整段濒死结算里只有一个真源；"新的濒死状态时"的回退收窄
    （``flow/neardeath.txt`` 第 13 行）M1 不可达（票 05 §7），故不建重入分支。
    """
    target = frame.data["target"]
    _emit_near_death(host, frame, Timing.NEAR_DEATH_AT)
    frame.data.setdefault(
        "askers", _askers_from(host.state, host.state.turn_seat, len(host.state.players))
    )
    frame.data.setdefault("next", 0)


def near_death_ask(host: FrameHost, frame: Frame) -> Control:
    """逐个应答者求桃，直到有人把体力回复到 1 点以上、或全部询问完毕。

    "一个询问一个应答者"（票 05 §7）：每轮只发一个 ``PEACH`` 询问，``next`` 记进度，
    故暂停、恢复、拒绝都落在同一个槽位上。体力恢复到 1 点以上就**立即**脱离濒死
    （官方 4.17 第 4 条"每回复 1 点体力即检查"），不再问剩下的人。
    """
    target = frame.data["target"]
    if host.state.player(target).hp >= 1:
        return None
    while frame.data["next"] < len(frame.data["askers"]):
        asker = frame.data["askers"][frame.data["next"]]
        action = host.ask_query(QueryKind.PEACH, asker, Timing.NEAR_DEATH_AT, dying=target)
        if host.suspended(action):
            return "stay"
        frame.data["next"] += 1
        if isinstance(action, UseCard):
            host.recover_from_peach(frame, action, asker=asker, dying=target)
            return "stay"  # 回复帧出栈后重入本步：先看体力是否已恢复到 1 点以上
    return None


def near_death_after(host: FrameHost, frame: Frame) -> None:
    """"濒死结算结束后"：抢救结果直接读体力（官方 4.17 第 4 条）。

    本时机是**空实现**（官方此处只列技能效果，M1 无技能）：流程经过并照记，不产生状态
    变化（票 13 的三分类：dispatch 但无效果）。体力仍 ≤ 0 就是**死亡入口**：在此压入
    DEATH 子帧（票 22）。死亡结算挂在濒死帧**之上**，濒死帧因此留在栈上不弹出——
    它连同整条伤害链在终局清栈时逐帧记 `discard`（票 04 §5 的 5 帧）。
    """
    _emit_near_death(host, frame, Timing.NEAR_DEATH_AFTER)
    target = frame.data["target"]
    if host.state.player(target).hp <= 0:
        host.enter_death(frame, target)


NEAR_DEATH_STEPS = (
    Step(near_death_enter, Timing.NEAR_DEATH_ENTER),
    Step(near_death_at, Timing.NEAR_DEATH_AT),
    Step(near_death_ask, Timing.NEAR_DEATH_AT),
    Step(near_death_after, Timing.NEAR_DEATH_AFTER),
)


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


def _emit_death(host: FrameHost, frame: Frame, timing: Timing) -> None:
    _emit(host, frame, timing, actor=frame.data["target"])


def death_before_start(host: FrameHost, frame: Frame) -> None:
    """死亡结算开始前：①主公死亡不做回合结束；②否则定格 killer、自己的回合则回合结束。

    身份局才做这两件事（票 14 §4，官方 `flow_death` 首段）；无身份局保持 M1 的空实现。
    ``killer`` 只在**有来源的伤害且来源 ≠ 死者**时定格：失去体力 / 无来源伤害（闪电）/
    自杀 ⇒ 未定格 ⇒ 无奖惩。来源由死亡帧的 ``damage_source`` 带下来（帧私有载荷，
    不进 ``timing`` 记录的 ``source``，故记录面不变）。
    """
    _emit_death(host, frame, Timing.DEATH_BEFORE_START)
    if host.state.rule_config.mode is not Mode.IDENTITY:
        return
    seat = frame.data["target"]
    if host.state.player(seat).identity is Identity.LORD:
        # 官方"死亡结算开始前：若死亡的是主公，则游戏结束"——回合结束分支让位（票 14 §4）。
        return
    source = frame.data.get("damage_source")
    if source is not None and source != seat:
        frame.data["killer"] = source
    if host.state.turn_seat == seat:
        _emit(host, frame, Timing.TURN_END, actor=seat)


def death_reveal_identity_before(host: FrameHost, frame: Frame) -> None:
    """"亮出身份牌前"：先 dispatch 时机，再发一条 ``identity_reveal``（票 14 §5）。

    只进身份局的步骤表；主公也照发（开局已亮明，再发一次只是形状统一）。
    """
    _emit_death(host, frame, Timing.DEATH_REVEAL_IDENTITY_BEFORE)
    host.reveal_dead_identity(frame)


def death_reveal_identity_after(host: FrameHost, frame: Frame) -> None:
    """胜负检测点：**先照记时机与死亡事实，再调唯一终局入口**。

    顺序使 ``check_seq`` 恰为这条 ``DEATH_REVEAL_IDENTITY_AFTER`` 记录的序号
    （票 08 §6 的"检测点那条记录"口径，票 22 第 6 条）。检测只置 ``pending_outcome``，
    不清栈、不终止——死亡结算剩余两段照跑（票 13 §3）。
    """
    _emit_death(host, frame, Timing.DEATH_REVEAL_IDENTITY_AFTER)
    host.record_death_fact(frame)
    host.check_game_end()


def death_on(host: FrameHost, frame: Frame) -> None:
    """死亡时：先 dispatch（M2 无死亡时技能），再 Ⅰ.a 系统处理牌、最后 Ⅱ 奖惩。"""
    _emit_death(host, frame, Timing.DEATH_ON)
    host.discard_dead_cards(frame)
    host.apply_death_rewards(frame)


def death_after(host: FrameHost, frame: Frame) -> None:
    """空实现：死亡结算收尾空时点（票 13 §4 的 C 类）。"""
    _emit_death(host, frame, Timing.DEATH_AFTER)


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
#   2. ``judge_before_effect``：改判窗口，真 dispatch；M2 无改判技能 ⇒ 队列恒空、无询问；
#   3. ``judge_after_effect``：结果（花色 / 点数 / 牌名）由后果 op 直接读帧私有 ``judge_card``；
#   4. ``judge_finish``：判定结算结束 ⇒ 判定牌 ``PROCESSING→DISCARD``，随后调后果 op。
#
# 帧私有 ``data`` 把 **``subject``（被判定者）** 与 **``judge_card``（判定牌）** 与
# ``card``（渠道牌）**分列**（票 10 §1）：三个判定时机的记录一律 ``actor = subject``，
# ``cards = (judge_card,)``，故【闪电】伤害的渠道牌永远不会错成判定牌。


def _judge_actor(frame: Frame) -> int:
    return frame.data["subject"]


def _judge_cards(frame: Frame) -> tuple[CardRef, ...]:
    judge_card = frame.data.get("judge_card")
    return (judge_card,) if judge_card is not None else ()


def _emit_judge(host: FrameHost, frame: Frame, timing: Timing) -> None:
    _emit(host, frame, timing, actor=_judge_actor(frame), cards=_judge_cards(frame))


def judge_on(host: FrameHost, frame: Frame) -> None:
    """取判定牌 → 入处理区 → **再** dispatch ``JUDGE_ON``（票 10 §1 步骤 1）。

    短摸（两堆皆空）⇒ 引擎已判 ``both_decks_empty`` 平局并终止；本步不再落牌、不再 dispatch。
    """
    if not host.draw_judge_card(frame):
        return
    host.move_judge_card_to_processing(frame)
    _emit_judge(host, frame, Timing.JUDGE_ON)


def judge_before_effect(host: FrameHost, frame: Frame) -> None:
    """改判窗口：**真 dispatch = 空实现**（保留：dispatch、无询问、无状态变化）。

    不是跳过——跳过会逼 M3 加【鬼才】类技能时改判定骨架（票 03 §4）。响应队列按
    "当前回合角色起逆时针、每角色一次"建好；M2 有能力者集合为空 ⇒ 队列恒空 ⇒ 不构造
    任何询问（票 10 §4）。真出现有能力者时，改判询问协议（票 19 的 ``RESPOND``）尚未建立，
    故 fail-closed 而不是静默丢弃。
    """
    _emit_judge(host, frame, Timing.JUDGE_CARD_BEFORE_EFFECT)
    if rejudge_askers(host.state, rejudge_capable_seats()):
        raise NotImplementedError("M2 没有改判技能：改判询问协议（票 19 的 RESPOND）属 M3")


def judge_after_effect(host: FrameHost, frame: Frame) -> None:
    """判定牌生效后：结果已是帧私有 ``judge_card`` 的牌面，后果 op 在 ``judge_finish`` 读它。"""
    _emit_judge(host, frame, Timing.JUDGE_CARD_AFTER_EFFECT)


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


def recover_value(host: FrameHost, frame: Frame) -> None:
    """**确定回复数值**：先跑时机，再把体力写高（夹在体力上限内）。

    官方 4.13："回复体力"是数值变化四类事件之一，回复值受**体力上限**限制；
    目标值由本帧算，原语只负责写（票 07 §1）。
    """
    target = frame.data["targets"][0]
    _emit(host, frame, Timing.RECOVER_VALUE, actor=target)
    player = host.state.player(target)
    host.apply_hp_change(frame, target, min(player.hp + frame.data["amount"], player.hp_max))


def recover_after(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.RECOVER_AFTER, actor=frame.data["targets"][0])


RECOVER_STEPS = (
    Step(recover_value, Timing.RECOVER_VALUE),
    Step(recover_after, Timing.RECOVER_AFTER),
)


# ---- PLAY 帧：L6 两个时机（票 19 §2：打出必须独立）---------------------------


def play_declare(host: FrameHost, frame: Frame) -> None:
    """``PLAY_DECLARE``：打出牌进入处理区（与使用的第一段移动同姿态）。"""
    _emit(
        host,
        frame,
        Timing.PLAY_DECLARE,
        actor=frame.data["source"],
        cards=(frame.data["card"],),
    )
    host.move_played_card_to_processing(frame)


def play_on_play(host: FrameHost, frame: Frame) -> None:
    """``PLAY_ON_PLAY``：打出的实体牌随即进弃牌堆（打出无使用结算）。"""
    _emit(
        host,
        frame,
        Timing.PLAY_ON_PLAY,
        actor=frame.data["source"],
        cards=(frame.data["card"],),
    )
    host.move_played_card_to_discard(frame)


PLAY_STEPS = (
    Step(play_declare, Timing.PLAY_DECLARE),
    Step(play_on_play, Timing.PLAY_ON_PLAY),
)
"""打出事件的步骤表：两个时机构成 L6 的全部节点（票 01 的 6 个转 dispatch 之一对）。"""


def build_steps(mode: Mode = Mode.NO_IDENTITY) -> StepTable:
    """按模式装配步骤表：只有 DEATH 帧的形状分叉（票 25）。"""
    return {
        FrameKind.USE: USE_STEPS,
        FrameKind.RESOLVE: RESOLVE_STEPS,
        FrameKind.PLAY: PLAY_STEPS,
        FrameKind.DAMAGE: DAMAGE_STEPS,
        FrameKind.LIFE_DECREASE: LIFE_DECREASE_STEPS,
        FrameKind.NEAR_DEATH: NEAR_DEATH_STEPS,
        FrameKind.DEATH: IDENTITY_DEATH_STEPS if mode is Mode.IDENTITY else DEATH_STEPS,
        FrameKind.RECOVER: RECOVER_STEPS,
        FrameKind.JUDGE: JUDGE_STEPS,
    }
