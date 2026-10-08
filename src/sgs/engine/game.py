"""引擎门面：一局的回合环、询问交付与终局。

**低层入口**接受人为指定的局面（:meth:`Engine.from_state`）；:func:`new_game` 是它的
便利包装——实例化牌堆（``card_id = 0..n-1`` 按数据表顺序）→ 洗牌 → 按座序发牌。

引擎只在**询问**处让出控制权（票 04 §3）：``run`` 循环要么推进结算栈 / 回合环一步，
要么把当前询问交给该座位的参与者。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from sgs.agents import Participant
from sgs.data import ContentPack, RuleConfig
from sgs.effects import (
    CallOp,
    DamageNature,
    EffectContext,
    EffectStep,
    PushFrame,
    apply_hp,
    discard_count,
    draw_from_deck,
    legal_actions_for,
    move_card,
)
from sgs.resolution import Frame, ResolutionStack, StepTable
from sgs.state import (
    Action,
    CardRef,
    DiscardQuery,
    DodgeQuery,
    FrameKind,
    GameState,
    HpChangeDelta,
    MoveReason,
    PeachQuery,
    Phase,
    PlayPhaseQuery,
    Player,
    Query,
    QueryKind,
    Zone,
    project,
)
from sgs.timing import Timing

from .errors import IllegalActionError
from .journal import (
    ActionRecord,
    DeckShuffleRecord,
    FrameRecord,
    GameEndRecord,
    Header,
    JournalRecord,
    QueryRecord,
    TimingRecord,
    dump_journal,
    wrap_delta,
)
from .rng import RandomSource
from .steps import build_steps

__all__ = ["DRAW_COUNT", "INITIAL_HP", "Engine", "Outcome", "instantiate_cards", "new_game"]

INITIAL_HP = 4
"""M1 的体力上限恒 4（无武将、无体力上限变化）。"""

DRAW_COUNT = 2
"""摸牌阶段的额定摸牌数；M1 无修正摸牌数的效果。"""


class _Suspend:
    """``ask`` 的哨兵：询问已发出，等参与者应答。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试可读性
        return "SUSPEND"


SUSPEND = _Suspend()


class Outcome(BaseModel):
    """终局判定结果；由唯一的 :meth:`Engine.check_game_end` 产出。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: Literal["winner", "draw"]
    winner: int | None = None
    reason: str


def _child_payload(
    kind: FrameKind,
    parent_data: dict[str, Any],
    scalars: Sequence[tuple[str, Any]],
) -> dict[str, Any]:
    """子帧载荷 = 从使用帧继承 ``source`` / ``targets`` / ``card`` / ``use_timing``
    + 行为表的标量字面量。

    ``use_timing`` 一路带着走，好让"这次使用是从哪个窗口开的"在后面每一层都读得到
    （濒死求桃开的【桃】用它把自己的回复结算与行为表的效果帧区分开，票 21）。
    伤害帧再规范化成官方**六要素**（票 07 §6）：``target`` = 唯一目标、
    ``channel`` = 使用的实体牌、``nature`` 恒 ``NORMAL``、``chain`` 恒 ``False``。
    """
    data: dict[str, Any] = {
        "source": parent_data.get("source"),
        "targets": tuple(parent_data.get("targets", ())),
        "card": parent_data.get("card"),
        "use_timing": parent_data.get("use_timing"),
    }
    for key, value in scalars:
        if key in data:
            raise ValueError(f"行为表的标量 {key!r} 会覆盖子帧继承的字段")
        data[key] = value
    if kind is FrameKind.DAMAGE:
        targets = data["targets"]
        data["target"] = targets[0] if targets else None
        data["channel"] = data["card"]
        data["nature"] = DamageNature.NORMAL
        data["chain"] = False
    return data


class Engine:
    """一局的驱动器：显式结算栈 + 回合环 + 记录流。"""

    def __init__(
        self,
        state: GameState,
        *,
        seed: int,
        init_records: Sequence[JournalRecord] = (),
    ) -> None:
        self.state = state
        self._records: list[JournalRecord] = [
            Header(
                seed=seed,
                pack_id=state.pack_id,
                deck_profile=state.deck_profile,
                rule_config=state.rule_config,
            )
        ]
        self._records.extend(init_records)
        self._seq = len(init_records)
        self._last_seq = len(init_records)

        self.stack = ResolutionStack()
        self.steps: StepTable = build_steps()
        """帧类型 → 步骤表：使用 / 结算的步骤在 :mod:`sgs.engine.steps`。"""

        self.pending: Query | None = None
        self.pending_outcome: Outcome | None = None
        self.finished = False
        self.death_entry: int | None = None
        """已走到**死亡入口**的座位；``None`` = 不在死亡路径上（票 21，死亡结算归票 22）。"""

        self._answer: Action | None = None
        self._started = False
        self._frame_seq = 0
        self._check_seq: int | None = None
        self._turn_pc = 0
        self._turn_data: dict[str, Any] = {}
        self._turn_steps: tuple[Callable[[], Any], ...] = (
            self._turn_before_start,
            self._turn_start,
            self._phase_prepare,
            self._phase_judge,
            self._phase_draw_start,
            self._phase_draw,
            self._phase_draw_end,
            self._phase_play_start,
            self._phase_play_idle,
            self._phase_play_end,
            self._phase_discard_start,
            self._phase_discard,
            self._phase_discard_end,
            self._phase_finish,
            self._turn_end,
            self._turn_after_end,
        )

    # -- 便利包装 -----------------------------------------------------------

    @classmethod
    def new_game(cls, *, seed: int, rule_config: RuleConfig, pack: ContentPack) -> "Engine":
        """造初始状态（实例化 → 洗牌 → 发牌）并带上开局那几条记录。"""
        state, init_records = build_initial(seed=seed, rule_config=rule_config, pack=pack)
        return cls(state, seed=seed, init_records=init_records)

    @classmethod
    def from_state(cls, state: GameState, *, seed: int) -> "Engine":
        """低层入口：从**人为指定的局面**起跑（只写 header，不虚构开局记录）。"""
        return cls(state, seed=seed)

    # -- 记录流 -------------------------------------------------------------

    @property
    def records(self) -> tuple[JournalRecord, ...]:
        return tuple(self._records)

    def journal(self) -> str:
        """canonical JSONL（唯一序列化器的入口）。"""
        return dump_journal(self._records)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _emit(self, record: JournalRecord) -> None:
        self._records.append(record)
        seq = getattr(record, "seq", None)
        if seq is not None:
            self._last_seq = seq

    def emit_timing(
        self,
        timing: Timing,
        *,
        actor: int | None = None,
        source: int | None = None,
        targets: tuple[int, ...] = (),
        cards: tuple[CardRef, ...] = (),
        frame_id: int = 0,
        depth: int = 0,
    ) -> None:
        """dispatch 一个时机；空实现也照记。"""
        self._emit(
            TimingRecord(
                seq=self._next_seq(),
                timing=timing,
                frame_id=frame_id,
                depth=depth,
                actor=actor,
                source=source,
                targets=targets,
                cards=cards,
            )
        )

    def emit_delta(self, delta: Any, *, frame_id: int = 0, depth: int = 0) -> None:
        self._emit(wrap_delta(delta, seq=self._next_seq(), frame_id=frame_id, depth=depth))

    def _emit_frame(self, op: str, frame: Frame) -> None:
        self._emit(
            FrameRecord(
                seq=self._next_seq(),
                op=op,
                frame_id=frame.frame_id,
                frame_kind=frame.kind,
                depth=self.stack.depth,
                return_to=frame.return_to,
            )
        )

    # -- 结算栈 -------------------------------------------------------------

    def push_frame(self, kind: FrameKind, data: dict[str, Any], *, timing: Timing) -> Frame:
        """压入一个子帧；``return_to`` 命名式记录"从哪回来"（父帧 kind + 时机 id）。"""
        parent = self.stack.top if self.stack else None
        return_to = (parent.kind.value, timing.value) if parent is not None else None
        self._frame_seq += 1
        frame = Frame(kind=kind, data=data, return_to=return_to, frame_id=self._frame_seq)
        self.stack.push(frame)
        self._emit_frame("push", frame)
        return frame

    def pop_frame(self) -> Frame:
        frame = self.stack.pop()
        self._emit_frame("pop", frame)
        return frame

    # -- 结算帧步骤可用的操作面（步骤表在 steps.py，只经这些方法改状态）---------

    def emit_frame_timing(
        self, frame: Frame, timing: Timing, *, actor: int | None = None, cards=()
    ) -> None:
        """以 ``frame`` 为主体 dispatch 一个时机；``source`` / ``targets`` 随帧继承。"""
        self.emit_timing(
            timing,
            frame_id=frame.frame_id,
            depth=self.stack.depth,
            actor=actor,
            source=frame.data.get("source"),
            targets=tuple(frame.data.get("targets", ())),
            cards=tuple(cards),
        )

    def suspended(self, value: Any) -> bool:
        """``ask`` 的返回值是否为"已暂停等应答"哨兵。"""
        return value is SUSPEND

    def ask_query(
        self,
        kind: QueryKind,
        actor: int,
        timing: Timing,
        *,
        dying: int | None = None,
    ) -> Any:
        """构造并交付一次询问；已有应答（重入）则直接返回它。"""
        return self.ask(lambda seq: self._build_query(kind, actor, timing, seq, dying=dying))

    def start_use(
        self,
        card: CardRef,
        *,
        source: int,
        targets: tuple[int, ...],
        use_timing: Timing | None = None,
    ) -> Frame:
        """开始一次使用：记一次本回合已用、压入 USE 帧（票 07 §6）。

        ``use_timing`` 覆盖压栈步骤的 ``return_to`` 标签：默认是出牌阶段的
        ``USE_CHOOSE_TARGET``，濒死求桃里出【桃】时是 ``NEAR_DEATH_AT``
        （票 04 §4 的"从哪回来"要读得出是哪个窗口开的使用）。
        """
        counts = self.state.player(source).card_use_counts
        counts[card.type] = counts.get(card.type, 0) + 1
        return self.push_frame(
            FrameKind.USE,
            {
                "card": card,
                "source": source,
                "targets": tuple(targets),
                "use_timing": use_timing,
            },
            timing=use_timing if use_timing is not None else Timing.USE_CHOOSE_TARGET,
        )

    def recover_from_peach(
        self, frame: Frame, action: UseCard, *, asker: int, dying: int
    ) -> None:
        """有人在濒死窗口出【桃】：把**回复结算插在濒死之下**，再走【桃】自己的使用链。

        官方 4.17："每回复 1 点体力即检查"。所以回复（内含体力写入）必须先于
        "【桃】的使用结算结束"发生——于是先压 ``RECOVER`` 帧、再把这次使用挂在它上面
        （票 21 第 3 条"回复结算插入在濒死之下，完成后回到被中断的原结算继续"）；
        反之若先走使用链、出栈后再写体力，濒死判定就会读到过期的体力。

        使用帧里的【桃】**不再**走"生效后"的效果列表：这次回复由本方法显式建立
        （否则行为表会再压一个回复帧，同一次回复被写两遍）。压栈那一步的
        ``return_to`` 标签是 ``NEAR_DEATH_AT``，记录流里因此读得出"这次使用是求桃窗口开的"，
        效果层也据此辨认出"这张牌的效果已在此落地"。
        """
        self.push_frame(
            FrameKind.RECOVER,
            {
                "source": asker,
                "targets": (dying,),
                "card": action.card,
                "amount": 1,
            },
            timing=Timing.RECOVER_VALUE,
        )
        self.start_use(
            action.card,
            source=asker,
            targets=(dying,),
            use_timing=Timing.NEAR_DEATH_AT,
        )

    def move_used_card_to_processing(self, frame: Frame) -> None:
        """手牌 → 处理区（``reason = USE``）：在 L4.1 / L4.2 之间（票 07 §3）。"""
        self._move_in_frame(
            frame, (frame.data["card"],), to_zone=Zone.PROCESSING, reason=MoveReason.USE
        )

    def move_processing_card_to_discard(self, frame: Frame) -> None:
        """处理区 → 弃牌堆（``reason = USE``）：L5.6"使用结算结束后"（票 07 §3）。"""
        self._move_in_frame(
            frame, (frame.data["card"],), to_zone=Zone.DISCARD, reason=MoveReason.USE
        )

    def _effect_context(self, frame: Frame, *, actor: int) -> EffectContext:
        """叶子原语要的结算主体信息：从当前帧继承 `source` / `targets` / `card` 与层级。

        ``actor`` 是这次原语调用的主体（移动牌的操使者 / 体力被写的座位），由调用点给。
        """
        return EffectContext(
            actor=actor,
            source=frame.data.get("source"),
            targets=tuple(frame.data.get("targets", ())),
            card=frame.data.get("card"),
            frame_id=frame.frame_id,
            depth=self.stack.depth,
        )

    def _move_in_frame(
        self,
        frame: Frame,
        cards: tuple[CardRef, ...],
        *,
        to_zone: Zone,
        reason: MoveReason,
    ) -> None:
        ctx = self._effect_context(frame, actor=frame.data["source"])
        delta = move_card(self.state, ctx, tuple(cards), to_zone=to_zone, reason=reason)
        self.emit_delta(delta, frame_id=frame.frame_id, depth=self.stack.depth)

    def apply_effects(
        self, frame: Frame, effects: Sequence[EffectStep], *, timing: Timing
    ) -> None:
        """解释行为表的步骤列表：``PushFrame`` 压子帧、``CallOp`` 调叶子原语（M1 未登记）。"""
        for step in effects:
            if isinstance(step, PushFrame):
                data = _child_payload(step.kind, frame.data, step.scalars)
                self.push_frame(step.kind, data, timing=timing)
                continue
            assert isinstance(step, CallOp)  # union 只有两类步骤
            raise NotImplementedError(
                f"M1 没有登记叶子原语调用 {step.op!r}（CallOp 只留位，票 07 §2）"
            )

    def apply_hp_change(self, frame: Frame, seat: int, value: int) -> HpChangeDelta:
        """调 ``apply_hp`` 这个叶子原语并把返回的变化事件写进记录流（票 07 §1）。

        ``frame_id`` 即调用它的扣减 / 回复帧，故"这次体力变化属于谁"可由帧关联推出。
        """
        ctx = self._effect_context(frame, actor=seat)
        delta = apply_hp(self.state, ctx, seat, value)
        self.emit_delta(delta, frame_id=frame.frame_id, depth=self.stack.depth)
        return delta

    # -- 询问 ---------------------------------------------------------------

    def ask(self, build: Callable[[int], Query]) -> Action | _Suspend:
        """把一次询问交给参与者；已有应答则直接返回它。"""
        if self._answer is not None:
            answer, self._answer = self._answer, None
            return answer
        seq = self._next_seq()
        query = build(seq)
        self._emit(QueryRecord(seq=seq, query=query))
        self.pending = query
        return SUSPEND

    def submit(self, action: Action) -> None:
        """接受一个应答；合法性 = 成员判定，非法输入零状态改动。"""
        pending = self.pending
        if pending is None:
            raise RuntimeError("当前没有待应答的询问")
        if action.answers_seq != pending.seq:
            self._emit(
                ActionRecord(
                    seq=self._next_seq(), accepted=False, reason="answers_seq 陈旧", action=action
                )
            )
            raise IllegalActionError(
                reason_code="stale",
                message="应答的 answers_seq 与当前询问不符",
                expected=str(pending.seq),
                got=str(action.answers_seq),
                answers_seq=action.answers_seq,
            )
        if action not in pending.legal_actions:
            self._emit(
                ActionRecord(
                    seq=self._next_seq(),
                    accepted=False,
                    reason="不在候选集合内",
                    action=action,
                )
            )
            raise IllegalActionError(
                reason_code="illegal",
                message="行动不在候选集合内",
                expected="候选集合的成员之一",
                got=action.kind,
                answers_seq=action.answers_seq,
            )
        self._emit(ActionRecord(seq=self._next_seq(), accepted=True, action=action))
        self._answer = action
        self.pending = None

    def _build_query(
        self,
        kind: QueryKind,
        actor: int,
        timing: Timing,
        seq: int,
        *,
        dying: int | None = None,
    ) -> Query:
        actions = legal_actions_for(self.state, actor, kind, answers_seq=seq, dying=dying)
        view = project(self.state, actor)
        if kind is QueryKind.PLAY_PHASE:
            return PlayPhaseQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="出牌阶段：使用一张牌，或结束出牌阶段。",
                legal_actions=actions,
                view=view,
            )
        if kind is QueryKind.DISCARD:
            player = self.state.player(actor)
            count = discard_count(player)
            return DiscardQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt=f"弃牌阶段：请弃置 {count} 张手牌。",
                legal_actions=actions,
                view=view,
                count=count,
            )
        if kind is QueryKind.DODGE:
            return DodgeQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="以你为目标的【杀】生效前：是否使用【闪】？",
                legal_actions=actions,
                view=view,
            )
        if kind is QueryKind.PEACH:
            if dying is None:
                raise ValueError("PEACH 询问必须给出濒死者座位")
            return PeachQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt=f"座位 {dying} 处于濒死状态：是否对其使用【桃】？",
                legal_actions=actions,
                view=view,
                dying=dying,
            )
        raise ValueError(f"M1 还没有 {kind.value} 询问的构造器")

    # -- 终局 ---------------------------------------------------------------

    def check_game_end(self, *, draw_reason: str | None = None) -> Outcome | None:
        """唯一终局入口：死亡锚点与牌堆耗尽平局共用（票 13 §3 / 票 08 §6）。"""
        if draw_reason is not None:
            outcome = Outcome(result="draw", winner=None, reason=draw_reason)
        else:
            alive = [player.seat for player in self.state.players if player.alive]
            if len(alive) > 1:
                return None
            if len(alive) == 1:
                outcome = Outcome(result="winner", winner=alive[0], reason="last_surviving")
            else:
                outcome = Outcome(result="draw", winner=None, reason="no_survivor")
        self.pending_outcome = outcome
        # check_seq = **调用本入口时**最后一条已 emit 记录的序号（票 08 §6）。
        # 死亡路径上检测点与终止点之间还会 emit 死亡结算的记录，所以必须在此刻定格，
        # 不能在 finish() 时再取。
        self._check_seq = self._last_seq
        return outcome

    def finish(self) -> None:
        """终止点：清栈（逐帧记被丢弃的帧）→ 终局记录（流里最后一条）。"""
        if self.finished:
            return
        for frame in self.stack.clear():
            self._emit_frame("discard", frame)
        outcome = self.pending_outcome
        if outcome is None or self._check_seq is None:
            raise RuntimeError("没有终局判定就清栈")
        self._emit(
            GameEndRecord(
                seq=self._next_seq(),
                result=outcome.result,
                winner=outcome.winner,
                reason=outcome.reason,
                check_seq=self._check_seq,
            )
        )
        self.finished = True

    # -- 驱动 ---------------------------------------------------------------

    def run(self, participants: Sequence[Participant]) -> tuple[JournalRecord, ...]:
        """推进到终局；每次询问交给 ``participants[actor]`` 应答。"""
        while not self.finished:
            if self.pending is None:
                self._step()
                if self.death_entry is not None:
                    # 走到死亡入口即止（票 21）：死亡结算与清栈归票 22，届时这里
                    # 会换成"跑完死亡四段再走终局入口"，而不是直接跳出驱动循环。
                    break
                continue
            query = self.pending
            self.submit(participants[query.actor].respond(query))
        return self.records

    def _step(self) -> None:
        if self.finished or self.death_entry is not None:
            return
        if not self._started:
            self._start()
            return
        if self.stack:
            top = self.stack.top
            popped = self.stack.step(self.steps, self)
            if popped is not None:
                self._emit_frame("pop", popped)
            if (
                popped is not None
                and popped.kind is FrameKind.LIFE_DECREASE
                and popped.data.get("dying")
                and self.state.player(popped.data["target"]).hp <= 0
            ):
                # 停下：整条结算链止步于**死亡入口**。检测、记录与逐帧清栈归票 22；
                # 这里只把"谁死了"记下来，好让"到达入口"可被单独断言。
                self.death_entry = popped.data["target"]
            return
        self._step_turn()

    def _start(self) -> None:
        self.state.turn_seat = self.state.rule_config.first_seat
        self.emit_timing(Timing.GAME_START)
        self._started = True

    def _step_turn(self) -> None:
        step = self._turn_steps[self._turn_pc]
        if step() == "stay":
            return
        self._turn_pc += 1
        if self._turn_pc >= len(self._turn_steps):
            self._turn_pc = 0
            self._turn_data.clear()
            if self.state.turn_seat is not None and self.state.players:
                self.state.turn_seat = (self.state.turn_seat + 1) % len(self.state.players)

    # -- 回合环的每一步 -----------------------------------------------------

    def _turn_before_start(self) -> None:
        self.emit_timing(Timing.TURN_BEFORE_START, actor=self.state.turn_seat)

    def _turn_start(self) -> None:
        # 该座位自己的 TURN_START 是使用次数计数器的重置点（票 07 §6）。
        seat = self.state.turn_seat
        if seat is not None:
            self.state.player(seat).card_use_counts.clear()
        self.emit_timing(Timing.TURN_START, actor=seat)

    def _phase_prepare(self) -> None:
        # 准备阶段在本里程碑没有内部事件：只发阶段体（票 06 §7）。
        self.state.phase = Phase.PREPARE
        self.emit_timing(Timing.PHASE_PREPARE, actor=self.state.turn_seat)

    def _phase_judge(self) -> None:
        # 空阶段：判定区在 M1 不存在，流程经过但不 dispatch 任何子时机。
        self.state.phase = Phase.JUDGE
        self.emit_timing(Timing.PHASE_JUDGE, actor=self.state.turn_seat)

    def _phase_draw_start(self) -> None:
        self.state.phase = Phase.DRAW
        self.emit_timing(Timing.PHASE_DRAW_START, actor=self.state.turn_seat)

    def _phase_draw(self) -> None:
        seat = self._turn_seat()
        self.emit_timing(Timing.PHASE_DRAW, actor=seat)
        taken = draw_from_deck(self.state, DRAW_COUNT)
        if taken:
            delta = move_card(
                self.state,
                EffectContext(actor=seat),
                taken,
                to_zone=Zone.HAND,
                to_seat=seat,
                reason=MoveReason.DRAW,
            )
            self.emit_delta(delta)
        if len(taken) < DRAW_COUNT:
            # 短摸信号 → 唯一终局入口 → 检测点当步立即清栈（票 08 §6）。
            draw_reason = "both_decks_empty" if not self.state.discard else "deck_exhausted"
            self.check_game_end(draw_reason=draw_reason)
            self.finish()

    def _phase_draw_end(self) -> None:
        self.emit_timing(Timing.PHASE_DRAW_END, actor=self.state.turn_seat)

    def _phase_play_start(self) -> None:
        self.state.phase = Phase.PLAY
        self.emit_timing(Timing.PHASE_PLAY_START, actor=self.state.turn_seat)

    def _phase_play_idle(self) -> str | None:
        seat = self._turn_seat()
        if not self._turn_data.get("play_idle_emitted"):
            self.emit_timing(Timing.PHASE_PLAY_IDLE, actor=seat)
            self._turn_data["play_idle_emitted"] = True
        action = self.ask(
            lambda seq: self._build_query(QueryKind.PLAY_PHASE, seat, Timing.PHASE_PLAY_IDLE, seq)
        )
        if action is SUSPEND:
            return "stay"
        if action.kind == "EndPhase":
            return None
        # 使用一张牌：压入 USE 帧；子帧结算完再回到本步重新询问（票 07 §3）。
        self.start_use(action.card, source=seat, targets=action.targets)
        return "stay"

    def _phase_play_end(self) -> None:
        self.emit_timing(Timing.PHASE_PLAY_END, actor=self.state.turn_seat)

    def _phase_discard_start(self) -> None:
        self.state.phase = Phase.DISCARD
        self.emit_timing(Timing.PHASE_DISCARD_START, actor=self.state.turn_seat)

    def _phase_discard(self) -> str | None:
        seat = self._turn_seat()
        if not self._turn_data.get("discard_emitted"):
            self.emit_timing(Timing.PHASE_DISCARD, actor=seat)
            self._turn_data["discard_emitted"] = True
        player = self.state.player(seat)
        if discard_count(player) <= 0:
            return None
        action = self.ask(
            lambda seq: self._build_query(QueryKind.DISCARD, seat, Timing.PHASE_DISCARD, seq)
        )
        if action is SUSPEND:
            return "stay"
        delta = move_card(
            self.state,
            EffectContext(actor=seat),
            action.cards,
            to_zone=Zone.DISCARD,
            reason=MoveReason.DISCARD,
        )
        self.emit_delta(delta)
        return None

    def _phase_discard_end(self) -> None:
        self.emit_timing(Timing.PHASE_DISCARD_END, actor=self.state.turn_seat)

    def _phase_finish(self) -> None:
        self.state.phase = Phase.FINISH
        self.emit_timing(Timing.PHASE_FINISH, actor=self.state.turn_seat)

    def _turn_end(self) -> None:
        self.emit_timing(Timing.TURN_END, actor=self.state.turn_seat)

    def _turn_after_end(self) -> None:
        self.emit_timing(Timing.TURN_AFTER_END, actor=self.state.turn_seat)

    def _turn_seat(self) -> int:
        seat = self.state.turn_seat
        if seat is None:
            raise RuntimeError("回合环启动前没有当前回合角色")
        return seat


def instantiate_cards(pack: ContentPack) -> tuple[CardRef, ...]:
    """按数据表顺序把内容包实例化成牌实例：``card_id`` 即下标（票 08 §4）。

    牌实例的 id 在开局一次分配、随洗牌一起走；行为字段不在其中（归 ``effects``）。
    """
    names = {card_type.id: card_type.name for card_type in pack.card_types}
    return tuple(
        CardRef(
            card_id=index,
            type=deck_card.type,
            name=names[deck_card.type],
            suit=deck_card.suit.value,
            rank=deck_card.rank,
        )
        for index, deck_card in enumerate(pack.deck.cards)
    )


def build_initial(
    *, seed: int, rule_config: RuleConfig, pack: ContentPack
) -> tuple[GameState, tuple[JournalRecord, ...]]:
    """实例化牌堆 → 洗牌 → 发牌；顺带产出开局那几条记录。

    ``seq = 1`` 必是初次 ``deck_shuffle``（``frame_id = 0``：无帧哨兵），
    其后是发牌时机与按座序每座位一条 ``card_move``（票 08 §5 / 票 06 §7）。
    """
    cards = instantiate_cards(pack)
    players = tuple(
        Player(seat=seat, hp=INITIAL_HP, hp_max=INITIAL_HP)
        for seat in range(rule_config.player_count)
    )
    state = GameState(
        rule_config=rule_config,
        pack_id=pack.pack_id,
        deck_profile=pack.deck_profile,
        cards=cards,
        players=players,
        deck=list(range(len(cards))),
    )

    RandomSource(seed).substream("shuffle").shuffle(state.deck)

    records: list[JournalRecord] = [
        DeckShuffleRecord(
            seq=1,
            frame_id=0,
            depth=0,
            cards=state.refs(state.deck),
            from_zone=None,
            to_zone=Zone.DECK,
        ),
        TimingRecord(
            seq=2,
            frame_id=0,
            depth=0,
            timing=Timing.GAME_INITIAL_HAND_DEALING,
        ),
    ]
    ctx = EffectContext(actor=rule_config.first_seat)
    seq = 2
    for player in state.players:
        taken = draw_from_deck(state, rule_config.starting_hand_size)
        if not taken:
            continue
        delta = move_card(
            state,
            ctx,
            taken,
            to_zone=Zone.HAND,
            to_seat=player.seat,
            reason=MoveReason.DRAW,
        )
        seq += 1
        records.append(wrap_delta(delta, seq=seq, frame_id=0, depth=0))
    return state, tuple(records)


def new_game(*, seed: int, rule_config: RuleConfig, pack: ContentPack) -> GameState:
    """便利包装：造初始状态（实例化牌堆 → 洗牌 → 发牌），不返回记录流。

    要"跑一局并拿到记录流"用 :meth:`Engine.new_game`。
    """
    return build_initial(seed=seed, rule_config=rule_config, pack=pack)[0]
