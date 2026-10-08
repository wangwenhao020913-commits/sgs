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
    EffectContext,
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
    FrameKind,
    GameState,
    MoveReason,
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
        self.steps: StepTable = {}
        """帧类型 → 步骤表；骨架期结算栈为空，步骤表随使用 / 伤害 / 濒死票登记。"""

        self.pending: Query | None = None
        self.pending_outcome: Outcome | None = None
        self.finished = False

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

    def _build_query(self, kind: QueryKind, actor: int, timing: Timing, seq: int) -> Query:
        actions = legal_actions_for(self.state, actor, kind, answers_seq=seq)
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
        raise ValueError(f"骨架期还没有 {kind.value} 询问的构造器")

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
                continue
            query = self.pending
            self.submit(participants[query.actor].respond(query))
        return self.records

    def _step(self) -> None:
        if self.finished:
            return
        if not self._started:
            self._start()
            return
        if self.stack:
            popped = self.stack.step(self.steps, self)
            if popped is not None:
                self._emit_frame("pop", popped)
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
        self.emit_timing(Timing.TURN_START, actor=self.state.turn_seat)

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
        # 使用牌的候选随票 19 落地；骨架期出牌阶段只有"结束阶段"。
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
