#!/usr/bin/env python3
"""按起始手牌扫种子，给**实机验收**找可复现的局面（W5-03 retro）。

开局页的人数只有 4–8，而借刀的 ``33 / 45 / 46`` 金录像是 2 人脚本局；实机验收想凑
「人类座位有某张牌、另一座位有武器 / 某张牌」时，手翻种子很慢。本工具只读引擎初始状态：

    .venv/bin/python tools/acceptance_seed.py --players 4 --card borrowed_sword \
        --other-slot weapon --limit 5000 --show 5
    .venv/bin/python tools/acceptance_seed.py --players 4 --card slash \
        --other-card slash --limit 5000 --show 5

输出每行一个种子：人类座位的起始手牌 + 满足附加条件的其他座位手牌。只扫**起始手牌**，
不模拟出牌；装备 / 摸牌仍取决于实际对局。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import new_game
from sgs.state import SlotKind

__all__ = ["Match", "find_seeds", "main"]


@dataclass(frozen=True)
class Match:
    """一个命中的种子：人类座位手牌 + 满足附加条件的其他座位。"""

    seed: int
    hand: tuple[str, ...]
    others: tuple[tuple[int, tuple[str, ...]], ...]


def _slot(value: str) -> SlotKind:
    try:
        return SlotKind(value)
    except ValueError as exc:
        allowed = ", ".join(kind.value for kind in SlotKind)
        raise argparse.ArgumentTypeError(f"未知槽位 {value!r}；可选：{allowed}") from exc


def find_seeds(
    *,
    players: int,
    human_seat: int,
    card: str,
    other_card: str | None = None,
    other_slot: SlotKind | None = None,
    limit: int = 5000,
    show: int = 5,
) -> list[Match]:
    """扫 ``0..limit-1``，返回人类手牌含 ``card`` 的前 ``show`` 个种子。

    ``other_card`` / ``other_slot`` 是**可选**的附加条件：只保留「至少一个其他座位
    手牌里有该牌 / 该槽位牌」的种子。两个条件同时给时按 AND 过滤。
    """
    if players < 2 or not 0 <= human_seat < players:
        raise ValueError("players 必须 ≥ 2，human_seat 必须落在座位范围内")
    if limit < 1 or show < 1:
        raise ValueError("limit / show 必须 ≥ 1")
    pack = load_content_pack(deck_profile="standard_108")
    rule = RuleConfig(player_count=players)
    need_other = other_card is not None or other_slot is not None
    matches: list[Match] = []
    for seed in range(limit):
        state = new_game(seed=seed, rule_config=rule, pack=pack, hero_pool=())
        hand = tuple(state.cards[cid].type for cid in state.players[human_seat].hand)
        if card not in hand:
            continue
        others: list[tuple[int, tuple[str, ...]]] = []
        for seat, player in enumerate(state.players):
            if seat == human_seat:
                continue
            types = tuple(state.cards[cid].type for cid in player.hand)
            if other_card is not None and other_card not in types:
                continue
            if other_slot is not None and not any(
                state.cards[cid].slot == other_slot for cid in player.hand
            ):
                continue
            if need_other:
                others.append((seat, types))
        if need_other and not others:
            continue
        matches.append(Match(seed=seed, hand=hand, others=tuple(others)))
        if len(matches) >= show:
            break
    return matches


def _format(matches: list[Match]) -> str:
    if not matches:
        return "no match"
    lines = ["seed\thuman hand\tother seats"]
    for match in matches:
        others = (
            ", ".join(f"seat{seat}: {'/'.join(types)}" for seat, types in match.others)
            or "-"
        )
        lines.append(f"{match.seed}\t{','.join(match.hand)}\t{others}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="按起始手牌扫种子，给实机验收找可复现局面（只读初始状态）。"
    )
    parser.add_argument("--players", type=int, default=4, help="人数（开局页支持 4–8）")
    parser.add_argument("--human-seat", type=int, default=0, help="人类座位，缺省 0")
    parser.add_argument("--card", required=True, help="人类起始手牌里必须有的 card.type")
    parser.add_argument("--other-card", default=None, help="另一座位手牌里必须有的 card.type")
    parser.add_argument("--other-slot", type=_slot, default=None, help="另一座位手牌里的槽位")
    parser.add_argument("--limit", type=int, default=5000, help="扫描的种子数，缺省 5000")
    parser.add_argument("--show", type=int, default=5, help="最多输出几个命中，缺省 5")
    args = parser.parse_args(argv)
    try:
        matches = find_seeds(
            players=args.players,
            human_seat=args.human_seat,
            card=args.card,
            other_card=args.other_card,
            other_slot=args.other_slot,
            limit=args.limit,
            show=args.show,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(_format(matches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
