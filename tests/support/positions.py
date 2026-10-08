"""构造局面的入口：把「座位 / 体力 / 手牌 / 牌堆序 / 阶段」写成 ``GameState``。

纯函数、直接调用，不引 fixture 魔法（票 12 §6）。人为指定的局面**不要求** 53 张牌
全部就位：牌守恒是整局不变量（票 11 §4），由 fuzz 与基准断言，
构造局面单测只关心被测那条链。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sgs.data import ContentPack, RuleConfig
from sgs.engine import instantiate_cards
from sgs.state import GameState, Player, Query

__all__ = ["RecordingPlayer", "SeatSpec", "build_state", "cards_of_type", "play_standard"]


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


@dataclass
class RecordingPlayer:
    """确定性脚本玩家：录下每一次询问，并总是给同一个规则化的应答。

    默认策略：能结束阶段就结束阶段，否则取候选里的第一个（候选已按稳定键排序）。
    """

    seen: list[Query] = field(default_factory=list)

    def respond(self, action_query: Query):
        self.seen.append(action_query)
        for candidate in action_query.legal_actions:
            if candidate.kind == "EndPhase":
                return candidate
        return action_query.legal_actions[0]


def play_standard(seed: int = 7, *, pack: ContentPack | None = None):
    """用标准内容包 + 两个随机玩家跑完一整局，返回跑完的引擎（便于读记录流）。"""
    from sgs.agents import RandomPlayer
    from sgs.data import load_content_pack
    from sgs.engine import Engine, RandomSource

    resolved_pack = pack if pack is not None else load_content_pack()
    rule_config = RuleConfig()
    engine = Engine.new_game(seed=seed, rule_config=rule_config, pack=resolved_pack)
    ai_stream = RandomSource(seed).substream("ai")
    participants = [RandomPlayer(ai_stream) for _ in range(rule_config.player_count)]
    engine.run(participants)
    return engine
