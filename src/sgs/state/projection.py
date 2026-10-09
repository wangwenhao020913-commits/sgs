"""参与者视角投影：``project(state, seat) -> PlayerView``（票 05 §5）。

纯函数、每次构造询问时现算、不缓存；内部一律按座位序输出，不依赖任何无序容器的迭代。
"""

from __future__ import annotations

from .models import GameState
from .protocol import PlayerPublic, PlayerView

__all__ = ["project"]


def project(state: GameState, seat: int) -> PlayerView:
    """投影出 ``seat`` 这一参与者能看到的棋盘信息。"""
    players = tuple(
        PlayerPublic(
            seat=player.seat,
            hp=player.hp,
            hp_max=player.hp_max,
            hand_count=len(player.hand),
            alive=player.alive,
            equip=state.refs(player.equip),
            judge=state.refs(player.judge),
        )
        for player in state.players
    )
    self_hand = state.refs(state.player(seat).hand)
    return PlayerView(
        seat=seat,
        players=players,
        self_hand=self_hand,
        deck_count=len(state.deck),
        discard=state.refs(state.discard),
        processing=state.refs(state.processing),
        turn_seat=state.turn_seat,
        phase=state.phase,
    )
