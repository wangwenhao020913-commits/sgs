"""牌堆快照：这是"符合规则第十章"的**权威**检查。

``expect`` 只防"清单被改动而摘要忘了改"，不替代本快照；两层互为交叉验证——
单处录入错字过不了这里。字面量来自票 03 §3 与票 06 §1 的逐格核对结果，
不从数据文件反推。

两个档位各有一份逐张快照：

- ``basic_53`` 是 M1 的 53 张切片（杀 30 / 闪 15 / 桃 8），**显式选档**读取，
  使 M2 把默认档翻到 108 的改动不会泄漏进 M1 回归网（票 16 §9）；
- ``standard_108`` 是标准版全表（基本 53 + 锦囊 36 + 装备 19），含 4 张 EX 牌，
  故 ♠2 / ♣2 / ♥Q / ♦Q 四个槽位各有三张（票 06 §1 的 108 行逐张转录）。
"""

from __future__ import annotations

from collections import Counter

from sgs.data import load_content_pack

BASIC_53 = "basic_53"
STANDARD_108 = "standard_108"

_EQUIPMENT_IDS = (
    "crossbow",
    "qinggang_sword",
    "double_sword",
    "green_dragon_blade",
    "serpent_spear",
    "axe",
    "halberd",
    "qilin_bow",
    "ice_sword",
    "eight_diagrams",
    "renwang_shield",
    "chitu",
    "jueying",
    "dilu",
    "zhuahuang_feidian",
    "dawan",
    "zixing",
)
"""标准版 19 张装备的 17 个牌名（诸葛连弩 ×2、八卦阵 ×2 各多一张实体牌）。"""

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

EXPECTED_STANDARD_108: tuple[tuple[str, str, int], ...] = (
    ("peach_garden", "heart", 1),
    ("arrow_barrage", "heart", 1),
    ("dodge", "heart", 2),
    ("dodge", "heart", 2),
    ("peach", "heart", 3),
    ("harvest", "heart", 3),
    ("peach", "heart", 4),
    ("harvest", "heart", 4),
    ("qilin_bow", "heart", 5),
    ("chitu", "heart", 5),
    ("peach", "heart", 6),
    ("indulgence", "heart", 6),
    ("peach", "heart", 7),
    ("ex_nihilo", "heart", 7),
    ("peach", "heart", 8),
    ("ex_nihilo", "heart", 8),
    ("peach", "heart", 9),
    ("ex_nihilo", "heart", 9),
    ("slash", "heart", 10),
    ("slash", "heart", 10),
    ("slash", "heart", 11),
    ("ex_nihilo", "heart", 11),
    ("peach", "heart", 12),
    ("dismantlement", "heart", 12),
    ("lightning", "heart", 12),
    ("dodge", "heart", 13),
    ("zhuahuang_feidian", "heart", 13),
    ("duel", "spade", 1),
    ("lightning", "spade", 1),
    ("double_sword", "spade", 2),
    ("eight_diagrams", "spade", 2),
    ("ice_sword", "spade", 2),
    ("dismantlement", "spade", 3),
    ("snatch", "spade", 3),
    ("dismantlement", "spade", 4),
    ("snatch", "spade", 4),
    ("green_dragon_blade", "spade", 5),
    ("jueying", "spade", 5),
    ("indulgence", "spade", 6),
    ("qinggang_sword", "spade", 6),
    ("slash", "spade", 7),
    ("barbarian_invasion", "spade", 7),
    ("slash", "spade", 8),
    ("slash", "spade", 8),
    ("slash", "spade", 9),
    ("slash", "spade", 9),
    ("slash", "spade", 10),
    ("slash", "spade", 10),
    ("snatch", "spade", 11),
    ("nullify", "spade", 11),
    ("dismantlement", "spade", 12),
    ("serpent_spear", "spade", 12),
    ("barbarian_invasion", "spade", 13),
    ("dawan", "spade", 13),
    ("crossbow", "diamond", 1),
    ("duel", "diamond", 1),
    ("dodge", "diamond", 2),
    ("dodge", "diamond", 2),
    ("dodge", "diamond", 3),
    ("snatch", "diamond", 3),
    ("dodge", "diamond", 4),
    ("snatch", "diamond", 4),
    ("dodge", "diamond", 5),
    ("axe", "diamond", 5),
    ("slash", "diamond", 6),
    ("dodge", "diamond", 6),
    ("slash", "diamond", 7),
    ("dodge", "diamond", 7),
    ("slash", "diamond", 8),
    ("dodge", "diamond", 8),
    ("slash", "diamond", 9),
    ("dodge", "diamond", 9),
    ("slash", "diamond", 10),
    ("dodge", "diamond", 10),
    ("dodge", "diamond", 11),
    ("dodge", "diamond", 11),
    ("peach", "diamond", 12),
    ("halberd", "diamond", 12),
    ("nullify", "diamond", 12),
    ("slash", "diamond", 13),
    ("zixing", "diamond", 13),
    ("duel", "club", 1),
    ("crossbow", "club", 1),
    ("slash", "club", 2),
    ("eight_diagrams", "club", 2),
    ("renwang_shield", "club", 2),
    ("slash", "club", 3),
    ("dismantlement", "club", 3),
    ("slash", "club", 4),
    ("dismantlement", "club", 4),
    ("slash", "club", 5),
    ("dilu", "club", 5),
    ("slash", "club", 6),
    ("indulgence", "club", 6),
    ("slash", "club", 7),
    ("barbarian_invasion", "club", 7),
    ("slash", "club", 8),
    ("slash", "club", 8),
    ("slash", "club", 9),
    ("slash", "club", 9),
    ("slash", "club", 10),
    ("slash", "club", 10),
    ("slash", "club", 11),
    ("slash", "club", 11),
    ("borrowed_sword", "club", 12),
    ("nullify", "club", 12),
    ("borrowed_sword", "club", 13),
    ("nullify", "club", 13),
)

RED_SUITS = frozenset({"heart", "diamond"})


def _actual(profile: str = BASIC_53) -> tuple[tuple[str, str, int], ...]:
    pack = load_content_pack(deck_profile=profile)
    return tuple((card.type, card.suit.value, card.rank) for card in pack.deck.cards)


# ---------------------------------------------------------------------------
# 基本 53 张切片（M1 回归网）
# ---------------------------------------------------------------------------


def test_deck_matches_the_53_card_snapshot():
    assert _actual(BASIC_53) == EXPECTED_CARDS


def test_deck_counts_by_type():
    counts = Counter(card_type for card_type, _, _ in _actual(BASIC_53))

    assert counts == {"slash": 30, "dodge": 15, "peach": 8}


def test_deck_counts_by_suit_and_color():
    cards = _actual(BASIC_53)
    suits = Counter(suit for _, suit, _ in cards)
    color = Counter("red" if suit in RED_SUITS else "black" for _, suit, _ in cards)

    assert suits == {"heart": 13, "diamond": 19, "spade": 7, "club": 14}
    assert color == {"red": 32, "black": 21}


def test_ranks_run_from_two_to_thirteen():
    ranks = {rank for _, _, rank in _actual(BASIC_53)}

    assert min(ranks) == 2
    assert max(ranks) == 13


def test_each_suit_rank_slot_holds_at_most_two_cards():
    slots = Counter((suit, rank) for _, suit, rank in _actual(BASIC_53))

    assert len(slots) == 37
    assert sum(1 for count in slots.values() if count == 2) == 16
    assert max(slots.values()) == 2


def test_repeated_type_suit_rank_is_legal():
    """标准版每槽印两张，故"同型同花色同点数"可以有 11 组重复。"""
    triples = Counter(_actual(BASIC_53))

    assert sum(1 for count in triples.values() if count == 2) == 11


# ---------------------------------------------------------------------------
# 标准版 108 张全表（M2 默认档）
# ---------------------------------------------------------------------------


def test_the_108_deck_matches_the_snapshot():
    assert _actual(STANDARD_108) == EXPECTED_STANDARD_108


def test_the_108_deck_counts_by_category_and_type():
    cards = _actual(STANDARD_108)
    by_type = Counter(card_type for card_type, _, _ in cards)
    counts = Counter(card_type for card_type, _, _ in cards)

    assert len(cards) == 108
    assert sum(counts[card_type] for card_type in ("slash", "dodge", "peach")) == 53
    assert by_type["slash"] == 30 and by_type["dodge"] == 15 and by_type["peach"] == 8
    assert by_type["dismantlement"] == 6 and by_type["snatch"] == 5
    assert by_type["ex_nihilo"] == 4 and by_type["nullify"] == 4
    assert by_type["indulgence"] == 3 and by_type["duel"] == 3
    assert by_type["barbarian_invasion"] == 3 and by_type["harvest"] == 2
    assert by_type["borrowed_sword"] == 2 and by_type["lightning"] == 2
    assert by_type["peach_garden"] == 1 and by_type["arrow_barrage"] == 1
    assert sum(by_type[card_type] for card_type in _EQUIPMENT_IDS) == 19


def test_the_108_deck_holds_27_cards_per_suit():
    suits = Counter(suit for _, suit, _ in _actual(STANDARD_108))

    assert suits == {"heart": 27, "diamond": 27, "spade": 27, "club": 27}


def test_the_108_deck_has_four_three_card_slots_from_the_ex_cards():
    """含 EX 的 108 张在 ♠2 / ♣2 / ♥Q / ♦Q 四个槽位各有三张（票 06 §1）。"""
    slots = Counter((suit, rank) for _, suit, rank in _actual(STANDARD_108))
    triples = {slot for slot, count in slots.items() if count == 3}

    assert triples == {("spade", 2), ("club", 2), ("heart", 12), ("diamond", 12)}


def test_the_108_deck_has_ten_cards_at_rank_two_and_queen():
    ranks = Counter(rank for _, _, rank in _actual(STANDARD_108))

    assert ranks[2] == 10 and ranks[12] == 10
    assert all(ranks[rank] == 8 for rank in range(1, 14) if rank not in (2, 12))


def test_the_53_slice_is_exactly_the_basic_cards_of_the_108_deck():
    """基本 53 张是标准全表**按牌名过滤 basic** 的切片（票 06 §5 的复核）。

    两档的存储序不同（切片按牌名分组、全表按花色点数），故比的是**多重集**。
    """
    basic_names = {"slash", "dodge", "peach"}
    slice_of_108 = tuple(
        (card_type, suit, rank)
        for card_type, suit, rank in _actual(STANDARD_108)
        if card_type in basic_names
    )

    assert Counter(slice_of_108) == Counter(EXPECTED_CARDS)


# ---------------------------------------------------------------------------
# 牌面事实：攻击范围、延时标记（票 09 §6）
# ---------------------------------------------------------------------------


def _card_types_by_id():
    pack = load_content_pack(deck_profile=STANDARD_108)
    return {card_type.id: card_type for card_type in pack.card_types}


def test_weapons_declare_their_attack_range_from_the_first_hand_table():
    ranges = {
        card_type.id: card_type.attack_range
        for card_type in _card_types_by_id().values()
        if card_type.subcategory == "weapon"
    }

    assert ranges == {
        "crossbow": 1,
        "qinggang_sword": 2,
        "double_sword": 2,
        "green_dragon_blade": 3,
        "serpent_spear": 3,
        "axe": 3,
        "halberd": 4,
        "qilin_bow": 5,
        "ice_sword": 2,
    }


def test_only_weapons_carry_an_attack_range():
    offenders = {
        card_type.id: card_type.attack_range
        for card_type in _card_types_by_id().values()
        if card_type.attack_range is not None and card_type.subcategory != "weapon"
    }

    assert offenders == {}


def test_only_the_two_delayed_tricks_carry_the_delayed_marker():
    delayed = {card_type.id for card_type in _card_types_by_id().values() if card_type.delayed}

    assert delayed == {"indulgence", "lightning"}


def test_the_basic_53_card_types_are_untouched_by_the_new_face_facts():
    pack = load_content_pack()
    basics = {card_type.id: card_type for card_type in pack.card_types if card_type.category.value == "basic"}

    assert set(basics) == {"slash", "dodge", "peach"}
    for card_type in basics.values():
        assert card_type.attack_range is None
        assert card_type.delayed is False
        assert card_type.subcategory is None
