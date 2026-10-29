"""参与者视角投影：``project(state, seat) -> PlayerView``（票 05 §5）。

纯函数、每次构造询问时现算、不缓存；内部一律按座位序输出，不依赖任何无序容器的迭代。
身份可见性也在这里落地（票 13 §6）：投影时就把不可见的身份抹成 ``None``，
使"身份不泄露"是结构事实而不是约定。
"""

from __future__ import annotations

from sgs.data import Identity

from .models import CardRef, GameState, Player
from .protocol import PlayerPublic, PlayerView

__all__ = ["project"]


def _visible_identity(player: Player, *, viewer: int) -> Identity | None:
    """``viewer`` 能看到 ``player`` 的身份吗（票 13 §6）。

    可见 ⇔ 本人 ∨ 主公（开局立即亮明）∨ **已亮明字段为真**（U1-04）∨ 已死亡
    （阵亡即亮明）；其余存活者不可见。

    主公与阵亡仍按既有口径派生（字段是 U1-04 新增的"亮明"入口，不替换它们）；字段让
    M5 的亮明效果只需写状态、不用改投影。
    """
    if (
        player.seat == viewer
        or player.identity is Identity.LORD
        or player.identity_visible
        or not player.alive
    ):
        return player.identity
    return None


def _hero_zone_facts(
    state: GameState, player: Player, *, with_faces: bool
) -> tuple[int, tuple[CardRef, ...]]:
    """一个座位武将牌上的 ``(公开张数, 本人牌面)``（票 27）。

    - **张数公开**：同一座位多个放置技能实例的区**求和**（跨座位的区算宿主）；
    - **牌面只给宿主**：与 M6 的 2026-10 口径一致
      （``tools/sgs_web/visibility.py::hero_zone_ref_visible``），未登记 / 畸形的区同一默认；
      他人视角只拿张数（``with_faces=False``，不构造 ``CardRef``）。
    - 区之间**没有规则先后**：这里按区键排序只为可重现，顺序不是契约（AI 只数张数）。
    """
    count = 0
    cards: list[CardRef] = []
    for skill_instance in sorted(player.hero_cards):
        zone = state.hero_zone(player.seat, skill_instance)
        count += len(zone)
        if with_faces:
            cards.extend(state.refs(zone))
    return count, tuple(cards)


def project(state: GameState, seat: int) -> PlayerView:
    """投影出 ``seat`` 这一参与者能看到的棋盘信息。

    武将 id 与势力是**公开信息**（标准版武将牌明置；`04`（§2「信息可见性」）），故逐座位照搬；
    ``identity`` 仍走 :func:`_visible_identity` 的过滤（票 02 §可见性 8：过滤逻辑一字不改）。

    武将牌上的牌（票 27）：逐座位给**张数**（公开，守恒量的一项）；牌面只给查看者自己当
    宿主的区（:func:`_hero_zone_facts`），与 M6 的 2026-10 口径一致。两者都 ``exclude``，
    不进记录流。
    """
    hero_zone_facts = {
        player.seat: _hero_zone_facts(
            state, player, with_faces=player.seat == seat
        )
        for player in state.players
    }
    players = tuple(
        PlayerPublic(
            seat=player.seat,
            hp=player.hp,
            hp_max=player.hp_max,
            hand_count=len(player.hand),
            alive=player.alive,
            equip=state.refs(player.equip),
            judge=state.refs(player.judge),
            hero_zone_count=hero_zone_facts[player.seat][0],
            chained=player.chained,
            flipped=player.flipped,
            wine_active=player.wine_active,
            identity=_visible_identity(player, viewer=seat),
            hero=player.hero,
            kingdom=player.kingdom,
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
        hero_zone_cards=hero_zone_facts[seat][1],
    )
