"""候选行动的生成：**唯一真源**（票 07 §4）。

引擎在构造询问的那一刻调用 :func:`legal_actions_for`，把候选内联下发；
参与者的合法性 = 是否属于内联候选（成员判定）。因此不另写一份"校验器"——
候选与校验不可能漂移。

排序键由票 05 §4 定死：**牌实例 id 升序 → 同 id 再按行动类型名 → 无牌实例的排最后**，
规则层不得重排。

骨架期只有两类询问会真的被构造（``PLAY_PHASE`` / ``DISCARD``）；``DODGE`` 与
``PEACH`` 的候选随【杀】与濒死票落地。
"""

from __future__ import annotations

from itertools import combinations

from sgs.state import (
    Action,
    DiscardCards,
    EndPhase,
    GameState,
    QueryKind,
    UseCard,
)

__all__ = ["legal_actions_for"]


def legal_actions_for(
    state: GameState, seat: int, kind: QueryKind, *, answers_seq: int
) -> tuple[Action, ...]:
    """给出 ``seat`` 在 ``kind`` 询问下的全部候选，按票 05 的稳定键排序。"""
    if kind is QueryKind.PLAY_PHASE:
        candidates = _play_phase(state, seat, answers_seq)
    elif kind is QueryKind.DISCARD:
        candidates = _discard(state, seat, answers_seq)
    else:
        raise ValueError(f"骨架期还没有 {kind.value} 询问的候选生成器")
    return tuple(sorted(candidates, key=_sort_key))


def _play_phase(state: GameState, seat: int, answers_seq: int) -> list[Action]:
    # 杀 / 桃的候选随各自的使用流程落地（票 19 / 21）；骨架期出牌阶段只有"结束阶段"。
    del state, seat  # 骨架期用不到棋盘与座位；签名与其它候选生成器保持一致。
    return [EndPhase(answers_seq=answers_seq)]


def _discard(state: GameState, seat: int, answers_seq: int) -> list[Action]:
    player = state.player(seat)
    count = len(player.hand) - player.hp
    if count <= 0:
        return []
    return [
        DiscardCards(cards=state.refs(list(combo)), answers_seq=answers_seq)
        for combo in combinations(player.hand, count)
    ]


def _sort_key(action: Action) -> tuple[int, tuple[int, ...], str]:
    if isinstance(action, UseCard):
        return (0, (action.card.card_id,), action.kind)
    if isinstance(action, DiscardCards):
        return (0, tuple(card.card_id for card in action.cards), action.kind)
    return (1, (), action.kind)
