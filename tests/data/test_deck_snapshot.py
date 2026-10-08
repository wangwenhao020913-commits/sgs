"""53 张牌堆快照：这是"符合规则第十章"的**权威**检查。

``expect`` 只防"清单被改动而摘要忘了改"，不替代本快照；两层互为交叉验证——
单处录入错字过不了这里。字面量来自票 03 §3 的逐格核对结果，不从数据文件反推。
"""

from __future__ import annotations

from collections import Counter

from sgs.data import load_content_pack

EXPECTED_CARDS: tuple[tuple[str, str, int], ...] = (
    ("slash", "heart", 10),
    ("slash", "heart", 10),
    ("slash", "heart", 11),
    ("slash", "spade", 7),
    ("slash", "spade", 8),
    ("slash", "spade", 8),
    ("slash", "spade", 9),
    ("slash", "spade", 9),
    ("slash", "spade", 10),
    ("slash", "spade", 10),
    ("slash", "diamond", 6),
    ("slash", "diamond", 7),
    ("slash", "diamond", 8),
    ("slash", "diamond", 9),
    ("slash", "diamond", 10),
    ("slash", "diamond", 13),
    ("slash", "club", 2),
    ("slash", "club", 3),
    ("slash", "club", 4),
    ("slash", "club", 5),
    ("slash", "club", 6),
    ("slash", "club", 7),
    ("slash", "club", 8),
    ("slash", "club", 8),
    ("slash", "club", 9),
    ("slash", "club", 9),
    ("slash", "club", 10),
    ("slash", "club", 10),
    ("slash", "club", 11),
    ("slash", "club", 11),
    ("dodge", "heart", 2),
    ("dodge", "heart", 2),
    ("dodge", "heart", 13),
    ("dodge", "diamond", 2),
    ("dodge", "diamond", 2),
    ("dodge", "diamond", 3),
    ("dodge", "diamond", 4),
    ("dodge", "diamond", 5),
    ("dodge", "diamond", 6),
    ("dodge", "diamond", 7),
    ("dodge", "diamond", 8),
    ("dodge", "diamond", 9),
    ("dodge", "diamond", 10),
    ("dodge", "diamond", 11),
    ("dodge", "diamond", 11),
    ("peach", "heart", 3),
    ("peach", "heart", 4),
    ("peach", "heart", 6),
    ("peach", "heart", 7),
    ("peach", "heart", 8),
    ("peach", "heart", 9),
    ("peach", "heart", 12),
    ("peach", "diamond", 12),
)

RED_SUITS = frozenset({"heart", "diamond"})


def _actual() -> tuple[tuple[str, str, int], ...]:
    pack = load_content_pack()
    return tuple((card.type, card.suit.value, card.rank) for card in pack.deck.cards)


def test_deck_matches_the_53_card_snapshot():
    assert _actual() == EXPECTED_CARDS


def test_deck_counts_by_type():
    counts = Counter(card_type for card_type, _, _ in _actual())

    assert counts == {"slash": 30, "dodge": 15, "peach": 8}


def test_deck_counts_by_suit_and_color():
    cards = _actual()
    suits = Counter(suit for _, suit, _ in cards)
    color = Counter("red" if suit in RED_SUITS else "black" for _, suit, _ in cards)

    assert suits == {"heart": 13, "diamond": 19, "spade": 7, "club": 14}
    assert color == {"red": 32, "black": 21}


def test_ranks_run_from_two_to_thirteen():
    ranks = {rank for _, _, rank in _actual()}

    assert min(ranks) == 2
    assert max(ranks) == 13


def test_each_suit_rank_slot_holds_at_most_two_cards():
    slots = Counter((suit, rank) for _, suit, rank in _actual())

    assert len(slots) == 37
    assert sum(1 for count in slots.values() if count == 2) == 16
    assert max(slots.values()) == 2


def test_repeated_type_suit_rank_is_legal():
    """标准版每槽印两张，故"同型同花色同点数"可以有 11 组重复。"""
    triples = Counter(_actual())

    assert sum(1 for count in triples.values() if count == 2) == 11
