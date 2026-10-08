"""构造局面的入口：把「座位 / 体力 / 手牌 / 牌堆序 / 阶段」写成 ``GameState``。

纯函数、直接调用，不引 fixture 魔法（票 12 §6）。人为指定的局面**不要求** 53 张牌
全部就位：牌守恒是整局不变量（票 11 §4），由 fuzz 与基准断言，
构造局面单测只关心被测那条链。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sgs.data import ContentPack, RuleConfig, load_content_pack
from sgs.engine import Engine, instantiate_cards
from sgs.state import GameState, Phase, Player, Query, UseCard
from sgs.timing import Timing

__all__ = [
    "PeachInPlayPhasePlayer",
    "RecordingPlayer",
    "SeatSpec",
    "SequenceHalt",
    "build_state",
    "cards_of_type",
    "play_phase_peach_engine",
    "play_standard",
    "run_turns",
    "run_until",
    "turn_loop_engine",
]


@dataclass(frozen=True)
class SeatSpec:
    """一个座位的构造参数；``hand`` 是 ``card_id`` 元组。"""

    hp: int = 4
    hp_max: int = 4
    hand: tuple[int, ...] = ()
    alive: bool = True


def cards_of_type(pack: ContentPack, type_id: str) -> list[int]:
    """按数据表顺序给出某牌名的全部 ``card_id``。"""
    return [index for index, card in enumerate(pack.deck.cards) if card.type == type_id]


def build_state(
    *,
    pack: ContentPack,
    seats: list[SeatSpec],
    deck: tuple[int, ...] = (),
    discard: tuple[int, ...] = (),
    processing: tuple[int, ...] = (),
    turn_seat: int | None = None,
    phase=None,
    rule_config: RuleConfig | None = None,
) -> GameState:
    """把人为指定的局面写成 ``GameState``（``card_id`` 即数据表里的下标）。"""
    cards = instantiate_cards(pack)
    players = tuple(
        Player(
            seat=seat,
            hp=spec.hp,
            hp_max=spec.hp_max,
            alive=spec.alive,
            hand=list(spec.hand),
        )
        for seat, spec in enumerate(seats)
    )
    return GameState(
        rule_config=rule_config if rule_config is not None else RuleConfig(),
        pack_id=pack.pack_id,
        deck_profile=pack.deck_profile,
        cards=cards,
        players=players,
        deck=list(deck),
        discard=list(discard),
        processing=list(processing),
        turn_seat=turn_seat,
        phase=phase,
    )


def _end_phase_or_first(query: Query):
    """能结束阶段就结束阶段，否则取候选里的第一个（候选已按稳定键排序）。"""
    for candidate in query.legal_actions:
        if candidate.kind == "EndPhase":
            return candidate
    return query.legal_actions[0]


@dataclass
class RecordingPlayer:
    """确定性脚本玩家：录下每一次询问，并总是给同一个规则化的应答。

    默认策略：能结束阶段就结束阶段，否则取候选里的第一个（候选已按稳定键排序）。
    """

    seen: list[Query] = field(default_factory=list)

    def respond(self, action_query: Query):
        self.seen.append(action_query)
        return _end_phase_or_first(action_query)


def play_standard(seed: int = 7, *, pack: ContentPack | None = None):
    """用标准内容包跑完一整局（两个随机玩家），返回跑完的引擎（便于读记录流）。

    重洗落地后（票 23）牌堆会被弃牌堆无限补足，只有**进攻**才会收局；
    随机玩家会真的打死一方，故这一局仍必达 ``game_end``。
    需要"跑到第 N 个回合为止"的结构性单测请用 :func:`run_turns`。
    """
    import sgs.agents as agents
    from sgs.engine import RandomSource

    resolved_pack = pack if pack is not None else load_content_pack()
    rule_config = RuleConfig()
    engine = Engine.new_game(seed=seed, rule_config=rule_config, pack=resolved_pack)
    ai_stream = RandomSource(seed).substream("ai")
    participants = [agents.RandomPlayer(ai_stream) for _ in range(rule_config.player_count)]
    engine.run(participants)
    return engine


class SequenceHalt(Exception):
    """测试用的跳出信号：**重洗落地后，被动局面不再因牌堆耗尽自然终止**（票 23）。

    谁都不进攻时牌堆会被弃牌堆无限补足，故被动整局没有终局；需要"跑前几个回合"
    的结构性单测用 :func:`run_turns` 主动在回合边界跳出。
    """


class _TurnLimitedPlayer:
    """包装参与者：走完 ``turns`` 个回合后，在下一个询问处抛出 :class:`SequenceHalt`。"""

    def __init__(self, inner, engine, *, turns: int) -> None:
        self._inner = inner
        self._engine = engine
        self._turns = turns

    def respond(self, query: Query):
        if _completed_turns(self._engine) >= self._turns:
            raise SequenceHalt
        return self._inner.respond(query)


def _completed_turns(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
    )


class _StopWhenPlayer:
    """包装参与者：``stop(engine)`` 一成立，就在下一个询问处抛出 :class:`SequenceHalt`。"""

    def __init__(self, inner, engine, stop) -> None:
        self._inner = inner
        self._engine = engine
        self._stop = stop

    def respond(self, query: Query):
        if self._stop(self._engine):
            raise SequenceHalt
        return self._inner.respond(query)


def run_until(engine, participants, stop):
    """跑到 ``stop(engine)`` 成立即停（由参与者抛出 :class:`SequenceHalt` 中止）。"""
    wrapped = [_StopWhenPlayer(player, engine, stop) for player in participants]
    try:
        engine.run(wrapped)
    except SequenceHalt:
        pass
    return engine


def run_turns(engine, participants, *, turns: int = 1):
    """跑到 ``turns`` 个完整回合即停；不要求终局（票 23 的被动局面没有终局）。"""
    wrapped = [_TurnLimitedPlayer(player, engine, turns=turns) for player in participants]
    try:
        engine.run(wrapped)
    except SequenceHalt:
        pass
    return engine


# ---------------------------------------------------------------------------
# 时机覆盖清单的两个构造局面来源（票 25 / 票 12 §6）
# ---------------------------------------------------------------------------


class PeachInPlayPhasePlayer:
    """出牌阶段主动使用【桃】；其余询问走最小合法应答（结束阶段 / 放弃）。

    仅用于场景 7（:func:`play_phase_peach_engine`）：它是「出牌阶段主动使用【桃】回复 1 点
    体力」的驱动，故【桃】优先于一切、且只挑 ``type == "peach"`` 的 ``UseCard``。
    """

    def respond(self, query: Query):
        for candidate in query.legal_actions:
            if isinstance(candidate, UseCard) and candidate.card.type == "peach":
                return candidate
        return _end_phase_or_first(query)


def turn_loop_engine(seed: int = 1):
    """一个完整回合的引擎（人为局面从准备阶段起跑，票 12 §6 的构造局面单测 8）。

    ``RecordingPlayer`` 每步都结束阶段，故这一回合只经历阶段轮换与摸牌 / 弃牌，
    不碰使用结算。记录流是 L0 ``GAME_START`` / L1 / L2 可见节点的实测来源。
    """
    state = build_state(
        pack=load_content_pack(),
        seats=[SeatSpec(hp=4, hand=(40, 41, 42, 43)), SeatSpec(hp=4)],
        deck=tuple(range(10)),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=seed)
    run_turns(engine, [RecordingPlayer(), RecordingPlayer()], turns=1)
    return engine


def play_phase_peach_engine(seed: int = 1):
    """场景 7：座位 0 体力 3（未满）且手握一张【桃】，在**出牌阶段主动**使用它回复。

    回复走【桃】自己的使用链（L4 / L5）压出的 ``RECOVER`` 帧（L10），与濒死窗口里
    出【桃】（基准 03 / 04）互为独立来源。摸到的两张牌即使含【杀】也不使用：驱动玩家
    只看【桃】与结束阶段。
    """
    pack = load_content_pack()
    peach = cards_of_type(pack, "peach")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hp=3, hp_max=4, hand=(peach,)), SeatSpec(hp=4)],
        deck=tuple(card_id for card_id in range(len(pack.deck.cards)) if card_id != peach),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=seed)
    run_turns(engine, [PeachInPlayPhasePlayer(), PeachInPlayPhasePlayer()], turns=1)
    return engine
